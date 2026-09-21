import asyncio
import os
import time
import gc
import platform
from fractions import Fraction
from typing import Optional, List, Dict, Any
import av
import numpy as np
from aiortc import MediaStreamTrack
from aiortc.contrib.media import MediaPlayer

RECV_TIMEOUT = 3.0

from config import (
    QUALITY_PROFILES,
    DEFAULT_QUALITY,
    IN_VIDEO_DEVICE,
    OUT_VIDEO_DEVICE,
)

class SyntheticFFmpegTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30, name: str = "Camera"):
        super().__init__()
        self.width = width
        self.height = height
        self.fps = fps
        self.name = name
        self._time_base = Fraction(1, fps)
        self._pts = 0
        self._start_time = time.time()
        print(f"[FFMPEG CAMERA] Using Synthetic FFmpeg Track for {self.name} ({width}x{height}@{fps}fps) ⚠️")

    async def recv(self) -> av.VideoFrame:
        pts, time_base = await self.next_timestamp()
        frame = av.VideoFrame(self.width, self.height, "yuv420p")
        frame.planes[0].update(bytes([16] * frame.planes[0].buffer_size))   # Y
        frame.planes[1].update(bytes([128] * frame.planes[1].buffer_size))  # Cb
        frame.planes[2].update(bytes([128] * frame.planes[2].buffer_size))  # Cr
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def stop_camera(self):
        pass

class FFmpegCameraTrack(MediaStreamTrack):
    kind = "video"

    def __init__(self, source: str, quality: str = DEFAULT_QUALITY, name: str = "Camera", is_dual: bool = False):
        super().__init__()
        prof = QUALITY_PROFILES.get(quality.lower(), QUALITY_PROFILES["720p"])
        self.target_width = prof["width"]
        self.target_height = prof["height"]
        self.target_fps = prof["fps"]
        self.is_dual = is_dual
        self.name = name
        self.source = str(source)
        self.player: Optional[MediaPlayer] = None
        self._fallback_track: Optional[MediaStreamTrack] = None
        self._consecutive_errors: int = 0

        self.width = self.target_width
        self.height = self.target_height
        self.fps = self.target_fps

        self._init_ffmpeg_player()

    def _init_ffmpeg_player(self):
        is_linux = platform.system() == "Linux"
        fmt = "v4l2" if is_linux else None

        resolutions_to_try = [
            (self.target_width, self.target_height, self.target_fps),
            (1920, 1080, 30),
            (1280, 720, 30),
            (640, 480, 25),
        ]

        seen = set()
        unique_resolutions = []
        for w, h, fps in resolutions_to_try:
            key = (w, h, fps)
            if key not in seen:
                seen.add(key)
                unique_resolutions.append(key)

        opened = False
        for w, h, fps in unique_resolutions:
            print(f"[FFMPEG CAMERA] Opening {self.name} via V4L2 ({w}x{h}@{fps}fps, device={self.source})...")
            try:
                if is_linux and (os.path.exists(self.source) or self.source.startswith("/dev/")):
                    options_raw = {
                        "video_size": f"{w}x{h}",
                        "framerate": str(fps),
                    }
                    self.player = MediaPlayer(self.source, format=fmt, options=options_raw)
                    self.width = w
                    self.height = h
                    self.fps = fps
                    opened = True
                    print(f"[FFMPEG CAMERA] {self.name} opened successfully on {self.source} ({w}x{h}@{fps}fps) ✅")
                    break
                else:
                    raise RuntimeError(f"Device {self.source} not accessible on this platform")
            except Exception as e1:
                try:
                    options_mjpeg = {
                        "video_size": f"{w}x{h}",
                        "framerate": str(fps),
                        "input_format": "mjpeg",
                    }
                    self.player = MediaPlayer(self.source, format=fmt, options=options_mjpeg)
                    self.width = w
                    self.height = h
                    self.fps = fps
                    opened = True
                    print(f"[FFMPEG CAMERA] {self.name} opened with MJPEG {w}x{h}@{fps}fps on {self.source} ✅")
                    break
                except Exception as e2:
                    print(f"[FFMPEG CAMERA] {self.name} failed at {w}x{h}@{fps}fps: {e1} / {e2}")

                if self.player:
                    try:
                        if hasattr(self.player, "container") and self.player.container:
                            self.player.container.close()
                    except Exception:
                        pass
                    self.player = None

                gc.collect()
                time.sleep(0.3)
                continue

        if not opened and is_linux and os.path.exists(self.source):
            print(f"[FFMPEG CAMERA] Trying native auto-negotiated V4L2 format for {self.source}...")
            try:
                self.player = MediaPlayer(self.source, format=fmt)
                opened = True
                print(f"[FFMPEG CAMERA] {self.name} opened with native V4L2 stream on {self.source} ✅")
            except Exception as e_native:
                print(f"[FFMPEG CAMERA] Native V4L2 failed on {self.source}: {e_native}")

        if not opened:
            print(f"[FFMPEG CAMERA] Device {self.source} unavailable. Using synthetic fallback.")
            self.player = None
            self._fallback_track = SyntheticFFmpegTrack(self.target_width, self.target_height, self.target_fps, name=self.name)

    async def recv(self) -> av.VideoFrame:
        if self.player and self.player.video:
            try:
                frame = await asyncio.wait_for(
                    self.player.video.recv(),
                    timeout=RECV_TIMEOUT,
                )
                self._consecutive_errors = 0
                return frame
            except asyncio.TimeoutError:
                self._consecutive_errors = getattr(self, "_consecutive_errors", 0) + 1
                if self._consecutive_errors == 1:
                    print(f"[FFMPEG CAMERA] {self.name} recv() timeout ({RECV_TIMEOUT}s) — synthetic frame qaytarilmoqda...")
                if not self._fallback_track:
                    self._fallback_track = SyntheticFFmpegTrack(self.width, self.height, self.fps, name=self.name)
                return await self._fallback_track.recv()
            except Exception as e:
                self._consecutive_errors = getattr(self, "_consecutive_errors", 0) + 1
                if self._consecutive_errors == 1:
                    print(f"[FFMPEG CAMERA] {self.name} recv error: {e} — synthetic frame qaytarilmoqda...")
                if not self._fallback_track:
                    self._fallback_track = SyntheticFFmpegTrack(self.width, self.height, self.fps, name=self.name)
                return await self._fallback_track.recv()

        if self._fallback_track:
            return await self._fallback_track.recv()

        frame = av.VideoFrame(self.width, self.height, "yuv420p")
        pts, time_base = await self.next_timestamp()
        frame.planes[0].update(bytes([16] * frame.planes[0].buffer_size))
        frame.planes[1].update(bytes([128] * frame.planes[1].buffer_size))
        frame.planes[2].update(bytes([128] * frame.planes[2].buffer_size))
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def stop_camera(self):
        print(f"[FFMPEG CAMERA] Releasing {self.name} resources ({self.source})...")
        if self.player:
            try:
                if hasattr(self.player, "container") and self.player.container:
                    self.player.container.close()
            except Exception as e:
                print(f"[FFMPEG CAMERA] Close error for {self.name}: {e}")
            self.player = None
        if self._fallback_track:
            self._fallback_track.stop_camera()
        gc.collect()

def create_video_tracks(camera_mode: str = "all", quality: str = DEFAULT_QUALITY) -> List[MediaStreamTrack]:
    mode = (camera_mode or "all").lower()
    print(f"[CAMERA FACTORY] Creating FFmpeg video track(s) for mode='{mode}', quality='{quality}'")

    if mode == "in":
        return [FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera (IN_VCAM2)", is_dual=False)]
    elif mode == "out":
        return [FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera (OUT_VCAM2)", is_dual=False)]
    elif mode in ("all", "both"):
        track_in = FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera (IN_VCAM2)", is_dual=True)
        track_out = FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera (OUT_VCAM2)", is_dual=True)
        return [track_in, track_out]
    else:
        print(f"[CAMERA FACTORY] Unknown mode '{mode}', defaulting to 'all'")
        track_in = FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera (IN_VCAM2)", is_dual=True)
        track_out = FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera (OUT_VCAM2)", is_dual=True)
        return [track_in, track_out]
