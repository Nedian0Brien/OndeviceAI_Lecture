"""카메라 열기, 프레임 읽기, 해상도 선택, 영상 창, 시각·fps 계산.

시연 장비는 Jetson + USB 카메라(GStreamer 640x480 30fps)입니다. macOS에서는 iPhone을 Continuity Camera로 씁니다.
- Jetson: `--pipeline usb` → `GSTREAMER_USB` 파이프라인. 해상도는 파이프라인 문자열이 정합니다.
- macOS: `--camera <번호>`. OpenCV는 카메라를 uniqueID 문자열 순으로 번호를 매깁니다. iPhone이 연결되어 있으면
  보통 0번이 iPhone, 1번이 내장 카메라입니다(2026-10-18 확인). macOS용 OpenCV에는 GStreamer가 없습니다.

OpenCV는 함수·메서드 안에서 import합니다.
"""

import argparse
import sys
import time


# Jetson USB 카메라(practice 설정): YUY2 640x480 30fps를 BGR로 바꿔 appsink로 받음
# queue와 appsink의 drop 설정은 처리가 늦을 때 오래된 프레임을 버려 지연이 쌓이지 않게 함
GSTREAMER_USB = (
    "v4l2src device=/dev/video0 ! "
    "video/x-raw,format=YUY2,width=640,height=480,framerate=30/1 ! "
    "videoconvert ! video/x-raw,format=BGR ! "
    "queue leaky=downstream max-size-buffers=1 ! "
    "appsink drop=true max-buffers=1 sync=false"
)
# 카메라 번호로 열 때 요청하는 해상도. 시연 장비 기준(640x480)이 기본이고, macOS에서 iPhone으로 시험할 때만
# 1280x720을 씀. 저해상도 USB 카메라에 720p를 요청하면 fps가 크게 떨어지는 경우가 있어 Jetson에서는 올리지 않음
DEFAULT_RESOLUTION = (640, 480)
AUTO_RESOLUTION = {"darwin": (1280, 720)}


def parse_resolution(value):
    """argparse type: 'auto' 또는 '1280x720' 꼴을 받습니다."""
    if value == "auto":
        return value
    try:  # "가로x세로" 꼴만 받음
        width, height = (int(part) for part in value.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("auto 또는 1280x720 꼴로 지정하세요.") from None
    if width < 160 or height < 120:  # 최소 해상도 검사
        raise argparse.ArgumentTypeError("너무 작은 해상도입니다.")
    return width, height


def capture_size(resolution, platform=None):
    """요청할 해상도를 돌려줍니다. 'auto'는 macOS 1280x720, 그 밖(Jetson 등) 640x480입니다."""
    if resolution != "auto":  # 직접 지정한 해상도는 그대로
        return resolution
    return AUTO_RESOLUTION.get(platform or sys.platform, DEFAULT_RESOLUTION)


def open_capture(camera=0, pipeline=None, size=DEFAULT_RESOLUTION):
    """pipeline이 있으면 GStreamer, 없으면 카메라 번호로 캡처를 엽니다.

    size는 카메라 번호로 열 때 요청하는 해상도입니다. 카메라가 지원하지 않으면 가까운 값이 들어옵니다.
    GStreamer 파이프라인은 문자열 안의 width/height가 해상도를 정합니다.
    """
    import cv2

    capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER) if pipeline else cv2.VideoCapture(camera)
    if not capture.isOpened():  # 장치가 없거나 다른 앱이 쓰는 중
        raise RuntimeError("카메라를 열 수 없습니다. --camera 또는 --pipeline 값을 확인하세요.")
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
    return capture


def read_frame(capture, wait=10.0):
    """(프레임, 끊겼던 시간 초)를 돌려줍니다. 끊기지 않았으면 0입니다.

    iPhone Continuity Camera는 기기를 움직이거나 잠금을 풀면 잠시 멈춥니다. wait초까지 다시 들어오기를
    기다리고, 그래도 없으면 RuntimeError를 냅니다.
    끊긴 동안의 손 상태로 입력하지 않도록, 끊김이 있었으면 앱이 판정기를 초기화합니다.
    """
    import cv2

    ok, frame = capture.read()
    if ok:  # 보통의 경우: 바로 읽힘
        return frame, 0.0
    since = time.monotonic()
    print("카메라 프레임이 끊겼습니다. 다시 들어오기를 기다립니다.", flush=True)
    # 50ms 간격으로 wait초까지 다시 읽어 봄(waitKey로 창 이벤트도 처리함)
    while time.monotonic() - since <= wait:
        cv2.waitKey(50)
        ok, frame = capture.read()
        if ok:
            gap = time.monotonic() - since
            print(f"카메라 프레임이 다시 들어옵니다 ({gap:.1f}초 끊김).", flush=True)
            return frame, gap
    raise RuntimeError(f"카메라 프레임을 {wait:.0f}초 동안 읽지 못했습니다. 연결/파이프라인을 확인하세요.")


def window_size(w, h, min_width=960, max_width=1440, max_height=900):
    """영상 크기에 맞춘 처음 창 크기를 돌려줍니다. 640x480은 960x720으로 키우고, 큰 영상은 화면에 들어오게 줄입니다."""
    scale = min(max(min_width / w, 1.0), max_width / w, max_height / h)
    return round(w * scale), round(h * scale)


def is_window_closed(cv2, name):
    """창이 닫혔는지 돌려줍니다. 미지원(-1)과 닫힘(0)을 구분합니다.

    일부 GTK/Wayland 환경은 창 표시 여부 조회를 지원하지 않습니다. 그때는 닫힘으로 보지 않고 Q/Esc로 종료합니다.
    cv2를 인자로 받는 것은 테스트에서 가짜 모듈을 넣기 위해서입니다.
    """
    try:
        visible = cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE)
    except cv2.error:  # 조회 자체가 실패함: 닫히지 않은 것으로 봄
        return False
    return visible == 0


class Window:
    """OpenCV 영상 창 하나.

    size를 주면 처음부터 그 크기로 열고, 없으면 첫 화면을 보여줄 때 영상 크기에 맞춥니다(`window_size`).
    """

    def __init__(self, title, size=None):
        import cv2

        self.title = title
        self.fullscreen = False
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        self.sized = size is not None  # 창 크기를 이미 정했는지
        if size is not None:
            cv2.resizeWindow(title, *size)

    def show(self, image):
        """영상을 창에 표시합니다. 첫 표시 때 창 크기를 영상에 맞춥니다."""
        import cv2

        if not self.sized:  # 첫 화면: 영상 크기에 맞춰 창 크기를 정함
            cv2.resizeWindow(self.title, *window_size(image.shape[1], image.shape[0]))
            self.sized = True
        cv2.imshow(self.title, image)

    def read_key(self):
        """1ms 동안 키 입력을 기다립니다. 입력이 없으면 255입니다(waitKey의 -1 & 0xFF)."""
        import cv2

        return cv2.waitKey(1) & 0xFF

    def closed(self):
        """사용자가 창을 닫았는지 돌려줍니다."""
        import cv2

        return is_window_closed(cv2, self.title)

    def toggle_fullscreen(self):
        """전체 화면과 일반 창을 오갑니다."""
        import cv2

        self.fullscreen = not self.fullscreen
        cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN,
                              cv2.WINDOW_FULLSCREEN if self.fullscreen else cv2.WINDOW_NORMAL)


class VideoClock:
    """MediaPipe VIDEO 모드에 넘길 ms 시각입니다. 같은 ms에 두 프레임이 와도 엄격히 증가하게 만듭니다."""

    def __init__(self):
        self.last = -1

    def ms(self, now):
        """now(초) → 직전보다 큰 ms 정수."""
        self.last = max(self.last + 1, int(now * 1000))
        return self.last


class FpsMeter:
    """화면 표시용 fps입니다. 프레임 간격의 역수를 지수 이동 평균(새 값 10%)으로 부드럽게 합니다."""

    def __init__(self):
        self.previous = time.monotonic()
        self.fps = 0.0

    def tick(self, now):
        """프레임 하나가 지났음을 알리고 갱신한 fps를 돌려줍니다."""
        self.fps = 0.9 * self.fps + 0.1 / max(now - self.previous, 1e-6)
        self.previous = now
        return self.fps
