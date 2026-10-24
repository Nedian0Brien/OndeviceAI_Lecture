// 앱에 넣은 Python(BeeWare Python-Apple-support)을 시작하고 판정 세션(pinch_vision.Session)을 부르는 C 함수.
// Swift는 브리징 헤더로 이 두 함수만 쓴다. 돌려받은 문자열은 호출한 쪽이 free()로 해제한다.
#ifndef PYTHON_HOST_H
#define PYTHON_HOST_H

/// 내장 Python을 시작하고 pinch_vision.Session(log_dir)을 만든다. 성공하면 NULL, 실패하면 오류 문구.
/// resource_path: 앱 번들의 리소스 폴더(그 안의 python/가 PYTHONHOME, app/이 판정 Python 파일).
char *pyhost_start(const char *resource_path, const char *log_dir);

/// session.<method>(json_text)를 부르고 결과 JSON 문자열을 돌려준다. Python 예외면 {"error": "트레이스백"}.
/// 어느 스레드에서 불러도 되지만, 앱은 한 직렬 큐에서만 부른다.
char *pyhost_call(const char *method, const char *json_text);

#endif
