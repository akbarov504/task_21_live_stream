from pathlib import Path
import json, time, asyncio, threading
import urllib3
urllib3.disable_warnings()

from config import (
    WS_BASE,
    DISABLE_SSL_VERIFY,
    SEND_DEST,
    SIGNAL_TOPIC_BASE,
    PING_INTERVAL,
    PING_TIMEOUT,
    RECONNECT,
    DEFAULT_CAMERA_MODE,
    DEFAULT_QUALITY,
    ENABLE_AUDIO,
)
from auth_api import get_token, get_info, get_serial_number
from stomp_sockjs import SockJSTompClient, build_sockjs_ws_url
from webrtc_task import WebRTCManager

BASE_DIR = Path(__file__).resolve().parent

class LiveStreamApp:
    def __init__(self, token: str, serial_number: str, truck_id: int, ws_url: str, ws_session_id: str):
        self.token = token
        self.serial_number = serial_number
        self.truck_id = truck_id
        self.ws_url = ws_url
        self.ws_session_id = ws_session_id

        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()

        self.client: SockJSTompClient | None = None
        self.webrtc = WebRTCManager(self.loop, self._send_signal_payload, local_serial=self.serial_number)

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _send_signal_payload(self, payload: dict):
        if not self.client:
            return

        to_user = payload.get("toUsername") or "admin"
        to_sid = payload.get("toSessionId") or ""
        sig_type = payload.get("type")

        wrapped_body = json.dumps({"data": payload}, ensure_ascii=False)
        self.client.send(SEND_DEST, body=wrapped_body, content_type="application/json")

        if to_sid:
            t1 = f"{SIGNAL_TOPIC_BASE}/{to_user}/{to_sid}"
            t2 = f"{SIGNAL_TOPIC_BASE}/{self.serial_number}/{to_sid}"
            t3 = f"/topic/signal/{to_user}/{to_sid}"
            t4 = f"/topic/signal/{self.serial_number}/{to_sid}"

            self.client.send(t1, body=wrapped_body, content_type="application/json")
            self.client.send(t2, body=wrapped_body, content_type="application/json")
            self.client.send(t3, body=wrapped_body, content_type="application/json")
            self.client.send(t4, body=wrapped_body, content_type="application/json")

        print(f"[SIGNAL OUT] Type: {sig_type} -> Sent to {to_sid} (user: {to_user}) via STOMP 📡")

    def on_connected(self, cli: SockJSTompClient):
        sig_dest = f"{SIGNAL_TOPIC_BASE}/{self.serial_number}/{self.ws_session_id}"
        cli.subscribe(sig_dest, "sub-video-signal")
        print(f"[STOMP] SUBSCRIBED to video signal topic: {sig_dest}")

        err_dest = f"/topic/error/{self.serial_number}/{self.ws_session_id}"
        cli.subscribe(err_dest, "sub-video-err")
        print(f"[STOMP] SUBSCRIBED to error topic: {err_dest}")

        print("\n" + "=" * 65)
        print(">>> 🎥 LIVE STREAMING SERVICE IS RUNNING & READY")
        print(f"    Serial Number    : {self.serial_number}")
        print(f"    Truck ID         : {self.truck_id}")
        print(f"    MiniPC Session ID: {self.ws_session_id}")
        print(f"    Camera Mode      : {DEFAULT_CAMERA_MODE}")
        print(f"    Default Quality  : {DEFAULT_QUALITY}")
        print(f"    Audio Enabled    : {ENABLE_AUDIO}")
        print(f"    Signal Topic     : {sig_dest}")
        print("=" * 65)
        print(">>> Brauzer orqali 'web_test.html' dan ulanib test qilishingiz mumkin.\n")

    def on_message(self, dest: str, headers: dict, body: str):
        print(f"\n[STOMP MSG] Destination: {dest}")

        if "/error/" in dest:
            print(f"[ERROR TOPIC]: {body[:300]}")
            return

        if SIGNAL_TOPIC_BASE in dest or "/topic/signal/" in dest:
            try:
                raw = json.loads(body)
            except Exception as e:
                print(f"[SIGNAL] JSON parse error: {e}")
                return

            if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], dict):
                signal = raw["data"]
            else:
                signal = raw

            sig_type = signal.get("type")
            print(f"[SIGNAL IN] Type: {sig_type} | From: {signal.get('fromSessionId')} -> To: {signal.get('toSessionId')}")

            if not sig_type:
                print(f"[SIGNAL] Missing type in payload: {signal}")
                return

            self.webrtc.handle_signal(signal)
            return

        print(f"[MSG OTHER]: {dest} | {body[:200]}")

    def on_error_frame(self, headers: dict, body: str):
        print(f"[STOMP ERROR FRAME]: {headers} {body[:300]}")

    def run_once(self):
        self.token = get_token()

        self.client = SockJSTompClient(
            ws_url=self.ws_url,
            token=self.token,
            host="dev-gw.tracksafe365.com",
            disable_ssl_verify=DISABLE_SSL_VERIFY,
            ping_interval=PING_INTERVAL,
            ping_timeout=PING_TIMEOUT,
            on_connected=self.on_connected,
            on_message=self.on_message,
            on_error_frame=self.on_error_frame,
        )
        self.client.connect()

def main():
    print("[INIT] Starting Live Streaming Service...")
    token = get_token()
    info = get_info()
    serial_number = str(info.get("serialNumber", "unknown"))
    truck_id = int(info.get("truckId", 0))

    while True:
        ws_url, ws_session_id = build_sockjs_ws_url(WS_BASE)
        print(f"\n[WS SETUP] URL: {ws_url}")
        print(f"[WS SETUP] Session: {ws_session_id}")
        print(f"[WS SETUP] Serial Number: {serial_number}")

        app = LiveStreamApp(
            token=token,
            serial_number=serial_number,
            truck_id=truck_id,
            ws_url=ws_url,
            ws_session_id=ws_session_id
        )
        app.run_once()

        if not RECONNECT:
            break

        print("[RECONNECT] Connection closed. Reconnecting in 3s...")
        time.sleep(3)

if __name__ == "__main__":
    main()
