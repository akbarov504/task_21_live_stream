import os

LOCAL_SERVICE_URL = os.getenv("LOCAL_SERVICE_URL", "http://127.0.0.1:8787")
TOKEN_URL = os.getenv("TOKEN_URL", f"{LOCAL_SERVICE_URL}/token")
INFO_URL = os.getenv("INFO_URL", f"{LOCAL_SERVICE_URL}/info")

WS_BASE = os.getenv("WS_BASE", "wss://dev-gw.tracksafe365.com/services/glsstream/stream")
DISABLE_SSL_VERIFY = os.getenv("DISABLE_SSL_VERIFY", "1") == "1"
SEND_DEST = os.getenv("SEND_DEST", "/app/send/signal")
SIGNAL_TOPIC_BASE = os.getenv("SIGNAL_TOPIC_BASE", "/topic/video/stream/signal")

QUALITY_PROFILES = {
    "480p": {"width": 640, "height": 480, "fps": 25},
    "720p": {"width": 1280, "height": 720, "fps": 30},
    "1080p": {"width": 1920, "height": 1080, "fps": 30},
    "2k": {"width": 2560, "height": 1440, "fps": 30},
}
DEFAULT_QUALITY = os.getenv("DEFAULT_QUALITY", "720p").lower()
if DEFAULT_QUALITY not in QUALITY_PROFILES:
    DEFAULT_QUALITY = "720p"

OUT_VIDEO_DEVICE = os.getenv("OUT_VIDEO_DEVICE", "/dev/video42")
OUT_AUDIO_DEVICE = os.getenv("OUT_AUDIO_DEVICE", "hw:Camera,0")

IN_VIDEO_DEVICE = os.getenv("IN_VIDEO_DEVICE", "/dev/video43")
IN_AUDIO_DEVICE = os.getenv("IN_AUDIO_DEVICE", "hw:Camera_1,0")

CAMERA_IN_DEVICE = IN_VIDEO_DEVICE
CAMERA_OUT_DEVICE = OUT_VIDEO_DEVICE

DEFAULT_CAMERA_MODE = os.getenv("DEFAULT_CAMERA_MODE", "all").lower()

ENABLE_AUDIO = os.getenv("ENABLE_AUDIO", "1") == "1"
AUDIO_RATE = int(os.getenv("AUDIO_RATE", "48000"))

ICE_SERVERS = os.getenv("ICE_SERVERS", '["stun:stun.l.google.com:19302"]')
ICE_USERNAME = os.getenv("ICE_USERNAME", "")
ICE_CREDENTIAL = os.getenv("ICE_CREDENTIAL", "")

PING_INTERVAL = int(os.getenv("PING_INTERVAL", "25"))
PING_TIMEOUT = int(os.getenv("PING_TIMEOUT", "10"))
RECONNECT = os.getenv("RECONNECT", "1") == "1"
