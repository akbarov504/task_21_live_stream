import unittest
from unittest.mock import patch, MagicMock
from auth_api import parse_iso_expiry, get_token, get_info
import config
from camera_task import create_video_tracks, FFmpegCameraTrack, SyntheticFFmpegTrack

class TestLiveStreamComponents(unittest.TestCase):
    def test_iso_expiry_parsing(self):
        iso_str = "2030-01-01T00:00:00.000000"
        ts = parse_iso_expiry(iso_str)
        self.assertGreater(ts, 0)

    @patch("requests.get")
    def test_token_caching(self, mock_get):
        import auth_api
        auth_api._cached_token = None
        auth_api._token_expires_at = 0

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "expires_at": "2030-01-01T00:00:00.000000",
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
            "serialNumber": "1613a0b4be18b91e",
            "truckId": 40
        }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        info1 = get_info(force_refresh=True)
        self.assertEqual(info1.get("serialNumber"), "1613a0b4be18b91e")
        self.assertEqual(mock_get.call_count, 1)

        info2 = get_info(force_refresh=False)
        self.assertEqual(info2.get("truckId"), 40)
        self.assertEqual(mock_get.call_count, 1)

    def test_quality_profiles(self):
        for q in ["480p", "720p", "1080p", "2k"]:
            self.assertIn(q, config.QUALITY_PROFILES)
            prof = config.QUALITY_PROFILES[q]
            self.assertIn("width", prof)
            self.assertIn("height", prof)
            self.assertIn("fps", prof)

    def test_camera_track_factory(self):
        tracks_in = create_video_tracks("in", "720p")
        self.assertEqual(len(tracks_in), 1)
        self.assertIsInstance(tracks_in[0], FFmpegCameraTrack)
        tracks_in[0].stop_camera()

        tracks_all = create_video_tracks("all", "720p")
        self.assertEqual(len(tracks_all), 2)
        self.assertIsInstance(tracks_all[0], FFmpegCameraTrack)
        self.assertIsInstance(tracks_all[1], FFmpegCameraTrack)
        tracks_all[0].stop_camera()
        tracks_all[1].stop_camera()

if __name__ == "__main__":
    unittest.main()
