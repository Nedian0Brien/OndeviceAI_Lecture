"""모든 앱이 쓰는 실행 옵션과 경로 (표준 라이브러리만 사용).

카메라 관련 공통 옵션은 `add_camera_arguments`로 한 번에 붙인다. 좌우 반전 옵션은 앱마다 다르게 둔다.
카메라 배치가 다르기 때문이다.
- 엄지 마디 앱·꼬집기 키보드·녹화 도구: 카메라가 사람을 마주 보는 셀카 배치 → 기본 반전, `--no-mirror`로 끔
- 손끝 꼬집기 앱: 카메라를 눈 위치에 두는 1인칭 배치 → 기본 비반전, `--mirror`로 켬
"""

import argparse
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]  # 저장소 루트 (common → Project01 → 루트)
DEFAULT_MODEL = ROOT / "src/models/MediaPipe/hand_landmarker.task"
PIPELINE_HELP = "GStreamer 문자열. 'usb'는 practice의 /dev/video0 설정"


def positive_float(value):
    """argparse type: 0보다 큰 유한수."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("0보다 큰 유한수를 지정하세요.")
    return result


def add_camera_arguments(parser, camera_help=None):
    """--camera, --pipeline, --model, --font를 붙인다. camera_help는 앱마다 다른 --camera 설명이다."""
    parser.add_argument("--camera", type=int, default=0, help=camera_help)
    parser.add_argument("--pipeline", help=PIPELINE_HELP)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--font", help="한글 폰트 ttf/ttc/otf 경로")


def resolve_pipeline(args):
    """--pipeline usb를 Jetson USB 카메라용 GStreamer 문자열로 바꾼다."""
    from common.camera import GSTREAMER_USB

    if args.pipeline == "usb":
        args.pipeline = GSTREAMER_USB
