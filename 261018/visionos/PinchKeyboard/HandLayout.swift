import CoreGraphics
import Foundation
import simd

/// 손 하나의 HUD 배치(3D, m). Mac 앱 draw_hud의 이미지 좌표 규칙을 손 관절 3D 위치에 그대로 적용한다.
struct HandLayout {
    static let fingerTips = [8, 12, 16, 20]
    static let fingerMCPs = [5, 9, 13, 17]
    /// MediaPipe 손 골격 연결선(common/landmarks.py HAND_CONNECTIONS).
    static let connections: [(Int, Int)] = {
        let chains = [[0, 1, 2, 3, 4], [0, 5, 6, 7, 8], [5, 9, 10, 11, 12], [9, 13, 14, 15, 16], [13, 17, 18, 19, 20],
                      [0, 17]]
        return chains.flatMap { chain in zip(chain, chain.dropFirst()).map { ($0, $1) } }
    }()
    /// 손끝 라벨 지름 범위(m). Mac: min(26, max(15, 손끝 간격 × 0.9)) px의 비율을 따른다.
    /// 2026-10-23 사용자 "전반적으로 사이즈를 줄여 봐": Mac과 같은 비율로 줄였다(이전 1.27~2.2cm, 간격 × 0.9).
    static let maxDiameter: Float = 0.0178
    static let minDiameter: Float = 0.0102

    let points: [SIMD3<Float>]  // 21점 월드 위치
    let diameter: Float  // 손끝 라벨 지름(m)

    init(points: [SIMD3<Float>]) {
        self.points = points
        let tips = Self.fingerTips.map { points[$0] }
        let spacing = zip(tips, tips.dropFirst()).map { simd_distance($0, $1) }.min() ?? 0.02
        diameter = min(Self.maxDiameter, max(Self.minDiameter, spacing * 0.75))
    }

    /// 손가락 끝마디 방향(DIP → TIP) 단위 벡터.
    func tipDirection(_ finger: Int) -> SIMD3<Float> {
        let tip = points[Self.fingerTips[finger]]
        let dip = points[Self.fingerTips[finger] - 1]
        let direction = tip - dip
        return simd_length(direction) > 1e-6 ? simd_normalize(direction) : SIMD3(0, 1, 0)
    }

    /// 손끝 라벨 중심: 끝마디 방향으로 손끝 바로 바깥(지름 × 0.85). 엄지가 닿는 면을 가리지 않는다.
    func labelCenter(_ finger: Int) -> SIMD3<Float> {
        points[Self.fingerTips[finger]] + tipDirection(finger) * diameter * 0.85
    }

    func tip(_ finger: Int) -> SIMD3<Float> {
        points[Self.fingerTips[finger]]
    }

    /// 손가락 위 경로(fingerRoute)에서 길이 비율 fraction의 점(Mac finger_path_point).
    func pathPoint(_ finger: Int, fraction: Float) -> SIMD3<Float> {
        let path = fingerRoute(finger)
        let lengths = zip(path, path.dropFirst()).map { simd_distance($0, $1) }
        var remaining = max(0, min(1, fraction)) * lengths.reduce(0, +)
        for (index, length) in lengths.enumerated() {
            if remaining <= length {
                let k = length > 0 ? remaining / length : 0
                return path[index] + (path[index + 1] - path[index]) * k
            }
            remaining -= length
        }
        return path[path.count - 1]
    }

    /// 손 이름 자리: 손목에서 팔 쪽으로 조금(Mac: 손목 아래 12px).
    var captionPoint: SIMD3<Float> {
        let away = points[0] - points[9]
        let direction = simd_length(away) > 1e-6 ? simd_normalize(away) : SIMD3(0, -1, 0)
        return points[0] + direction * 0.02
    }

    /// 손목 → 중지 뿌리 방향 단위 벡터.
    var forward: SIMD3<Float> {
        let direction = points[9] - points[0]
        return simd_length(direction) > 1e-6 ? simd_normalize(direction) : SIMD3(0, 1, 0)
    }

    /// 주먹 진행 자리: 손목~중지 뿌리 가운데.
    var palmCenter: SIMD3<Float> {
        (points[0] + points[9]) / 2
    }

    static let palmIndices = [0, 5, 9, 13, 17]

    /// 손바닥 원형 표시 중심: 손목·네 손가락 뿌리의 평균(월드).
    var hubCenter: SIMD3<Float> {
        Self.palmIndices.map { points[$0] }.reduce(.zero, +) / Float(Self.palmIndices.count)
    }

    /// 손바닥 평면 좌표계: 손바닥 법선(z, 사용자 쪽 면), 손목 → 중지 뿌리(y, 위), x = y × z(오른쪽).
    /// 손바닥 원형 표시는 이 평면에 눕혀 그린다. 손을 돌리거나 기울이면 원도 같이 돌고 기운다
    /// (2026-10-23 사용자: 원형 표시가 늘 사용자를 향해 손을 돌리거나 뒤집을 때 어색했다).
    /// 손등이 사용자 쪽이면 법선을 뒤집어 사용자 쪽 면에 그린다(뒤에서 보이거나 거울상이 되지 않게).
    func palmFrame(head: SIMD3<Float>) -> PalmFrame {
        let center = hubCenter
        var normal = simd_cross(points[5] - points[0], points[17] - points[0])
        normal = simd_length(normal) > 1e-9 ? simd_normalize(normal) : SIMD3(0, 0, 1)
        if simd_dot(normal, head - center) < 0 {
            normal = -normal
        }
        var up = points[9] - points[0]
        up -= simd_dot(up, normal) * normal
        up = simd_length(up) > 1e-9 ? simd_normalize(up) : SIMD3(0, 1, 0)
        let right = simd_cross(up, normal)
        return PalmFrame(center: center, right: right, up: up, normal: normal)
    }

    /// 손가락 경로: 손끝 버튼 자리 → TIP → DIP → PIP(Mac finger_path_point). 끝(순환 대기열 마지막 글자 자리)을 PIP로
    /// 둔다. MCP 쪽으로 내리면 손바닥 원과 겹쳐 글자가 잘렸다(2026-10-23 사용자).
    func fingerRoute(_ finger: Int) -> [SIMD3<Float>] {
        let mcp = Self.fingerMCPs[finger]
        return [labelCenter(finger)] + [3, 2, 1].map { points[mcp + $0] }
    }

    /// 손바닥 원형 표시 형태(Mac draw_palm_hub와 같은 구성). 손바닥 평면 좌표(m, x 오른쪽·y 아래)로 계산한다.
    /// spread: 손끝 버튼 펼침(0~1). waiting: 손가락별 대기 글자 수(대기 글자 자리를 비우는 데 쓴다, 최대 2).
    func hub(spread: Float, waiting: [Int] = [0, 0, 0, 0], head: SIMD3<Float>) -> PalmHub {
        let frame = palmFrame(head: head)
        let mid = frame.flat(palmCenter)  // 펼침 원점(손목~중지 뿌리 가운데, Mac palm)
        func placed(_ p: SIMD3<Float>) -> CGPoint {
            let q = mid + (frame.flat(p) - mid) * spread
            return CGPoint(x: CGFloat(q.x), y: CGFloat(q.y))
        }
        var slots: [CGPoint] = []
        for finger in 0..<4 {
            let count = min(2, waiting[safe: finger] ?? 0)
            if count > 0 {
                for slot in 1...count {
                    slots.append(placed(pathPoint(finger, fraction: Float(slot) / Float(count))))
                }
            }
        }
        let homes = (0..<4).map { placed(labelCenter($0)) }
        let wristPoint = frame.flat(points[0])
        let wrist = PalmHub.bearing(CGPoint(x: CGFloat(wristPoint.x), y: CGFloat(wristPoint.y)))
        let away = wrist + 180  // 손가락 쪽 가운데: 각도를 이 둘레로 펴서 0°/360° 경계를 넘지 않게 한다
        let angles = Self.fingerMCPs.map { mcp -> Double in  // 손가락 방향 = MCP 쪽
            let q = frame.flat(points[mcp])
            let raw = PalmHub.bearing(CGPoint(x: CGFloat(q.x), y: CGFloat(q.y))) - away + 180
            return away + (raw.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360) - 180
        }
        let size = CGFloat(simd_distance(frame.flat(points[0]), frame.flat(points[9])))
        return PalmHub(size: size, wrist: wrist, angles: angles, homes: homes, slots: slots)
    }
}

/// 손바닥 평면 좌표계(월드). 원형 표시 첨부 뷰는 center에 놓고 rotation으로 돌린다(뷰의 앞면 +z = normal).
struct PalmFrame {
    var center: SIMD3<Float>
    var right: SIMD3<Float>
    var up: SIMD3<Float>
    var normal: SIMD3<Float>

    var rotation: simd_quatf {
        simd_quatf(simd_float3x3(columns: (right, up, normal)))
    }

    /// 원형 표시를 놓을 자리: 손바닥 관절 평균에서 사용자 쪽으로 6mm(관절 위치는 손바닥 피부 안쪽이다).
    var surface: SIMD3<Float> {
        center + normal * 0.006
    }

    /// 월드 점 → 평면 좌표(m, 가운데 기준, x 오른쪽·y 아래: SwiftUI 캔버스와 같은 방향).
    func flat(_ p: SIMD3<Float>) -> SIMD2<Float> {
        let d = p - center
        return SIMD2(simd_dot(d, right), -simd_dot(d, up))
    }
}

/// 손바닥 원형 표시 형태(손바닥 평면 좌표, m).
struct PalmHub {
    var size: CGFloat  // 손 길이(손목~중지 뿌리, m)
    var wrist: Double  // 가운데에서 손목 방향(12시 0°에서 시계 방향)
    var angles: [Double]  // 손가락별 MCP 방향(손가락 쪽 둘레로 편 각도)
    var homes: [CGPoint]  // 손끝 버튼 자리(가운데 기준, m, 펼침 반영)
    var slots: [CGPoint]  // 대기 글자 자리(가운데 기준, m, 펼침 반영)

    /// 가운데에서 offset 방향 각도(12시 0°에서 시계 방향, y 아래).
    static func bearing(_ offset: CGPoint) -> Double {
        let degrees = atan2(Double(offset.x), Double(-offset.y)) * 180 / .pi
        return (degrees + 360).truncatingRemainder(dividingBy: 360)
    }
}

/// 손끝 버튼 접기·펼치기(Mac spread_motion과 같은 값): 주먹이면 0.15초에 손바닥 가운데로 모여 사라지고, 편 뒤
/// 0.15초가 지나면 0.35초에 손끝 자리로 펼쳐진다. 처음 보이는 손은 0에서 시작한다.
struct SpreadMotion {
    static let open = 0.35
    static let close = 0.15
    static let hold = 0.15
    var value = 0.0  // 선형 진행
    var last: Double? = nil
    var closedAt: Double? = nil

    /// 이번 프레임의 펼침 정도(ease-out, 0~1).
    mutating func update(closed: Bool, now: Double) -> Double {
        if closed {
            closedAt = now
        }
        let step = max(0, now - (last ?? now))
        if let closedAt, now - closedAt < Self.hold {
            value = max(0, value - step / Self.close)
        } else {
            value = min(1, value + step / Self.open)
        }
        last = now
        return 1 - pow(1 - value, 3)
    }
}

/// 입력 표시(Mac echo_look과 같은 값): 0.08초에 나타나 0.25초까지 머물고 0.6초까지 커지며 사라진다.
enum EchoMotion {
    static let fadeIn = 0.08
    static let hold = 0.25
    static let end = 0.6

    /// (불투명도, 배율). 끝났으면 nil.
    static func look(age: Double) -> (Double, Double)? {
        guard age >= 0, age < end else {
            return nil
        }
        if age < fadeIn {
            return (age / fadeIn, 0.85 + 0.15 * age / fadeIn)
        }
        if age < hold {
            return (1, 1)
        }
        let out = (age - hold) / (end - hold)
        return (1 - out, 1 + 0.15 * out)
    }
}

/// 순환 애니메이션(Mac cycle_motion): 대기열이 한 칸 돌았을 때만 0.25초 동안 ease-out으로 움직인다.
struct CycleMotion {
    static let duration = 0.25
    var queue: [String] = []
    var since: Double? = nil
    var previous: [String]? = nil

    mutating func update(_ newQueue: [String], now: Double) {
        guard newQueue != queue else {
            return
        }
        let turned = !queue.isEmpty && newQueue.count > 1 && Array(queue.dropFirst()) == Array(newQueue.dropLast())
        previous = turned ? queue : nil
        since = turned ? now : nil
        queue = newQueue
    }

    /// (진행 0~1, 직전 대기열). 움직이지 않으면 (1, nil).
    func progress(now: Double) -> (Double, [String]?) {
        guard let since, now - since < Self.duration else {
            return (1, nil)
        }
        let linear = (now - since) / Self.duration
        return (1 - pow(1 - linear, 3), previous)
    }
}
