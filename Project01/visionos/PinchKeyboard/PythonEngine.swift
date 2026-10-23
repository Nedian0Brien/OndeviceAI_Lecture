import Foundation

/// 내장 Python(Project01/visionos/pinch_vision.py의 Session)을 부르는 창구. Python 호출은 이 직렬 큐 하나에서만 한다.
final class PythonEngine: @unchecked Sendable {
    private let queue = DispatchQueue(label: "pinch-keyboard.python")
    private var started = false

    /// Python을 시작하고 판정 세션을 만든다. 실패하면 오류 문구.
    func start(logDirectory: URL) async -> String? {
        await withCheckedContinuation { continuation in
            queue.async {
                if self.started {
                    continuation.resume(returning: nil)
                    return
                }
                let resources = Bundle.main.resourcePath ?? ""
                let error = pyhost_start(resources, logDirectory.path)
                if let error {
                    continuation.resume(returning: String(cString: error))
                    free(error)
                } else {
                    self.started = true
                    continuation.resume(returning: nil)
                }
            }
        }
    }

    /// session.<method>(json)의 결과 JSON.
    func call(_ method: String, _ json: String) async -> String {
        await withCheckedContinuation { continuation in
            queue.async {
                guard let result = pyhost_call(method, json) else {
                    continuation.resume(returning: #"{"error": "Python 호출 결과가 없습니다"}"#)
                    return
                }
                continuation.resume(returning: String(cString: result))
                free(result)
            }
        }
    }
}

/// pinch_vision.Session.step / command가 돌려주는 상태(모양은 pinch_vision.py 모듈 설명). 키는 snake_case로 온다.
struct PinchState: Decodable {
    struct Finger: Decodable {
        var queue: [String]
        var active: Bool
        var approach: Double
        var release: Double
    }

    struct Hand: Decodable {
        var face: String?
        var occluded: Bool
        var armed: Bool
        var fist: Double
        var closed: Bool  // 주먹 자세(손끝 버튼을 접는다)
        var map: [String?]
        var fingers: [Finger]
        var dragging: Int?  // 모음 버튼을 꼬집는 중인 손가락
        var dragFrames: Int  // 판정에 쓰는 이번 접촉의 프레임 수(이 손이 보인 마지막 프레임들)
        var dragRadius: Double  // 끌기 기준 거리(m)
        var dragPreview: String?  // 지금 떼면 들어갈 획
        var dragPassed: Bool  // 끌기 기준을 넘었는가
        var dragPointer: Double?  // 끈 방향 각도(12시 0°에서 시계 방향)
    }

    /// 마지막으로 입력된 것(손바닥 가운데에 잠깐 띄운다). id가 바뀌면 새 입력.
    struct Echo: Decodable, Equatable {
        var id: Int
        var hand: String
        var text: String
    }

    /// 단어 타이핑 모드 상태(pinch_vision.Session.word_state).
    struct WordState: Decodable {
        var target: String?  // 지금 칠 단어(맞혔으면 그 단어, 건너뛰고 쉬는 중이면 nil)
        var solved: Bool  // 맞혀서 "다음 단어"를 기다리는 중
        var matched: Int  // 입력 칸 앞에서부터 맞은 글자 수
        var last: [String]?  // [직전 단어, "done" | "skip"]
        var done: Int
        var skipped: Int
        var seconds: Double?  // 다 친 단어의 평균 시간(초)
    }

    var text: String?
    var echo: Echo?
    var mode: String?  // "free" | "words"
    var word: WordState?
    var pending: Bool?
    var pendingPunct: Bool?
    var group: [String]?
    var groupIndex: Int?
    var hands: [String: Hand]?
    var log: String?
    var notice: String?
    var error: String?

    static func decode(_ json: String) -> PinchState? {
        guard let data = json.data(using: .utf8) else {
            return nil
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(PinchState.self, from: data)
    }
}
