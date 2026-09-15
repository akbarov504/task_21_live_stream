import unittest
import time
from unittest.mock import patch, MagicMock
from auth_api import parse_iso_expiry, get_token, get_info
import config
from camera_task import create_video_tracks, SingleCameraTrack, CompositeAllCameraTrack

class TestLiveStreamComponents(unittest.TestCase):
    def test_iso_expiry_parsing(self):
        iso_str = "2026-09-16T13:02:59.93889487"
        ts = parse_iso_expiry(iso_str)
        self.assertGreater(ts, 0)

    @patch("requests.get")
    def test_token_caching(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "expires_at": "2026-09-16T13:02:59.93889487",
            "token": "test_jwt_token_123"
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        t1 = get_token(force_refresh=True)
        self.assertEqual(t1, "test_jwt_token_123")
        self.assertEqual(mock_get.call_count, 1)

        t2 = get_token(force_refresh=False)
        self.assertEqual(t2, "test_jwt_token_123")
        self.assertEqual(mock_get.call_count, 1)

    @patch("requests.get")
    def test_info_caching(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "companyId": 1,
            "serialNumber": "f290c3c97361b8e3",
            "truckId": 68
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        info1 = get_info(force_refresh=True)
        self.assertEqual(info1.get("serialNumber"), "f290c3c97361b8e3")
        self.assertEqual(mock_get.call_count, 1)

        info2 = get_info(force_refresh=False)
        self.assertEqual(info2.get("truckId"), 68)
        self.assertEqual(mock_get.call_count, 1)

    def test_quality_profiles(self):
        for q in ["480p", "720p", "1080p", "2k"]:
            self.assertIn(q, config.QUALITY_PROFILES)
            prof = config.QUALITY_PROFILES[q]
            self.assertIn("width", prof)
            self.assertIn("height", prof)
            self.assertIn("fps", prof)

    @patch("cv2.VideoCapture")
    def test_camera_track_factory(self, mock_cv):
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cv.return_value = mock_cap

        tracks_in = create_video_tracks("in", "720p")
        self.assertEqual(len(tracks_in), 1)
        self.assertIsInstance(tracks_in[0], SingleCameraTrack)
        tracks_in[0].stop_camera()

        tracks_all = create_video_tracks("all", "1080p")
        self.assertEqual(len(tracks_all), 1)
        self.assertIsInstance(tracks_all[0], CompositeAllCameraTrack)
        tracks_all[0].stop_camera()

if __name__ == "__main__":
    unittest.main()
