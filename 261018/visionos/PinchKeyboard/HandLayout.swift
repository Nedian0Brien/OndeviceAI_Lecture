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
    static let maxDiameter: Float = 0.022
    static let minDiameter: Float = 0.0127

    let points: [SIMD3<Float>]  // 21점 월드 위치
    let diameter: Float  // 손끝 라벨 지름(m)

    init(points: [SIMD3<Float>]) {
        self.points = points
        let tips = Self.fingerTips.map { points[$0] }
        let spacing = zip(tips, tips.dropFirst()).map { simd_distance($0, $1) }.min() ?? 0.02
        diameter = min(Self.maxDiameter, max(Self.minDiameter, spacing * 0.9))
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

    /// 손가락 위 경로(라벨 자리 → TIP → DIP → PIP → MCP)에서 길이 비율 fraction의 점(Mac finger_path_point).
    func pathPoint(_ finger: Int, fraction: Float) -> SIMD3<Float> {
        let mcp = Self.fingerMCPs[finger]
        let path = [labelCenter(finger)] + [3, 2, 1, 0].map { points[mcp + $0] }
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

    /// 주먹 진행 자리: 손목~중지 뿌리 가운데.
    var palmCenter: SIMD3<Float> {
        (points[0] + points[9]) / 2
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
