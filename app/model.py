import attr
from enum import Enum
import math
import time
from typing import Optional


@attr.s(auto_attribs=True)
class Config:
    client_id: str
    client_secret: str
    redirect_uri: str
    mqtt_host: str
    user_email: str
    mqtt_publish_to: str
    mqtt_message_enter: str
    mqtt_message_leave: str
    user_code: Optional[str] = None
    mqtt_port: Optional[int] = 1883
    mqtt_timeout: Optional[int] = 60
    # 0 = No resync
    resync_interval: Optional[int] = 0
    qos: Optional[int] = 1


@attr.s(auto_attribs=True)
class UserAccessToken:
    access_token: str
    token_type: str
    refresh_token: str
    expires_in: int
    scope: str
    refresh_at_ts: Optional[int] = None

    def __attrs_post_init__(self):
        if not self.refresh_at_ts:
            self.refresh_at_ts = math.floor(time.time() + self.expires_in - 300)

class KnownState(Enum):
    UNKNOWN = 0
    ON_CALL = 1
    OFF_CALL = 2
