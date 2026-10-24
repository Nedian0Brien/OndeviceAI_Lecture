# PinchKeyboard Vision Pro 시험 앱

PinchKeyboard(`Project01/main.py`, 카메라 앱)를 Vision Pro에서 돌린다. 1인칭 카메라를 시야 근처에
고정할 수 없어서, Vision Pro의 손 추적으로 1인칭 시점 판정을 시험하고 기록을 모으려고 만든 개발·시험용 확장이다.
판정은 Swift로 다시 쓰지 않고, 앱에 넣은 Python이 `Project01/`의 판정 모듈을 그대로 실행한다.

## 구성

| 쪽 | 파일 | 하는 일 |
|---|---|---|
| Swift | `PinchKeyboard/HandTracker.swift` | ARKit 손 관절 27개 → MediaPipe 21점 순서. `world`(월드 m)와 `image`(머리 기준, x 오른쪽·y 아래·z 멀어지는 쪽) 두 벌 |
| Swift | `PinchKeyboard/AppModel.swift` | 약 45Hz로 관절을 Python에 넘기고 결과(입력 글자·손끝 라벨)를 받는다. 손끝 라벨 위치 |
| Swift | `ContentView.swift` | 입력 창(visionOS 기본 모양): 첫 화면은 제목, 모드 버튼 두 개(자유 입력 / 단어 입력), 짧은 설명, 설정. 모드 버튼을 누르면 그 모드로 손 입력이 시작되고 입력 화면(단어 입력이면 칠 단어·진행, 입력된 글자, 끝내기·지우기·건너뛰기·설정)으로 바뀐다. 자판은 카메라 앱과 같다(`../README.md` 자판 절) |
| Swift | `ImmersiveView.swift`, `HandLayout.swift`, `HUDStyle.swift` | 몰입 공간: 카메라 앱 화면(`../hud.py`)의 손 위 요소를 선 HUD(가는 선·눈금·파랑 강조)로(손끝 버튼과 다가옴·떼기 진행 눈금, 순환 대기 글자와 올라오는 애니메이션, 손바닥 원형 표시: 다가옴 막대·누르는 손가락 삼각형·주먹 지움 막대 링, 모음 끌기 궤적·기준 원·획 미리 보기, 주먹 쥘 때 접기·펼치기. 안내 표시를 켜면 골격선·손 이름). 손바닥 원형 표시는 머리 기준 이미지 좌표로 카메라 앱과 같은 계산을 한다(`HandLayout.hub`). 끌기 궤적은 판정 Python이 알려 준 프레임 수(`drag_frames`)만큼 같은 프레임의 관절로 그린다. 색·크기 비율·배치 규칙은 `hud.py`와 같다 |
| Swift | `SettingsView.swift` | 설정 창: 손 골격·안내 표시, 손 가림 선택. 판정 규칙은 카메라 앱과 같은 값으로 고정이다 |
| C | `PinchKeyboard/PythonHost.c` | 내장 Python 시작(`pyhost_start`)·호출(`pyhost_call`) |
| Python | `pinch_vision.py` | `Session.step(JSON) → JSON`: `keyboard.hand_signal` → `PinchTyper.update` → 상태. 단어 모드, 기록 |
| Python | `../keyboard.py`, `../record.py` | 카메라 앱과 같은 판정·조합·단어 시험. 기기 안 Python에는 OpenCV·MediaPipe가 없어서 두 파일은 표준 라이브러리만 import한다 |

빌드 단계 "Install Python"이 위 Python 파일과 `../fixtures/pinch-typer-keys.json`을 번들 `app/`에 복사하고,
`vendor/Python.xcframework/build/utils.sh`의 `install_python`으로 표준 라이브러리를 넣는다.
판정을 고치면 `Project01/`의 Python 파일만 고치고 다시 빌드하면 된다.

## 준비

```bash
Project01/visionos/fetch-python.sh   # BeeWare Python-Apple-support 3.14-b13 visionOS 패키지(약 28MB, SHA-256 확인)를 vendor/에 받는다
```

`vendor/`와 `build/`는 저장소에 넣지 않는다.

## 빌드 확인(서명 없이)

```bash
cd Project01/visionos
xcodebuild -project PinchKeyboard.xcodeproj -scheme PinchKeyboard -destination 'generic/platform=visionOS' \
  -derivedDataPath build/DerivedData CODE_SIGNING_ALLOWED=NO build
```

## 기기에 설치

1. Xcode > Settings > Accounts에서 Apple ID를 추가한다.
2. `PinchKeyboard.xcodeproj`를 열고 PinchKeyboard 타깃 > Signing & Capabilities에서 Team을 고른다.
   번들 ID(`com.minjaepark.pinchkeyboard`)가 이미 쓰인다고 나오면 바꾼다.
3. Vision Pro와 Mac을 같은 Wi-Fi에 두고, Vision Pro의 설정 > 일반 > 원격 기기에서 Mac과 페어링한다
   (Xcode > Window > Devices and Simulators에 뜨는 코드를 입력).
4. Xcode 위쪽 실행 대상에서 Vision Pro를 고르고 실행(⌘R). 개발자 모드를 켜라고 나오면 Vision Pro의 설정 >
   개인정보 보호 및 보안 > 개발자 모드를 켜고 다시 시작한다.
5. 처음 실행하면 손 추적 권한을 묻는다. 허용한다.

## 쓰기

- 창의 "자유 입력" 또는 "단어 입력" → 몰입 공간이 열리고 손끝마다 다음 글자 라벨이 뜬다. "끝내기"로 첫 화면에 돌아온다. 자판과 규칙은 `Project01/README.md`와 같다.
- 자유 입력: 마음대로 친다. 단어 입력: 짧은 단어(`record.py`의 `SHORT_WORDS` 60개, 모든 자음 그룹·복합 모음 포함)를 무작위로 하나씩 내고, 입력 칸이 그 단어가 되면 창이 초록이 되고 "다음 단어" 버튼이 뜬다(누르면 입력 칸을 비우고 다음 단어). 틀리면 주먹으로 지우거나 "지우기"로 처음부터, "건너뛰기"로 다음 단어. 단어 모드 기록(`…-words.jsonl`)은 카메라 앱 `--words` 기록과 같은 형식이라 `main.py --evaluate`가 기대 키 순서와 맞춰 오입력을 센다.
- 내장 Python이 도는지는 앱 시작 때 콘솔에 남는 자기 점검(`PinchKeyboard: Python … 고정 데이터 재생 '안녕하세요'`)으로 확인한다.
- 기록: 손 입력 중에는 늘 프레임별 손 신호를 앱 Documents의 `pinch-log-visionos-날짜-시각.jsonl`에 남긴다(손 입력
  한 번이 파일 하나, 1분에 약 8MB, 최근 20개만 남김). Mac에서 가져와 `python Project01/main.py --evaluate 기록.jsonl`로 다시 본다:
  `xcrun devicectl device copy from --device <기기> --domain-type appDataContainer --domain-identifier com.minjaepark.pinchkeyboard --source Documents/<파일> --destination <경로>`

## 알려진 한계

- 손 추적은 Full Space(몰입 공간)에서만 되고, 시뮬레이터에서는 동작하지 않는다.
- visionOS는 손바닥을 보며 꼬집으면 홈 화면을 연다. 앱이 `.persistentSystemOverlays(.hidden)`으로 숨겨 달라고
  요청하지만 시스템이 따르지 않을 수 있다(Apple 포럼 답변). 지금 자판(손바닥을 보며 꼬집기)과 겹치는지 기기에서 확인한다.
- 판정값은 카메라 앱과 같은 값이다. ARKit은 실측 3D라 MediaPipe와 값의 범위가 다를 수 있다.
