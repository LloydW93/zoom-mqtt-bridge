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

from .model import Config, KnownState, UserAccessToken

logging.basicConfig(level=logging.DEBUG)

CONFIG_FILE = f"{os.path.dirname(os.path.dirname(__file__))}/config.json"
CREDENTIALS_FILE = f"{os.path.dirname(os.path.dirname(__file__))}/.user_credentials.json"

STATE_MAP = {
    "Do_Not_Disturb": KnownState.ON_CALL
}

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

def mqtt_publish(client: mqtt, qos: bool, topic: str, payload: Any, is_retry: bool=False) -> bool:
    result = client.publish(topic, payload, retain=True, qos=qos)
    logging.debug("Send %s to MQTT topic %s. Result: %s", payload, topic, result)
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        logging.warning("Send failed, attempting reconnection to broker.")
        client.reconnect()
        if not is_retry:
            return mqtt_publish(client, topic, payload, is_retry=True)
        else:
            return False
    else:
        return True

def sync_presence_status(client: mqtt, config: Config, presence_status: KnownState) -> bool:
    publish_result = False
    try:
        if presence_status == KnownState.ON_CALL:
            logging.info("Entering meeting.")
            publish_result = mqtt_publish(mqtt_client, config.qos, config.mqtt_publish_to, config.mqtt_message_enter)
        else:
            logging.info("Leaving meeting.")
            publish_result = mqtt_publish(mqtt_client, config.qos, config.mqtt_publish_to, config.mqtt_message_leave)
    except Exception as e:
        logging.exception("Something differently sad face happened: %s", e)
    
    return publish_result


with open(CONFIG_FILE, "r") as cfg_fh:
    config = cattr.structure(json.load(cfg_fh), Config)

r_session = requests.Session()
mqtt_client = mqtt.Client(
    client_id=f"zoom-mqtt-bridge-{config.user_email}-{config.mqtt_publish_to}"
)

mqtt_client.on_message = lambda x: logging.error(f"I received something? {x}")
mqtt_client.enable_logger()
mqtt_client.connect(config.mqtt_host, config.mqtt_port, config.mqtt_timeout)

mqtt_loop = mqtt_client.loop_start()
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

known_state: KnownState = KnownState.UNKNOWN
resync_time = 0
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

    target_state = STATE_MAP.get(data["presence_status"], KnownState.OFF_CALL)

    if target_state != known_state:
        publish_result = sync_presence_status(mqtt_client, config, target_state)
        if publish_result:
            known_state = target_state
            resync_time = time.time()
            logging.debug("Known state is now %s", known_state)
        else:
            logging.debug("Not updating known state as publish failed") 
    elif config.resync_interval and time.time() >= resync_time + config.resync_interval:
        sync_presence_status(mqtt_client, config, known_state)
        logging.debug("Resynced status of %s", known_state)
        resync_time = time.time()

    time.sleep(1)
