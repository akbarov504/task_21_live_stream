import asyncio
import os
import time
import gc
import platform
from fractions import Fraction
from typing import Optional, List, Dict, Any
import av
from aiortc import MediaStreamTrack
from aiortc.contrib.media import MediaPlayer

from config import (
    QUALITY_PROFILES,
    DEFAULT_QUALITY,
    IN_VIDEO_DEVICE,
    OUT_VIDEO_DEVICE,
)

class SyntheticFFmpegTrack(MediaStreamTrack):
    """
    Lightweight synthetic video track using PyAV.
    Used when physical V4L2 camera is unavailable.
    """
    kind = "video"

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 25, name: str = "Camera"):
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

        t = time.time() - self._start_time
        y_val = int((t * 20) % 200) + 20

        for p in frame.planes:
            p.update(bytes([y_val] * p.buffer_size))

        frame.pts = pts
        frame.time_base = time_base
        return frame

    def stop_camera(self):
        pass

class FFmpegCameraTrack(MediaStreamTrack):
    """
    High-performance camera track backed by native FFmpeg (libavdevice / V4L2).
    Includes smart USB bandwidth handling and clean device releasing.
    """
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

        # In dual camera mode, use optimized 20-25 fps to prevent USB bus saturation
        if self.is_dual and self.target_fps > 20:
            self.target_fps = 20

        self.width = self.target_width
        self.height = self.target_height
        self.fps = self.target_fps

        self._init_ffmpeg_player()

    def _init_ffmpeg_player(self):
        is_linux = platform.system() == "Linux"
        fmt = "v4l2" if is_linux else None

        # Ladder of (width, height, fps) configurations to try
        if self.is_dual:
            resolutions_to_try = [
                (self.target_width, self.target_height, self.target_fps),
                (1280, 720, 20),
                (1280, 720, 15),
                (640, 480, 25),
                (1920, 1080, 15),
            ]
        else:
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
            options_mjpeg = {
                "video_size": f"{w}x{h}",
                "framerate": str(fps),
                "input_format": "mjpeg",
            }
            print(f"[FFMPEG CAMERA] Opening {self.name} via V4L2 MJPEG: {w}x{h}@{fps}fps (device={self.source})...")
            try:
                if is_linux and (os.path.exists(self.source) or self.source.startswith("/dev/")):
                    self.player = MediaPlayer(self.source, format=fmt, options=options_mjpeg)
                    self.width = w
                    self.height = h
                    self.fps = fps
                    opened = True
                    print(f"[FFMPEG CAMERA] {self.name} opened successfully with MJPEG {w}x{h}@{fps}fps ✅")
                    break
                else:
                    raise RuntimeError(f"Device {self.source} not accessible on this platform")
            except Exception as e:
                err_str = str(e)
                print(f"[FFMPEG CAMERA] {self.name} attempt failed at {w}x{h}@{fps}fps: {err_str}")

                # Ensure previous container / file descriptor is completely closed
                if self.player:
                    try:
                        if hasattr(self.player, "container") and self.player.container:
                            self.player.container.close()
                    except Exception:
                        pass
                    self.player = None

                gc.collect()
                time.sleep(0.5)  # Allow V4L2 kernel driver to release device endpoint
                continue

        if not opened:
            print(f"[FFMPEG CAMERA] Physical device {self.source} unavailable. Using synthetic fallback.")
            self.player = None
            self._fallback_track = SyntheticFFmpegTrack(self.target_width, self.target_height, self.target_fps, name=self.name)

    async def recv(self) -> av.VideoFrame:
        if self.player and self.player.video:
            try:
                return await self.player.video.recv()
            except Exception as e:
                print(f"[FFMPEG CAMERA] {self.name} recv error: {e}")
                if not self._fallback_track:
                    self._fallback_track = SyntheticFFmpegTrack(self.width, self.height, self.fps, name=self.name)
                return await self._fallback_track.recv()

        if self._fallback_track:
            return await self._fallback_track.recv()

        frame = av.VideoFrame(self.width, self.height, "yuv420p")
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def stop_camera(self):
        print(f"[FFMPEG CAMERA] Releasing {self.name} resources...")
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
    """
    Factory function using FFmpeg to create video tracks.
    - 'in'  : returns [In-Camera Track]
    - 'out' : returns [Out-Camera Track]
    - 'all' : returns [In-Camera Track, Out-Camera Track] (Dual-stream WebRTC)
    """
    mode = (camera_mode or "all").lower()
    print(f"[CAMERA FACTORY] Creating FFmpeg video track(s) for mode='{mode}', quality='{quality}'")

    if mode == "in":
        return [FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera", is_dual=False)]
    elif mode == "out":
        return [FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera", is_dual=False)]
    elif mode in ("all", "both"):
        track_in = FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera", is_dual=True)
        time.sleep(0.5)  # Allow USB host controller bandwidth allocation to settle
        track_out = FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera", is_dual=True)
        return [track_in, track_out]
    else:
        print(f"[CAMERA FACTORY] Unknown mode '{mode}', defaulting to 'all'")
        track_in = FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera", is_dual=True)
        time.sleep(0.5)
        track_out = FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera", is_dual=True)
        return [track_in, track_out]
