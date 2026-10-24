"""실제 카메라 없이 연결 끊김과 USB 재등록을 재현합니다."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from common.camera import Camera, GSTREAMER_USB, open_capture, usb_pipeline


class CaptureError(Exception):
    pass


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.frame = SimpleNamespace(size=640 * 480 * 3)
        self.capture = self.device()
        self.cv2 = SimpleNamespace(VideoCapture=Mock(return_value=self.capture), error=CaptureError,
                                   CAP_GSTREAMER=1800, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict("sys.modules", {"cv2": self.cv2}))
        self.clock = stack.enter_context(patch("common.camera.time.monotonic", return_value=0.0))
        stack.enter_context(patch("builtins.print"))

    def device(self):
        return Mock(isOpened=Mock(return_value=True), read=Mock(return_value=(True, self.frame)))

    def test_regular_frames_keep_capture_and_resolution(self):
        camera = Camera(camera=2, size=(1280, 720))
        self.assertEqual(camera.read(), (self.frame, 0.0))
        self.assertEqual(camera.read(), (self.frame, 0.0))
        self.cv2.VideoCapture.assert_called_once_with(2)
        self.assertEqual(self.capture.set.call_args_list,
                         [call(3, 1280), call(4, 720)])
        self.capture.release.assert_not_called()

    def test_reconnect_after_long_absence_and_failed_open(self):
        missing, recovered = self.device(), self.device()
        missing.isOpened.return_value = False
        self.capture.read.return_value = False, None
        self.cv2.VideoCapture.side_effect = [self.capture, missing, recovered]
        camera = Camera(pipeline=GSTREAMER_USB)
        self.assertEqual(camera.read(), (None, 0.0))
        self.capture.release.assert_called_once()
        self.clock.return_value = 0.5
        self.assertEqual(camera.read(), (None, 0.0))
        self.assertEqual(self.cv2.VideoCapture.call_count, 1)  # 1초 이전에는 다시 열지 않음
        self.clock.return_value = 1.0
        self.assertEqual(camera.read(), (None, 0.0))
        missing.release.assert_called_once()
        self.clock.return_value = 60.0  # 예전 10초 제한을 넘어도 복구함
        self.assertEqual(camera.read(), (self.frame, 60.0))
        self.assertEqual(camera.read(), (self.frame, 0.0))
        for call in self.cv2.VideoCapture.call_args_list:
            self.assertEqual(call.args, (GSTREAMER_USB, self.cv2.CAP_GSTREAMER))
        recovered.set.assert_not_called()  # 파이프라인 해상도를 다시 설정하지 않음
        camera.release()
        camera.release()
        recovered.release.assert_called_once()

    def test_camera_can_disconnect_again_after_recovery(self):
        second, third = self.device(), self.device()
        self.capture.read.return_value = False, None
        second.read.side_effect = [(True, self.frame), (False, None)]
        self.cv2.VideoCapture.side_effect = [self.capture, second, third]
        camera = Camera()
        camera.read()
        self.clock.return_value = 1.0
        self.assertEqual(camera.read(), (self.frame, 1.0))
        self.clock.return_value = 2.0
        self.assertEqual(camera.read(), (None, 0.0))
        second.release.assert_called_once()
        self.clock.return_value = 3.0
        self.assertEqual(camera.read(), (self.frame, 1.0))

    def test_opened_device_without_frames_is_released_and_retried(self):
        empty, recovered = self.device(), self.device()
        self.capture.read.return_value = False, None
        empty.read.return_value = False, None
        self.cv2.VideoCapture.side_effect = [self.capture, empty, recovered]
        camera = Camera()
        camera.read()
        self.clock.return_value = 1.0
        self.assertEqual(camera.read(), (None, 0.0))
        empty.release.assert_called_once()
        self.clock.return_value = 2.0
        self.assertEqual(camera.read(), (self.frame, 2.0))

    def test_empty_frames_and_read_exceptions_trigger_reconnect(self):
        for result in ((True, None), (True, SimpleNamespace(size=0)), CaptureError("USB removed")):
            with self.subTest(result=result):
                device = self.device()
                if isinstance(result, Exception):
                    device.read.side_effect = result
                else:
                    device.read.return_value = result
                self.cv2.VideoCapture.return_value = device
                camera = Camera()
                self.assertEqual(camera.read(), (None, 0.0))
                device.release.assert_called_once()

    def test_initial_open_failure_is_reported_and_released(self):
        self.capture.isOpened.return_value = False
        with self.assertRaisesRegex(RuntimeError, "카메라를 열 수 없습니다"):
            Camera()
        self.capture.release.assert_called_once()

    def test_configuration_exception_releases_capture(self):
        self.capture.set.side_effect = CaptureError("device removed during setup")
        with self.assertRaises(CaptureError):
            open_capture()
        self.capture.release.assert_called_once()


class UsbPipelineTests(unittest.TestCase):
    def test_stable_identity_survives_video_number_change(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video0"
            video.touch()
            for name in ("by-id", "by-path"):
                (root / name).mkdir()
                (root / name / "camera-video-index0").symlink_to(video)

            def path(value):
                return root if value == "/dev/v4l" else video if value == "/dev/video0" else Path(value)

            with patch("common.camera.Path", side_effect=path):
                pipeline = usb_pipeline()
            identity = root / "by-id" / "camera-video-index0"
            self.assertIn(f'device="{identity}"', pipeline)
            video.rename(root / "video4")
            identity.unlink()
            identity.symlink_to(root / "video4")
            self.assertTrue(identity.exists())
            self.assertIn(f'device="{identity}"', pipeline)  # 저장한 파이프라인을 그대로 재사용 가능
            identity.unlink()
            with patch("common.camera.Path", side_effect=path):
                self.assertIn(str(root / "by-path"), usb_pipeline())

    def test_missing_stable_links_keep_original_pipeline(self):
        with TemporaryDirectory() as directory, patch("common.camera.Path", return_value=Path(directory)):
            self.assertEqual(usb_pipeline(), GSTREAMER_USB)


if __name__ == "__main__":
    unittest.main()
