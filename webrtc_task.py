import numpy as np
from av import AudioFrame
from typing import Optional, List, Dict, Any
from aiortc import (
    MediaStreamTrack,
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCConfiguration,
    RTCIceServer,
)
from fractions import Fraction
import json, asyncio, sounddevice
from aiortc.sdp import candidate_from_sdp
from config import (
    ICE_SERVERS,
    ICE_USERNAME,
    ICE_CREDENTIAL,
    ENABLE_AUDIO,
    AUDIO_RATE,
    IN_AUDIO_DEVICE,
    OUT_AUDIO_DEVICE,
    DEFAULT_CAMERA_MODE,
    DEFAULT_QUALITY,
)
from camera_task import create_video_tracks

def resolve_audio_device(dev):
    if dev is None:
        return None
    try:
        return int(dev)
    except (ValueError, TypeError):
        pass

    dev_str = str(dev).strip()
    try:
        devices = sounddevice.query_devices()
        for idx, d in enumerate(devices):
            if d.get('max_input_channels', 0) > 0:
                name = d.get('name', '')
                if dev_str in name or (dev_str.startswith("hw:") and dev_str in name):
                    print(f"[MIC] Resolved '{dev_str}' to sounddevice index {idx} ({name})")
                    return idx
    except Exception as e:
        print(f"[MIC] Warning querying devices: {e}")

    return dev_str

def make_rtc_config() -> RTCConfiguration:
    servers_raw = json.loads(ICE_SERVERS)
    ice_servers = []
    for url in servers_raw:
        if ICE_USERNAME or ICE_CREDENTIAL:
            ice_servers.append(RTCIceServer(urls=[url], username=ICE_USERNAME, credential=ICE_CREDENTIAL))
        else:
            ice_servers.append(RTCIceServer(urls=[url]))
    return RTCConfiguration(iceServers=ice_servers)

def candidate_from_payload(cand_payload: dict) -> Optional[RTCIceCandidate]:
    if not cand_payload:
        return None
    cand_str  = cand_payload.get("candidate")
    sdp_mid   = cand_payload.get("sdpMid")
    sdp_mline = cand_payload.get("sdpMLineIndex")

    if cand_str:
        try:
            cand = candidate_from_sdp(cand_str)
            cand.sdpMid = str(sdp_mid) if sdp_mid is not None else "0"
            cand.sdpMLineIndex = int(sdp_mline) if sdp_mline is not None else 0
            return cand
        except Exception as e:
            print("[ICE] candidate_from_sdp error:", e)
            return None

    host = cand_payload.get("host") or cand_payload.get("ip")
    port = cand_payload.get("port")
    if host and port:
        try:
            fake = f"candidate:1 1 udp 2122260223 {host} {int(port)} typ host"
            cand = candidate_from_sdp(fake)
            cand.sdpMid = "0"
            cand.sdpMLineIndex = 0
            return cand
        except Exception as e:
            print("[ICE] fake candidate error:", e)
            return None
    return None

class MicrophoneTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self, loop: asyncio.AbstractEventLoop, rate: int = AUDIO_RATE, device = None):
        super().__init__()
        self.rate = rate
        self.blocksize = 960
        self._loop = loop
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=30)
        self._pts = 0
        self._stopped = False

        resolved_dev = resolve_audio_device(device)
        print(f"[MIC] Initializing MicrophoneTrack with device={device} (resolved={resolved_dev})...")

        try:
            device_info = sounddevice.query_devices(resolved_dev, 'input')
            max_ch = device_info.get('max_input_channels', 1)
        except Exception as e:
            print(f"[MIC] Query device warning ({e}), defaulting to mono on default mic")
            resolved_dev = None
            max_ch = 1

        self.hardware_channels = 2 if max_ch >= 2 else 1

        try:
            self.stream = sounddevice.InputStream(
                samplerate=self.rate,
                channels=self.hardware_channels,
                dtype="int16",
                blocksize=self.blocksize,
                latency="low",
                callback=self._sd_callback,
                device=resolved_dev,
            )
            self.stream.start()
            print(f"[MIC] MicrophoneTrack started ✅ (device={resolved_dev}, {self.hardware_channels} ch -> Mono Opus)")
        except Exception as e:
            print(f"[MIC] Error starting microphone stream: {e}")
            self.stream = None
            self._stopped = True

    def _sd_callback(self, indata, frames, time_info, status):
        if self._stopped:
            return
        try:
            mono_data = indata[:, 0].copy().reshape(1, -1)

            def _put():
                try:
                    self._queue.put_nowait(mono_data)
                except asyncio.QueueFull:
                    pass
            self._loop.call_soon_threadsafe(_put)
        except Exception:
            pass

    def stop_mic(self):
        self._stopped = True
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print("[MIC] stop error:", e)
            self.stream = None

    async def recv(self):
        if self._stopped or self.stream is None:
            silent_data = np.zeros((1, self.blocksize), dtype=np.int16)
            frame = AudioFrame.from_ndarray(silent_data, format="s16", layout="mono")
        else:
            data = await self._queue.get()
            frame = AudioFrame.from_ndarray(data, format="s16", layout="mono")

        frame.sample_rate = self.rate
        frame.pts = self._pts
        frame.time_base = Fraction(1, self.rate)
        self._pts += frame.samples
        return frame

class WebRTCStreamSession:
    def __init__(self, loop: asyncio.AbstractEventLoop, send_signal_cb):
        self.loop = loop
        self.send_signal_cb = send_signal_cb
        self.pc: Optional[RTCPeerConnection] = None
        self.video_tracks: List[MediaStreamTrack] = []
        self.mic_track: Optional[MicrophoneTrack] = None
        self.pending_ice = []
        self.remote_set = False
        self.meta: Optional[Dict[str, Any]] = None
        self._disconnect_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def _release_tracks(self):
        """Kamera va mikrofonni to'xtatadi. Kamera to'xtatish bloklovchi (sleep bor),
        shuning uchun uni executor threadida bajaramiz — event loop qotib qolmasin."""
        for vt in self.video_tracks:
            if hasattr(vt, "stop_camera"):
                await self.loop.run_in_executor(None, vt.stop_camera)
        self.video_tracks.clear()

        if self.mic_track:
            await self.loop.run_in_executor(None, self.mic_track.stop_mic)
            self.mic_track = None

    async def _reset_locked(self):
        """reset() ning ichki mantig'i. FAQAT self._lock allaqachon ushlangan joydan chaqiring."""
        print("[SESSION] Cleaning up session resources...")
        if self._disconnect_task and not self._disconnect_task.done():
            self._disconnect_task.cancel()
            self._disconnect_task = None

        await self._release_tracks()

        if self.pc:
            try:
                await self.pc.close()
            except Exception:
                pass
            self.pc = None

        self.remote_set = False
        self.pending_ice.clear()
        self.meta = None
        print("[SESSION] Reset complete ✅")

    async def reset(self):
        async with self._lock:
            await self._reset_locked()

    async def start_stream_and_offer(self, meta: dict, camera_mode: str = DEFAULT_CAMERA_MODE, quality: str = DEFAULT_QUALITY, audio_enabled: bool = ENABLE_AUDIO):
        async with self._lock:
            self.meta = meta
            await self._reset_locked()

            self.pc = RTCPeerConnection(make_rtc_config())

            self.video_tracks = create_video_tracks(camera_mode=camera_mode, quality=quality)
            for vt in self.video_tracks:
                self.pc.addTrack(vt)

            if audio_enabled:
                audio_dev = OUT_AUDIO_DEVICE if (camera_mode or "").lower() == "out" else IN_AUDIO_DEVICE
                self.mic_track = MicrophoneTrack(self.loop, device=audio_dev)
                self.pc.addTrack(self.mic_track)

            self._attach_pc_events()

            offer = await self.pc.createOffer()
            await self.pc.setLocalDescription(offer)
            print(f"[SESSION] Local SDP OFFER created. Gathering state: {self.pc.iceGatheringState}")

            offer_payload = {
                "type":          "OFFER",
                "sdp":           self.pc.localDescription.sdp,
                "fromUsername":  meta.get("fromUsername"),
                "fromSessionId": meta.get("fromSessionId"),
                "serialNumber":  meta.get("serialNumber"),
                "truckId":       meta.get("truckId"),
                "driverId":      meta.get("driverId"),
                "toUsername":    meta.get("toUsername"),
                "toSessionId":   meta.get("toSessionId"),
                "cameraMode":    camera_mode,
                "quality":       quality,
                "audio":         audio_enabled,
            }
            self.send_signal_cb(offer_payload)
            print(f"[SESSION] OFFER sent to {meta.get('toSessionId')} 📡")

            self._send_ice_from_sdp(self.pc.localDescription.sdp)

    def _attach_pc_events(self):
        @self.pc.on("connectionstatechange")
        async def on_state():
            if not self.pc:
                return
            state = self.pc.connectionState
            print(f"[SESSION] WebRTC connectionState = {state}")

            if state == "connected":
                if self._disconnect_task and not self._disconnect_task.done():
                    self._disconnect_task.cancel()
                    self._disconnect_task = None
                print("[SESSION] LIVE STREAM CONNECTED AND STREAMING 🎥 🔊")

            elif state == "disconnected":
                print("[SESSION] Disconnected — waiting 5s before resetting...")
                async def _delayed_reset():
                    try:
                        await asyncio.sleep(5)
                        if self.pc and self.pc.connectionState == "disconnected":
                            print("[SESSION] Still disconnected after 5s — resetting")
                            await self.reset()
                    except asyncio.CancelledError:
                        pass
                self._disconnect_task = self.loop.create_task(_delayed_reset())

            elif state in ("failed", "closed"):
                print(f"[SESSION] Connection {state} — cleaning up...")
                await self.reset()

        @self.pc.on("icegatheringstatechange")
        async def on_ice_gathering():
            if self.pc:
                print(f"[SESSION] ICE gathering state = {self.pc.iceGatheringState}")

        @self.pc.on("icecandidate")
        def on_icecandidate(candidate):
            if candidate and self.meta:
                cand_payload = {
                    "type":          "ICE_TO_WEB",
                    "fromUsername":  self.meta.get("fromUsername"),
                    "fromSessionId": self.meta.get("fromSessionId"),
                    "serialNumber":  self.meta.get("serialNumber"),
                    "truckId":       self.meta.get("truckId"),
                    "driverId":      self.meta.get("driverId"),
                    "toUsername":    self.meta.get("toUsername"),
                    "toSessionId":   self.meta.get("toSessionId"),
                    "candidate": {
                        "candidate":     candidate.candidate,
                        "sdpMid":        candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    },
                }
                self.send_signal_cb(cand_payload)
                print("[SESSION] ICE_TO_WEB sent via event")

    def _send_ice_from_sdp(self, sdp: str):
        if not sdp or not self.meta:
            return
        count = 0
        for line in sdp.splitlines():
            if line.startswith("a=candidate:"):
                cand_str = line[2:]
                payload = {
                    "type":          "ICE_TO_WEB",
                    "fromUsername":  self.meta.get("fromUsername"),
                    "fromSessionId": self.meta.get("fromSessionId"),
                    "serialNumber":  self.meta.get("serialNumber"),
                    "truckId":       self.meta.get("truckId"),
                    "driverId":      self.meta.get("driverId"),
                    "toUsername":    self.meta.get("toUsername"),
                    "toSessionId":   self.meta.get("toSessionId"),
                    "candidate": {
                        "candidate":     cand_str,
                        "sdpMid":        "0",
                        "sdpMLineIndex": 0,
                    },
                }
                self.send_signal_cb(payload)
                count += 1
        print(f"[SESSION] ICE_TO_WEB sent from SDP: {count} candidates")

    async def handle_answer(self, answer_sdp: str, meta: dict):
        if not self.pc:
            print("[SESSION] Error: Received ANSWER but PeerConnection is not initialized.")
            return

        print("[SESSION] Setting remote SDP ANSWER...")
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))
        self.remote_set = True
        print("[SESSION] Remote description set successfully ✅")

        for c in self.pending_ice:
            try:
                await self.pc.addIceCandidate(c)
                print("[SESSION] Queued ICE added ✅")
            except Exception as e:
                print("[SESSION] Queued ICE error:", e)
        self.pending_ice.clear()

    async def handle_ice_from_web(self, cand_payload: dict):
        cand = candidate_from_payload(cand_payload)
        if not cand:
            return

        if not self.pc or not self.remote_set:
            self.pending_ice.append(cand)
            print("[SESSION] Remote ICE queued (waiting for answer/pc)")
            return

        try:
            await self.pc.addIceCandidate(cand)
            print("[SESSION] Remote ICE candidate added ✅")
        except Exception as e:
            print("[SESSION] addIceCandidate error:", e)

    async def handle_offer(self, offer_sdp: str, meta: dict, camera_mode: str = DEFAULT_CAMERA_MODE, quality: str = DEFAULT_QUALITY, audio_enabled: bool = ENABLE_AUDIO):
        async with self._lock:
            self.meta = meta
            await self._reset_locked()

            self.pc = RTCPeerConnection(make_rtc_config())

            self.video_tracks = create_video_tracks(camera_mode=camera_mode, quality=quality)
            for vt in self.video_tracks:
                self.pc.addTrack(vt)

            if audio_enabled:
                audio_dev = OUT_AUDIO_DEVICE if (camera_mode or "").lower() == "out" else IN_AUDIO_DEVICE
                self.mic_track = MicrophoneTrack(self.loop, device=audio_dev)
                self.pc.addTrack(self.mic_track)

            self._attach_pc_events()

            await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
            self.remote_set = True

            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)

            answer_payload = {
                "type":          "ANSWER",
                "sdp":           self.pc.localDescription.sdp,
                "fromUsername":  meta.get("fromUsername"),
                "fromSessionId": meta.get("fromSessionId"),
                "serialNumber":  meta.get("serialNumber"),
                "truckId":       meta.get("truckId"),
                "driverId":      meta.get("driverId"),
                "toUsername":    meta.get("toUsername"),
                "toSessionId":   meta.get("toSessionId"),
            }
            self.send_signal_cb(answer_payload)
            print("[SESSION] Direct ANSWER sent to Web")
            self._send_ice_from_sdp(self.pc.localDescription.sdp)

class WebRTCManager:
    def __init__(self, loop: asyncio.AbstractEventLoop, send_signal_cb, local_serial: str):
        self.loop = loop
        self.send_signal_cb = send_signal_cb
        self.local_serial = local_serial
        self.sessions: Dict[str, WebRTCStreamSession] = {}

    def _get_session(self, key: str) -> WebRTCStreamSession:
        if key not in self.sessions:
            self.sessions[key] = WebRTCStreamSession(self.loop, self.send_signal_cb)
        return self.sessions[key]

    def handle_signal(self, signal: dict):
        if isinstance(signal, dict) and "data" in signal and isinstance(signal["data"], dict):
            signal = signal["data"]

        sig_type = signal.get("type", "").upper()
        from_sid = signal.get("fromSessionId") or "unknown_client"

        meta = {
            "fromUsername":  self.local_serial,
            "fromSessionId": signal.get("toSessionId"),
            "serialNumber":  self.local_serial,
            "truckId":       signal.get("truckId"),
            "driverId":      signal.get("driverId"),
            "toUsername":    signal.get("fromUsername"),
            "toSessionId":   from_sid,
        }

        camera_mode = signal.get("cameraMode") or signal.get("cameraType") or DEFAULT_CAMERA_MODE
        quality = signal.get("quality") or DEFAULT_QUALITY
        audio_req = signal.get("audio")
        audio_enabled = ENABLE_AUDIO if audio_req is None else bool(audio_req)

        sess = self._get_session(from_sid)

        print(f"[MANAGER] Signal received: type={sig_type}, fromSession={from_sid}, cameraMode={camera_mode}, quality={quality}, audio={audio_enabled}")

        if sig_type == "START_STREAM":
            asyncio.run_coroutine_threadsafe(
                sess.start_stream_and_offer(meta, camera_mode=camera_mode, quality=quality, audio_enabled=audio_enabled),
                self.loop
            )
            return

        if sig_type == "OFFER":
            offer_sdp = signal.get("sdp") or ""
            asyncio.run_coroutine_threadsafe(
                sess.handle_offer(offer_sdp, meta, camera_mode=camera_mode, quality=quality, audio_enabled=audio_enabled),
                self.loop
            )
            return

        if sig_type == "ANSWER":
            answer_sdp = signal.get("sdp") or ""
            asyncio.run_coroutine_threadsafe(
                sess.handle_answer(answer_sdp, meta),
                self.loop
            )
            return

        if sig_type == "ICE_TO_MINIPC":
            cand_payload = signal.get("candidate") or {}
            asyncio.run_coroutine_threadsafe(
                sess.handle_ice_from_web(cand_payload),
                self.loop
            )
            return

        if sig_type in ("STOP_STREAM", "HANGUP"):
            print(f"[MANAGER] Stopping stream for session {from_sid}")
            asyncio.run_coroutine_threadsafe(sess.reset(), self.loop)
            return

        if sig_type in ("ICE_TO_WEB",):
            return

        print(f"[MANAGER] Unhandled signal type: {sig_type}")
