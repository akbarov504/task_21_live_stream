from typing import Callable, Optional
import json, ssl, random, string, websocket

NULL = "\x00"

def gen_server_id() -> str:
    return f"{random.randint(0, 999):03d}"

def gen_session_id(n: int = 8) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))

def build_sockjs_ws_url(ws_base: str) -> tuple[str, str]:
    server_id = gen_server_id()
    session_id = gen_session_id(8)
    url = f"{ws_base}/{server_id}/{session_id}/websocket"
    return url, session_id

def build_frame(command: str, headers: dict | None = None, body: str = "") -> str:
    headers = headers or {}
    lines = [command]
    for k, v in headers.items():
        lines.append(f"{k}:{v}")
    lines.append("")
    return "\n".join(lines) + "\n" + (body or "") + NULL

def parse_frame(raw: str):
    raw = raw.lstrip("\n")
    if NULL in raw:
        raw = raw.split(NULL, 1)[0]
    head, _, body = raw.partition("\n\n")
    lines = head.split("\n")
    cmd = lines[0].strip() if lines else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return cmd, headers, body

def sockjs_send(ws: websocket.WebSocketApp, stomp_frame: str):
    ws.send(json.dumps([stomp_frame]))

class SockJSTompClient:
    def __init__(
        self,
        ws_url: str,
        token: str,
        host: str,
        disable_ssl_verify: bool,
        ping_interval: int,
        ping_timeout: int,
        on_connected: Callable[["SockJSTompClient"], None],
        on_message: Callable[[str, dict, str], None],
        on_error_frame: Callable[[dict, str], None],
    ):
        self.ws_url = ws_url
        self.token = token
        self.host = host
        self.disable_ssl_verify = disable_ssl_verify
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        self.on_connected_cb = on_connected
        self.on_message_cb = on_message
        self.on_error_frame_cb = on_error_frame

        self.ws: Optional[websocket.WebSocketApp] = None
        self.connected = False

    def connect(self):
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_ws_message,
            on_error=self._on_ws_error,
            on_close=self._on_ws_close,
        )
        sslopt = {"cert_reqs": ssl.CERT_NONE} if self.disable_ssl_verify else None
        self.ws.run_forever(
            sslopt=sslopt,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
        )

    def send_frame(self, frame: str):
        if not self.ws:
            return
        sockjs_send(self.ws, frame)

    def subscribe(self, destination: str, sub_id: str):
        f = build_frame("SUBSCRIBE", {"id": sub_id, "destination": destination, "ack": "auto"})
        self.send_frame(f)

    def send(self, destination: str, body: str, content_type: str = "application/json"):
        frame = build_frame(
            "SEND",
            {
                "destination": destination,
                "content-type": content_type,
                "content-length": str(len(body.encode("utf-8"))),
            },
            body=body,
        )
        self.send_frame(frame)

    def _on_open(self, ws):
        connect_frame = build_frame(
            "CONNECT",
            {
                "accept-version": "1.2",
                "host": self.host,
                "Authorization": f"Bearer {self.token}",
                "heart-beat": "10000,10000",
            },
        )
        sockjs_send(ws, connect_frame)
        print("[STOMP] WS OPEN -> STOMP CONNECT sent")

    def _on_ws_message(self, ws, message: str):
        if message == "o":
            return
        if message == "h":
            return
        if message.startswith("c"):
            print("[SockJS] CLOSE:", message)
            return

        if message.startswith("a"):
            try:
                frames = json.loads(message[1:])
            except Exception as e:
                print("[SockJS] Parse error:", e, message[:200])
                return

            for fr in frames:
                cmd, headers, body = parse_frame(fr)

                if cmd == "CONNECTED":
                    self.connected = True
                    print("[STOMP] CONNECTED ✅")
                    self.on_connected_cb(self)
                    continue

                if cmd == "ERROR":
                    print("[STOMP] ERROR FRAME:", headers, body[:300])
                    self.on_error_frame_cb(headers, body)
                    continue

                if cmd == "MESSAGE":
                    dest = headers.get("destination", "")
                    self.on_message_cb(dest, headers, body)
                    continue

                if cmd:
                    print("[STOMP] OTHER:", cmd, headers)

    def _on_ws_error(self, ws, error):
        print("[WS] ERROR:", error)

    def _on_ws_close(self, ws, code, msg):
        self.connected = False
        print(f"[WS] CLOSE: code={code}, msg={msg}")
