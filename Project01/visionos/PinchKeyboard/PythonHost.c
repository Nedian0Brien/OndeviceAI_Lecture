// 내장 Python 시작·호출. 설정은 Python-Apple-support 테스트베드(vendor/testbed)와 CPython "Using Python on iOS"의
// 요구 사항을 따른다: isolated 설정, UTF-8 모드, 시스템 로그, 버퍼 없는 stdio, 바이트코드 쓰지 않음(서명된 번들은
// 고칠 수 없다), 시그널 처리기 설치, PYTHONHOME = 번들의 python/.
#include <Python/Python.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "PythonHost.h"

static PyObject *session = NULL;  // pinch_vision.Session

static char *copy_text(const char *text) {
    size_t size = strlen(text) + 1;
    char *out = malloc(size);
    if (out != NULL) {
        memcpy(out, text, size);
    }
    return out;
}

// 걸려 있는 Python 예외를 {"error": "트레이스백"} JSON으로 바꾸고 지운다(GIL을 쥔 상태에서 부른다).
static char *exception_json(void) {
    PyObject *exc = PyErr_GetRaisedException();
    PyObject *traceback = PyImport_ImportModule("traceback");
    PyObject *json = PyImport_ImportModule("json");
    PyObject *lines = (traceback && exc) ? PyObject_CallMethod(traceback, "format_exception", "O", exc) : NULL;
    PyObject *empty = PyUnicode_FromString("");
    PyObject *text = (lines && empty) ? PyUnicode_Join(empty, lines) : NULL;
    PyObject *dict = PyDict_New();
    if (dict && text) {
        PyDict_SetItemString(dict, "error", text);
    }
    PyObject *dumped = (json && dict && text) ? PyObject_CallMethod(json, "dumps", "O", dict) : NULL;
    const char *utf8 = dumped ? PyUnicode_AsUTF8(dumped) : NULL;
    char *out = copy_text(utf8 ? utf8 : "{\"error\": \"Python 오류(내용을 읽지 못함)\"}");
    PyErr_Clear();
    Py_XDECREF(dumped);
    Py_XDECREF(dict);
    Py_XDECREF(text);
    Py_XDECREF(empty);
    Py_XDECREF(lines);
    Py_XDECREF(json);
    Py_XDECREF(traceback);
    Py_XDECREF(exc);
    return out;
}

static char *status_text(const char *step, PyStatus status) {
    char buffer[512];
    snprintf(buffer, sizeof buffer, "%s: %s", step, status.err_msg ? status.err_msg : "알 수 없는 오류");
    return copy_text(buffer);
}

char *pyhost_start(const char *resource_path, const char *log_dir) {
    if (session != NULL) {
        return NULL;
    }
    PyStatus status;
    PyPreConfig preconfig;
    PyConfig config;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;
    status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) {
        return status_text("Python 사전 초기화", status);
    }

    PyConfig_InitIsolatedConfig(&config);
    config.use_system_logger = 1;
    config.buffered_stdio = 0;
    config.write_bytecode = 0;
    config.install_signal_handlers = 1;
    char home[PATH_MAX];
    snprintf(home, sizeof home, "%s/python", resource_path);
    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (!PyStatus_Exception(status)) {
        status = PyConfig_Read(&config);
    }
    if (!PyStatus_Exception(status)) {
        status = Py_InitializeFromConfig(&config);
    }
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        return status_text("Python 초기화", status);
    }

    // 판정 Python 파일(번들 app/)을 import 경로 맨 앞에 둔다.
    char app[PATH_MAX];
    snprintf(app, sizeof app, "%s/app", resource_path);
    char *error = NULL;
    PyObject *sys_path = PySys_GetObject("path");  // 빌린 참조
    PyObject *app_text = PyUnicode_FromString(app);
    if (sys_path == NULL || app_text == NULL || PyList_Insert(sys_path, 0, app_text) != 0) {
        error = exception_json();
    }
    Py_XDECREF(app_text);
    if (error == NULL) {
        PyObject *module = PyImport_ImportModule("pinch_vision");
        if (module != NULL) {
            session = PyObject_CallMethod(module, "Session", "s", log_dir);
            Py_DECREF(module);
        }
        if (session == NULL) {
            error = exception_json();
        }
    }
    PyEval_SaveThread();  // GIL을 놓는다: 이후 호출은 pyhost_call이 PyGILState로 다시 쥔다.
    return error;
}

char *pyhost_call(const char *method, const char *json_text) {
    if (session == NULL) {
        return copy_text("{\"error\": \"Python 판정 세션이 시작되지 않았습니다\"}");
    }
    PyGILState_STATE gil = PyGILState_Ensure();
    PyObject *result = PyObject_CallMethod(session, method, "s", json_text);
    char *out;
    if (result == NULL) {
        out = exception_json();
    } else {
        const char *utf8 = PyUnicode_AsUTF8(result);
        out = utf8 ? copy_text(utf8) : exception_json();
        Py_DECREF(result);
    }
    PyGILState_Release(gil);
    return out;
}
