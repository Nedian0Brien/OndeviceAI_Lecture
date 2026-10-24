"""카메라 실행 옵션과 경로 (표준 라이브러리만 사용).

카메라 관련 공통 옵션은 `add_camera_arguments`로 한 번에 붙입니다. 카메라를 눈 위치에 두는 1인칭 배치가 기본이라
좌우 반전은 하지 않고, 셀카 배치에서만 project01.py의 `--mirror`로 켭니다.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]  # 저장소 루트 (common → Project01 → 루트)
DEFAULT_MODEL = ROOT / "src/models/MediaPipe/hand_landmarker.task"
PIPELINE_HELP = "GStreamer 문자열. 'usb'는 practice의 /dev/video0 설정"


def add_camera_arguments(parser, camera_help=None):
    """--camera, --pipeline, --model, --font를 붙입니다."""
    parser.add_argument("--camera", type=int, default=0, help=camera_help)
    parser.add_argument("--pipeline", help=PIPELINE_HELP)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--font", help="한글 폰트 ttf/ttc/otf 경로")


def resolve_pipeline(args):
    """--pipeline usb를 Jetson USB 카메라용 GStreamer 문자열로 바꿉니다."""
    from common.camera import GSTREAMER_USB

    if args.pipeline == "usb":  # 약칭만 바꾸고, 직접 쓴 파이프라인 문자열은 그대로 둠
        args.pipeline = GSTREAMER_USB
