"""손 카메라 입력기들이 함께 쓰는 공통 모듈.

앱은 `python 261018/<앱>.py`로 실행하므로 261018 디렉터리가 import 경로에 들어간다.
그래서 별도 설치 없이 `from common.camera import open_capture`처럼 쓸 수 있다(Jetson에서도 같다).

모듈 지도
  landmarks  MediaPipe 손 landmark 번호, 마디 선분, 좌표 변환과 거리 계산      (표준 라이브러리)
  pose       편 손·접는 중·주먹 판정, 손바닥/손등 방향                         (표준 라이브러리)
  gestures   시간에 따라 상태가 바뀌는 판정기: 머무름, 주먹 유지, 방향, 칼만 필터 (표준 라이브러리)
  hands      MediaPipe 손 검출, 좌우 라벨 규칙, 왼손·오른손 배정                (mediapipe·OpenCV)
  camera     카메라 열기, 프레임 끊김 대기, 해상도, 창, 시각·fps                 (OpenCV)
  hud        글꼴, 글자, 유리 버튼, 흐림 처리를 묶은 HUD 그리기 도구             (Pillow·numpy·OpenCV)
  hand_keys  손가락 마디 12칸 자판과 그 자판을 손 위에 그리는 HUD               (Pillow)
  cli        모든 앱이 쓰는 실행 옵션(--camera, --pipeline, --model, --font)
  synthetic  테스트용 합성 손(편 손, 주먹, 꼬집기 궤적)                          (numpy)

외부 패키지는 그 패키지가 필요한 함수 안에서 import한다. 그래서 `--demo`와 상태 머신 테스트는
OpenCV·MediaPipe 없이도 돈다. 이 파일도 아무것도 import하지 않는다.
"""
