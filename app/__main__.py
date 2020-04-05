import base64
import cattr
import datetime
import json
import logging
import os
import paho.mqtt.client as mqtt
import requests
import time

from typing import Any

from .model import Config, UserAccessToken

logging.basicConfig(level=logging.DEBUG)

CONFIG_FILE = f"{os.path.dirname(os.path.dirname(__file__))}/config.json"
CREDENTIALS_FILE = f"{os.path.dirname(os.path.dirname(__file__))}/.user_credentials.json"

def build_client_bearer(config: Config) -> str:
    return base64.b64encode(f"{config.client_id}:{config.client_secret}".encode("ascii")).decode("ascii")

def new_user_access_token(session: requests.Session, config: Config) -> UserAccessToken:
    client_bearer = build_client_bearer(config)
    result = session.post(
        "https://zoom.us/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": config.user_code,
            "redirect_uri": config.redirect_uri
        },
        headers={
            "Authorization": f"Basic {client_bearer}"
        }
    )
    
    logging.debug(result)
    logging.debug(result.text)
    result.raise_for_status()

    return cattr.structure(result.json(), UserAccessToken)

def refresh_user_access_token(session: requests.Session, config: Config, refresh_token: str) -> UserAccessToken:
    client_bearer = build_client_bearer(config)
    result = session.post(
        "https://zoom.us/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        },
        headers={
            "Authorization": f"Basic {client_bearer}"
        }
    )

    logging.debug(result)
    logging.debug(result.text)
    result.raise_for_status()

    return cattr.structure(result.json(), UserAccessToken)

def mqtt_publish(client: mqtt, topic: str, payload: Any):
    result = client.publish(topic, payload, retain=True)
    logging.debug("Send %s to MQTT topic %s. Result: %s", payload, topic, result)
    result.wait_for_publish()
    logging.debug("Send confirmed.")


with open(CONFIG_FILE, "r") as cfg_fh:
    config = cattr.structure(json.load(cfg_fh), Config)

r_session = requests.Session()
mqtt_client = mqtt.Client()
mqtt_client.connect(config.mqtt_host, config.mqtt_port, config.mqtt_timeout)

uat = None
if os.path.isfile(CREDENTIALS_FILE):
    try:
        with open(CREDENTIALS_FILE, "r") as cfh:
            uat = cattr.structure(json.load(cfh), UserAccessToken)
    except:
        pass
if not uat:
    uat = new_user_access_token(r_session, config)
    with open(CREDENTIALS_FILE, "w") as cfh:
        json.dump(cattr.unstructure(uat), cfh)

known_state = None
while True:
    if time.time() >= uat.refresh_at_ts:
        uat = refresh_user_access_token(r_session, config, uat.refresh_token)
        with open(CREDENTIALS_FILE, "w") as cfh:
            json.dump(cattr.unstructure(uat), cfh)

    try:
        data = r_session.get(
            f"https://api.zoom.us/v2/chat/users/me/contacts/{config.user_email}?query_presence_status=true",
            headers={
                "Authorization": f"Bearer {uat.access_token}"
            }
        ).json()
    except Exception as e:
        logging.exception("Something sad face happened: %s", e)

    logging.debug(data)

    if data["presence_status"] != known_state:
        try:
            if data["presence_status"] == "Do_Not_Disturb":
                logging.info("Entering meeting.")
                mqtt_publish(mqtt_client, config.mqtt_publish_to, config.mqtt_message_enter)
            else:
                logging.info("Leaving meeting.")
                mqtt_publish(mqtt_client, config.mqtt_publish_to, config.mqtt_message_leave)
            known_state = data["presence_status"]
            logging.debug("Known state is now %s", known_state)
        except Exception as e:
            logging.exception("Something differently sad face happened: %s", e)

    time.sleep(1)
