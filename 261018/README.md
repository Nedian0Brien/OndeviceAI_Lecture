# 손마디 천지인 입력기

손 카메라 영상으로 한글 천지인 입력을 하는 프로젝트입니다. MediaPipe Hand Landmarker로 손 landmark를 추적하고, 입력 결과를 카메라 영상 위 HUD로 표시하며 `S`로 UTF-8 텍스트 파일에 저장합니다. 다른 프로그램에 키 입력을 전달하는 OS 입력기는 포함하지 않습니다.

현재 주 버전은 **손끝 꼬집기 입력기(`pinch_cheonjiin.py`)** 입니다. 엄지로 손끝을 꼬집으면 그 손가락이 버튼이고, 같은 손가락을 꼬집은 횟수로 그룹 안의 자모를 고릅니다. 목표 용도가 패스스루 VR이라, 카메라는 **눈 위치에서 손을 내려다보는 1인칭 배치**를 기준으로 합니다(기본값: 좌우 반전 없음). 설계 근거는 `.intent/*_pinch-cheonjiin.md`에 있습니다. 앞서 시도한 방식은 [이전 시도](#이전-시도)에 정리했습니다.

## 손끝 꼬집기 입력기 (`pinch_cheonjiin.py`)

### 자판

(손바닥이 카메라를 향한 자세. 손등을 책상이나 무릎에 대고 손바닥을 위로 두면 편합니다)

| | 검지 | 중지 | 약지 | 소지 |
| --- | --- | --- | --- | --- |
| 왼손 | ㅣ | ㆍ | ㅡ | ⇧ 쉬프트 |
| 오른손 | ㄱ→ㅋ→ㄲ | ㄴ→ㄹ | ㄷ→ㅌ→ㄸ | ㅇ→ㅁ |
| 오른손 + ⇧ | ㅂ→ㅍ→ㅃ | ㅅ→ㅎ→ㅆ | ㅈ→ㅊ→ㅉ | . → , → ? → ! → 줄바꿈 |

### 규칙
- 같은 손가락을 다시 꼬집으면 순환합니다. **다른 버튼을 꼬집으면 앞 글자는 자동으로 확정**됩니다. 모음은 꼬집는 즉시 들어갑니다.
- ⇧(왼손 소지)는 다음 자음 하나에만 적용됩니다. 한 번 더 꼬집으면 취소됩니다.
- **왼손 주먹**: 후보가 있으면 확정, 없으면 공백. `안녕`의 ㄴ→ㄴ처럼 같은 손가락 자음이 이어질 때만 확정이 필요합니다.
- **오른손 주먹**: 지움. 순환 중인 후보가 있으면 후보 전체를, 없으면 마지막 입력 한 번을 지웁니다.
- 꼬집기는 손끝을 **뗄 때** 1회로 셉니다. 주먹을 쥐는 도중 엄지가 손끝을 스치는 것은 입력하지 않습니다.
- 각 손가락 끝 바로 바깥에 그 손가락의 키가 표시됩니다(`T`로 끄기). 꼬집을수록 라벨이 밝아지고, 순환 중인 손가락에는 현재 후보가 보입니다.

예: `안녕` = ㅇ(오른 소지) ㅣ ㆍ ㄴ(오른 중지) · 왼손 주먹 · ㄴ ㆍ ㆍ ㅣ ㅇ

### 마우스

(오른손을 뒤집어 손등이 보이면 마우스 모드, 키보드 입력은 멈춤)
- 오른손 엄지-검지를 꼬집은 채 움직이면 커서 이동(놓고 다시 잡기). 떨림은 칼만 필터로 거릅니다(`docs/wiki/Kalman-Filter.md`).
- 왼손 손등 엄지-검지: 짧게 1회 좌클릭, 2회 우클릭, 유지하면 버튼 누름(드래그).
- 기본은 영상 창 안의 가상 커서입니다. 오른쪽 위 저장·일시정지·도움말 버튼을 누르고, 우클릭으로 복사·전체 지우기 메뉴를 엽니다.
- `--os-mouse`를 주면 실제 OS 마우스를 움직입니다. `python -m pip install pynput`이 필요하고, macOS에서는 시스템 설정 > 개인정보 보호 및 보안 > 손쉬운 사용에서 터미널을 허용해야 합니다.

### 실행

```bash
python 261018/pinch_cheonjiin.py --camera 0                 # 1인칭 카메라 (iPhone 연결 시 0번)
python 261018/pinch_cheonjiin.py --camera 0 --log 261018/data/pinch-log.jsonl   # 손 신호 기록(임계값 조정용)
python 261018/pinch_cheonjiin.py --os-mouse                 # 실제 OS 마우스 (B)
python 261018/pinch_cheonjiin.py --mirror                   # 카메라가 사람을 마주 보는 셀카 배치
python 261018/pinch_cheonjiin.py --demo                     # 카메라 없이 '안녕하세요' 재생
```

| 키 | 기능 |
| --- | --- |
| `1`~`4` / `7` `8` `9` `0` | 왼손 / 오른손 검지~소지 꼬집기 시험 |
| Space / Backspace | 왼손 주먹(확정·공백) / 오른손 주먹(지움) 시험 |
| `[` / `]` | 꼬집기 판정값 낮춤 / 높임 (잘 안 잡히면 `]`, 닿기 전에 입력되면 `[`) |
| T / K / D | 손끝 라벨 / 화면 아래 자판 지도 / 손가락별 진단 수치 켜기·끄기 |
| S / P / H | 저장(`261018/pinch_output.txt`) / 일시정지 / 도움말 |
| G | 진단 스크린샷 저장 (`261018/data/snapshots/`, 1초마다·모드 전환 때도 자동 저장) |
| Q / Esc | 종료 |

### 구성
- `pinch_typer.py`: 카메라와 분리한 핵심 상태 머신(표준 라이브러리만). 손별 신호(손가락별 꼬집기 비율, 굽힘, 자세, 손 방향, 커서 기준점)를 받아 입력과 마우스 이벤트를 만듭니다. visionOS(ARKit)로 옮길 때도 이 신호만 만들면 됩니다.
- `fixtures/pinch-typer-keys.json`: 입력 시퀀스와 기대 결과. 다른 언어로 이식할 때 같은 파일로 검증합니다.
- 꼬집기 거리 = 엄지 끝~손가락 끝마디 선분(DIP→TIP) world 거리 / 손목~중지 뿌리 길이. landmark가 뼈 축 위에 있어 살이 맞닿아도 0이 되지 않으므로, 손끝 점이 아니라 끝마디 선분까지 잽니다. 0.25 아래면 접촉, 0.38 위면 뗌이고, 가장 가까운 손가락이 두 번째보다 0.03 이상 가까워야 합니다(중지를 꼬집으면 약지도 함께 가까워지기 때문). `D`를 누르면 손가락별 값이 보입니다.
- 판정값은 모든 사람에게 같은 고정값이며 사용자별 보정 단계는 없습니다. 현재 값은 임시값이고 `--log` 실측으로 다시 정합니다.
- 손 방향은 손목·검지 뿌리·소지 뿌리가 이루는 2D 외적의 부호로 정합니다. 왼손·오른손은 MediaPipe 좌우 라벨을 따르고, 두 손에 같은 라벨이 붙었을 때만 화면 위치로 나눕니다(왼쪽 = 왼손).

## 환경 준비

아래 명령은 리포지토리 루트 `Vision-LLM-202035152` 기준입니다. 필요한 패키지는 OpenCV, NumPy, MediaPipe, Pillow입니다.

**Jetson**
- JetPack이 제공한 OpenCV/GStreamer 환경을 유지하고, 노트북의 MediaPipe 설치 안내를 따릅니다.
- Pillow가 없으면 `python -m pip install Pillow`로 추가합니다.
- USB 카메라는 `--pipeline usb`(practice의 `/dev/video0` 설정)로 엽니다. CSI 카메라는 `--pipeline 'nvarguscamerasrc ... ! appsink ...'`처럼 GStreamer 문자열 전체를 넘깁니다.

**macOS + iPhone 카메라**
- iPhone을 Continuity Camera로 씁니다. 이 환경의 OpenCV에는 GStreamer가 없으므로 `--pipeline` 대신 `--camera` 번호를 씁니다.
- OpenCV는 카메라를 `uniqueID` 문자열 순으로 번호를 매깁니다. 2026-10-18 기준으로 iPhone이 연결되어 있으면 0번이 iPhone, 1번이 내장 카메라입니다. iPhone 연결이 끊기면 0번이 내장 카메라가 됩니다.
- iPhone은 잠근 상태로 두고, 후면 카메라가 손을 향하게 합니다. iPhone을 움직이거나 잠금을 풀면 영상이 잠시 멈출 수 있습니다. 프로그램은 최대 10초 기다린 뒤 이어서 동작합니다.
- 처음 실행할 때 터미널 앱에 카메라 권한을 허용해야 합니다.
- iPad는 Mac 웹캠(Continuity Camera)으로 쓸 수 없습니다.
- MediaPipe는 수업 노트북처럼 `--no-deps`로 설치합니다. 의존성을 함께 설치하면 `opencv-contrib-python`이 따라 들어와 기존 OpenCV와 충돌합니다.

```bash
python -m pip install --no-deps mediapipe
python -m pip install absl-py flatbuffers sounddevice certifi
```

**폰트**
- 한글 폰트는 Noto CJK, 나눔고딕, 맑은 고딕, Apple SD Gothic Neo의 일반 설치 경로에서 찾습니다.
- 없으면 `--font /경로/한글폰트.ttf`를 지정합니다. Ubuntu의 Noto CJK 패키지는 `fonts-noto-cjk`입니다.

## 천지인 조합 (`cheonjiin.py`)

모든 입력기가 같은 조합기(`CheonjiinIME`)를 씁니다.
- 지원 범위: 현대 한글 모음 21개, 자음 순환, 초성·중성·종성 조합, 겹받침, 다음 모음으로 받침 이동
- 아래아 `ㆍ`와 `ㆍㆍ`는 모음 조합 중에 임시로 표시됩니다.
- 지움은 마지막 키 입력 한 번을 취소합니다. 예를 들어 `가`에서 한 번 지우면 `기`, 다시 지우면 `ㄱ`이 됩니다.

| 입력 | 결과 |
| --- | --- |
| ㄱ + ㅣ + ㆍ | 가 |
| ㅇ + ㅣ + ㆍ + ㄴ | 안 |
| ㄱ + ㆍ + ㅡ + ㅣ + ㆍ | 과 |
| ㅡ + ㆍ + ㆍ + ㅣ | ㅝ |

## 수업 내용과 연결

| 수업 기술 | 적용 |
| --- | --- |
| 이미지 조정 | 좌우 반전(`pinch_cheonjiin.py --mirror`, `project01.py`는 기본 반전), `--flip-vertical` 상하 반전(`project01.py`) |
| 색공간 변환 | MediaPipe 입력용 BGR→RGB. `project01.py`는 명도·피부색 처리에 LAB 사용 |
| Image Enhancement·필터링 | `project01.py`의 `E`: LAB L 채널 CLAHE와 Gaussian blur |
| Color Segmentation·형태학 연산·Contour | `project01.py`의 `M`: 손바닥 샘플의 LAB a/b 범위로 손 영역 마스크, opening/closing, 외곽선 표시 |
| 객체 추적·신체 인식 | MediaPipe VIDEO 모드(`detect_for_video`)로 손 landmark 추적 |
| Kalman Filter | `pinch_typer.Kalman1D`: 손등 마우스 커서의 떨림 제거(`docs/wiki/Kalman-Filter.md`의 예측·보정 단계) |
| 관계성과 결과 표현 | 엄지 끝~끝마디 선분 거리로 꼬집기 판정, 손 방향(외적 부호)으로 키보드·마우스 전환, HUD와 한글 텍스트 |

MediaPipe VIDEO 모드와 `detect_for_video`는 [공식 Python 안내](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker/python)의 API를 사용합니다.

## 이전 시도

세 방식 모두 코드와 테스트가 남아 있고, 각각의 커밋에 경위가 적혀 있습니다.

| 방식 | 파일 | 결과 |
| --- | --- | --- |
| 엄지로 고른 키를 주먹으로 확정 (Jetson 원본) | 커밋 `767a19f`의 `project01.py` | 글자마다 손을 폈다 쥐어야 해서 입력이 거의 불가능했다. |
| 엄지로 마디를 누르면 입력, 주먹은 지움 | `project01.py`, `tap_*.py` | 단안 카메라로는 엄지가 마디에 닿았는지 판정하지 못해 iPhone 실기 시험에서 입력되지 않았다. |
| 자판 손 버튼을 선택 손으로 꼬집기 (방식 1) | `pinch_keyboard.py`, `pinch_detect.py` | 입력은 되었지만(`ㅂㅂㅇㅈ`), 맞은편 카메라 배치에서는 사용자 시점과 화면이 달라 조작하기 어려웠다. |

**엄지 마디 누르기 (`project01.py`)**
- 키는 검지~소지 4개 손가락 × 끝·중간·뿌리 마디 3개, 모두 12곳의 중점에 놓입니다.
- 엄지 끝과 마디 선분 사이 거리를 MediaPipe world 좌표로 재고, 12mm 안이면 누른 것으로 봅니다(`--tap-enter`, `--tap-exit`, 실행 중 `[` `]`).
- 주먹은 지움입니다.
- `python 261018/project01.py --camera 0`으로 실행하고, `--demo`로 카메라 없이 `안녕하세요` 조합을 확인합니다.
- `tap_record.py`·`tap_eval.py`는 판정 방식을 비교하는 도구입니다. 안내에 따라 누르기·근접·띄우기·주먹 시행을 기록하고, 검출기별 정밀도·재현율·키 정확도·혼동 행렬을 냅니다. 카메라를 고정한 상태에서 기록해야 합니다. 세션은 `261018/data/tap/`에 저장되며 git에 포함하지 않습니다.

**선택 손 꼬집기 (`pinch_keyboard.py`)**
- 왼손 손바닥을 자판으로 펴고, 오른손 엄지·검지 중점을 커서로 키 위에 둔 뒤 맞닿으면 입력합니다.
- 오므리기 시작할 때 강조된 키를 고정해 입력하고, 두 손이 겹치면 자판 손 위치를 1.5초까지 유지합니다.
- 왼손 주먹은 지움입니다.
- `python 261018/pinch_keyboard.py --camera 0`으로 실행합니다.

## 검증

```bash
python -m unittest discover -s 261018 -p 'test_*.py' -v
python 261018/pinch_cheonjiin.py --demo
python 261018/project01.py --demo
```

모든 테스트는 카메라 없이 돌아갑니다.

| 테스트 | 검증 내용 |
| --- | --- |
| `test_pinch_typer.py` | 고정 데이터(`fixtures/pinch-typer-keys.json`) 재생, 손가락별 꼬집기·순환·쉬프트·주먹, 손 방향, 손 배정, 마우스 제스처 |
| `test_project01.py` | 한글 조합, 12개 마디 누르기, 띄운 엄지 무시, 주먹 지움, 손 유실·양손 독립, 편 손/주먹 판정 |
| `test_tap.py` | 마디 기하, 누르기 검출기 상태 머신, 시행 판정 지표, 세션 저장·불러오기 |
| `test_pinch.py` | 꼬집기 판정과 키 고정, 손 역할 배정, 겹칠 때 자판 위치 유지, 주먹 지움 |

실제 카메라에서는 꼬집기 비율 분포, 손 방향 판정, 거리·조명 변화, 손 가림을 따로 확인해야 합니다. `pinch_cheonjiin.py --log`로 손 신호를 기록하면 판정값을 다시 정할 수 있습니다.
