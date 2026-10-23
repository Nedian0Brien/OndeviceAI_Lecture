import Foundation

/// 내장 Python(261018/pinch_vision.py의 Session)을 부르는 창구. Python 호출은 이 직렬 큐 하나에서만 한다.
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
        var hold: Double
    }

    struct Hand: Decodable {
        var face: String?
        var occluded: Bool
        var armed: Bool
        var fist: Double
        var map: [String?]
        var fingers: [Finger]
        var dragging: Int?  // 모음 버튼을 꼬집는 중인 손가락
        var dragFrames: Int  // 판정에 쓰는 이번 접촉의 프레임 수(이 손이 보인 마지막 프레임들)
        var dragRadius: Double  // 끌기 기준 거리(m)
        var dragPreview: String?  // 지금 떼면 들어갈 획
        var dragPassed: Bool  // 끌기 기준을 넘었는가
    }

    var text: String?
    var pending: Bool?
    var pendingPunct: Bool?
    var group: [String]?
    var groupIndex: Int?
    var holdMode: Bool?
    var hands: [String: Hand]?
    var settings: RuleSettings?
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

/// 판정 규칙 켜기·끄기(설정 창). 판정 Python의 pinch_vision.Session.apply_settings로 넘기고, 기기에 저장한다.
struct RuleSettings: Codable, Equatable {
    var hold = false  // 꼬집고 0.3초 머물러야 입력(누르고 유지)
    var settle = true  // 손을 뒤집은 직후 0.3초는 꼬집기를 받지 않음
    var indexSweep = true  // 오른손 검지 지나감 규칙(엄지가 검지를 스치거나 돌아오며 지나간 접촉을 버림)
    var occlusion = true  // 엄지 가림 규칙(소지 쪽으로 25° 넘게 기울면 손바닥 꼬집기를 받지 않음)

    private static let key = "PinchKeyboard.RuleSettings"

    static func load() -> RuleSettings {
        guard let data = UserDefaults.standard.data(forKey: key),
              let settings = try? JSONDecoder().decode(RuleSettings.self, from: data) else {
            return RuleSettings()
        }
        return settings
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: Self.key)
        }
    }

    /// pinch_vision 설정 명령(JSON).
    var command: String {
        #"{"cmd": "settings", "values": {"hold": \#(hold), "settle": \#(settle), "index_sweep": \#(indexSweep), "occlusion": \#(occlusion)}}"#
    }
}
