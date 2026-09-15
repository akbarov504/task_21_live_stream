import asyncio
import cv2
import numpy as np
import time
import threading
from fractions import Fraction
from typing import Optional, Tuple
from av import VideoFrame
from aiortc import MediaStreamTrack
from config import (
    QUALITY_PROFILES,
    DEFAULT_QUALITY,
    CAMERA_IN_DEVICE,
    CAMERA_OUT_DEVICE,
)

def parse_device_source(device_str: str):
    try:
        return int(device_str)
    except (ValueError, TypeError):
        return device_str

class CameraCaptureThread:
    def __init__(self, source, width: int, height: int, fps: int, name: str = "Camera"):
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps
        self.name = name

        self.cap: Optional[cv2.VideoCapture] = None
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.latest_frame: Optional[np.ndarray] = None
        self.is_hardware_available = False
        self._start_capture()

    def _start_capture(self):
        src = parse_device_source(self.source)
        print(f"[CAMERA] Opening {self.name} (source={src}, target={self.width}x{self.height}@{self.fps}fps)...")
        try:
            if isinstance(src, int):
                import platform
                if platform.system() == "Linux":
                    self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
                else:
                    self.cap = cv2.VideoCapture(src)
            else:
                self.cap = cv2.VideoCapture(str(src))

            if self.cap and self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                self.cap.set(cv2.CAP_PROP_FPS, self.fps)
                self.is_hardware_available = True
                print(f"[CAMERA] {self.name} opened successfully ✅")
            else:
                print(f"[CAMERA] {self.name} device {src} not found/accessible. Using synthetic stream ⚠️")
                self.is_hardware_available = False
        except Exception as e:
            print(f"[CAMERA] {self.name} open exception: {e}. Using synthetic stream ⚠️")
            self.is_hardware_available = False

        self.running = True
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

    def _generate_synthetic_frame(self, frame_idx: int) -> np.ndarray:
        img = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        t = time.time()
        c_r = int((np.sin(t) + 1) * 30)
        c_b = int((np.cos(t) + 1) * 40) + 30
        img[:] = (c_b, 20, c_r)

        cv2.rectangle(img, (0, 0), (self.width, 60), (30, 30, 30), -1)
        cv2.putText(
            img,
            f"LIVE: {self.name.upper()} [TEST FEED]",
            (20, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 128),
            2,
            cv2.LINE_AA,
        )

        cur_time = time.strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(
            img,
            f"Time: {cur_time} | Frame: {frame_idx}",
            (20, self.height - 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (220, 220, 220),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            img,
            f"Resolution: {self.width}x{self.height} @ {self.fps}fps",
            (20, self.height - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (180, 180, 180),
            1,
            cv2.LINE_AA,
        )

        pos_x = int((np.sin(t * 2) + 1) / 2 * (self.width - 120)) + 60
        pos_y = int((np.cos(t * 3) + 1) / 2 * (self.height - 200)) + 100
        cv2.circle(img, (pos_x, pos_y), 28, (0, 165, 255), -1)
        cv2.putText(img, "REC", (pos_x - 18, pos_y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

        return img

    def _capture_loop(self):
        frame_idx = 0
        frame_delay = 1.0 / max(1, self.fps)

        while self.running:
            loop_start = time.time()

            if self.is_hardware_available and self.cap:
                ret, frame = self.cap.read()
                if ret and frame is not None:
                    if frame.shape[1] != self.width or frame.shape[0] != self.height:
                        frame = cv2.resize(frame, (self.width, self.height))
                    with self.lock:
                        self.latest_frame = frame
                else:
                    frame = self._generate_synthetic_frame(frame_idx)
                    with self.lock:
                        self.latest_frame = frame
            else:
                frame = self._generate_synthetic_frame(frame_idx)
                with self.lock:
                    self.latest_frame = frame

            frame_idx += 1
            elapsed = time.time() - loop_start
            sleep_time = max(0.001, frame_delay - elapsed)
            time.sleep(sleep_time)

    def get_frame(self) -> Optional[np.ndarray]:
        with self.lock:
            if self.latest_frame is not None:
                return self.latest_frame.copy()
            return None

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap:
            try:
                self.cap.release()
            except Exception as e:
                print(f"[CAMERA] Error releasing {self.name}: {e}")
            self.cap = None
        print(f"[CAMERA] {self.name} capture stopped.")

class SingleCameraTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, source, quality: str = DEFAULT_QUALITY, name: str = "Camera"):
        super().__init__()
        prof = QUALITY_PROFILES.get(quality.lower(), QUALITY_PROFILES["720p"])
        self.width = prof["width"]
        self.height = prof["height"]
        self.fps = prof["fps"]
        self.name = name

        self.reader = CameraCaptureThread(source, self.width, self.height, self.fps, name=self.name)
        self._pts = 0
        self._time_base = Fraction(1, self.fps)
        self._frame_interval = 1.0 / self.fps

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()

        frame_bgr = self.reader.get_frame()
        if frame_bgr is None:
            frame_bgr = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        video_frame = VideoFrame.from_ndarray(frame_bgr, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def stop_camera(self):
        self.reader.stop()

class CompositeAllCameraTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, in_source, out_source, quality: str = DEFAULT_QUALITY):
        super().__init__()
        prof = QUALITY_PROFILES.get(quality.lower(), QUALITY_PROFILES["720p"])
        self.total_width = prof["width"]
        self.total_height = prof["height"]
        self.fps = prof["fps"]

        self.half_width = self.total_width // 2
        self.sub_height = self.total_height

        self.reader_in = CameraCaptureThread(in_source, self.half_width, self.sub_height, self.fps, name="In-Camera")
        self.reader_out = CameraCaptureThread(out_source, self.half_width, self.sub_height, self.fps, name="Out-Camera")

        self._pts = 0
        self._time_base = Fraction(1, self.fps)

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()

        frame_in = self.reader_in.get_frame()
        frame_out = self.reader_out.get_frame()

        if frame_in is None:
            frame_in = np.zeros((self.sub_height, self.half_width, 3), dtype=np.uint8)
        if frame_out is None:
            frame_out = np.zeros((self.sub_height, self.half_width, 3), dtype=np.uint8)

        cv2.rectangle(frame_in, (0, 0), (self.half_width, 35), (20, 20, 20), -1)
        cv2.putText(frame_in, "CAM 1: IN (CABIN)", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 200), 2)

        cv2.rectangle(frame_out, (0, 0), (self.half_width, 35), (20, 20, 20), -1)
        cv2.putText(frame_out, "CAM 2: OUT (ROAD)", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 200, 0), 2)

        composite = np.hstack([frame_in, frame_out])

        video_frame = VideoFrame.from_ndarray(composite, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def stop_camera(self):
        self.reader_in.stop()
        self.reader_out.stop()

def create_video_tracks(camera_mode: str = "in", quality: str = DEFAULT_QUALITY):
    mode = (camera_mode or "in").lower()
    print(f"[CAMERA FACTORY] Creating video track(s) for mode='{mode}', quality='{quality}'")

    if mode == "in":
        return [SingleCameraTrack(source=CAMERA_IN_DEVICE, quality=quality, name="In-Camera")]
    elif mode == "out":
        return [SingleCameraTrack(source=CAMERA_OUT_DEVICE, quality=quality, name="Out-Camera")]
    elif mode in ("all", "both"):
        return [CompositeAllCameraTrack(in_source=CAMERA_IN_DEVICE, out_source=CAMERA_OUT_DEVICE, quality=quality)]
    else:
        print(f"[CAMERA FACTORY] Unknown mode '{mode}', defaulting to 'in'")
        return [SingleCameraTrack(source=CAMERA_IN_DEVICE, quality=quality, name="In-Camera")]
