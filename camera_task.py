import asyncio
import os
import time
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
    Used when physical V4L2 camera is unavailable (e.g. testing / missing hardware).
    Consumes practically 0% CPU.
    """
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

        # Create clean YUV420P frame (native WebRTC format, no conversions needed)
        frame = av.VideoFrame(self.width, self.height, "yuv420p")

        # Background color modulation
        t = time.time() - self._start_time
        y_val = int((t * 20) % 200) + 20

        # Fill planes with solid color pattern
        for p in frame.planes:
            p.update(bytes([y_val] * p.buffer_size))

        frame.pts = pts
        frame.time_base = time_base
        return frame

    def stop_camera(self):
        pass

class FFmpegCameraTrack(MediaStreamTrack):
    """
    High-performance, low-CPU camera track backed by native FFmpeg (libavdevice / V4L2).
    Streams MJPEG / YUYV directly from hardware without OpenCV overhead.
    """
    kind = "video"

    def __init__(self, source: str, quality: str = DEFAULT_QUALITY, name: str = "Camera"):
        super().__init__()
        prof = QUALITY_PROFILES.get(quality.lower(), QUALITY_PROFILES["720p"])
        self.width = prof["width"]
        self.height = prof["height"]
        self.fps = prof["fps"]
        self.name = name
        self.source = str(source)
        self.player: Optional[MediaPlayer] = None
        self._fallback_track: Optional[MediaStreamTrack] = None

        self._init_ffmpeg_player()

    def _init_ffmpeg_player(self):
        print(f"[FFMPEG CAMERA] Opening {self.name} via FFmpeg V4L2: source={self.source}, {self.width}x{self.height}@{self.fps}fps")

        is_linux = platform.system() == "Linux"
        fmt = "v4l2" if is_linux else None

        # Try MJPEG hardware decoding first (highest FPS, lowest CPU)
        options = {
            "video_size": f"{self.width}x{self.height}",
            "framerate": str(self.fps),
            "input_format": "mjpeg",
        }

        try:
            if is_linux and (os.path.exists(self.source) or self.source.startswith("/dev/")):
                self.player = MediaPlayer(self.source, format=fmt, options=options)
                print(f"[FFMPEG CAMERA] {self.name} opened with MJPEG V4L2 ✅")
            else:
                raise RuntimeError(f"Device {self.source} not found on this system")
        except Exception as e1:
            print(f"[FFMPEG CAMERA] MJPEG open warning ({e1}), trying raw V4L2...")
            try:
                # Fallback to standard V4L2 format
                raw_options = {
                    "video_size": f"{self.width}x{self.height}",
                    "framerate": str(self.fps),
                }
                self.player = MediaPlayer(self.source, format=fmt, options=raw_options)
                print(f"[FFMPEG CAMERA] {self.name} opened with raw V4L2 ✅")
            except Exception as e2:
                print(f"[FFMPEG CAMERA] Hardware open failed ({e2}). Switching to Synthetic fallback.")
                self.player = None
                self._fallback_track = SyntheticFFmpegTrack(self.width, self.height, self.fps, name=self.name)

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

        # Last resort black frame
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

def create_video_tracks(camera_mode: str = "all", quality: str = DEFAULT_QUALITY) -> List[MediaStreamTrack]:
    """
    Factory function using FFmpeg to create video tracks.
    - 'in'  : returns [In-Camera Track]
    - 'out' : returns [Out-Camera Track]
    - 'all' : returns [In-Camera Track, Out-Camera Track] (Multi-stream WebRTC)
    """
    mode = (camera_mode or "all").lower()
    print(f"[CAMERA FACTORY] Creating FFmpeg video track(s) for mode='{mode}', quality='{quality}'")

    if mode == "in":
        return [FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera")]
    elif mode == "out":
        return [FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera")]
    elif mode in ("all", "both"):
        track_in = FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera")
        track_out = FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera")
        return [track_in, track_out]
    else:
        print(f"[CAMERA FACTORY] Unknown mode '{mode}', defaulting to 'all'")
        track_in = FFmpegCameraTrack(source=IN_VIDEO_DEVICE, quality=quality, name="In-Camera")
        track_out = FFmpegCameraTrack(source=OUT_VIDEO_DEVICE, quality=quality, name="Out-Camera")
        return [track_in, track_out]
