import ARKit
import Foundation
import QuartzCore
import simd

/// ARKit 손 추적 → 판정 Python이 받는 손 관절(MediaPipe 21점 순서) 두 벌.
///
/// - world: 관절의 월드 위치(m). 꼬집기 비율·관절 각도는 거리·각도만 쓰므로 좌표계와 무관하다.
/// - image: 머리(기기) 좌표를 MediaPipe 이미지 축에 맞춘 값(x 오른쪽, y 아래, z 멀어지는 쪽, m).
///   손바닥 방향(2D 외적)·기울기·자세가 이 값을 쓴다. ARKit 기기 좌표는 x 오른쪽, y 위, -z 앞이다.
final class HandTracker: @unchecked Sendable {
    /// MediaPipe 손 landmark 21점 순서(0 손목, 1~4 엄지, 5~8 검지, 9~12 중지, 13~16 약지, 17~20 소지).
    /// 손가락은 뿌리(Knuckle, MCP) · 중간 시작(IntermediateBase, PIP) · 끝마디 시작(IntermediateTip, DIP) · 끝(Tip).
    static let mediaPipeJoints: [HandSkeleton.JointName] = [
        .wrist,
        .thumbKnuckle, .thumbIntermediateBase, .thumbIntermediateTip, .thumbTip,
        .indexFingerKnuckle, .indexFingerIntermediateBase, .indexFingerIntermediateTip, .indexFingerTip,
        .middleFingerKnuckle, .middleFingerIntermediateBase, .middleFingerIntermediateTip, .middleFingerTip,
        .ringFingerKnuckle, .ringFingerIntermediateBase, .ringFingerIntermediateTip, .ringFingerTip,
        .littleFingerKnuckle, .littleFingerIntermediateBase, .littleFingerIntermediateTip, .littleFingerTip,
    ]

    struct Hand {
        var world: [SIMD3<Float>]  // 21점, 월드(m)
        var image: [SIMD3<Float>]  // 21점, 머리 기준 이미지 축(m)
    }

    private let session = ARKitSession()
    private let handProvider = HandTrackingProvider()
    private let worldProvider = WorldTrackingProvider()

    /// 손 추적 권한을 받고 시작한다. 실패하면 오류 문구.
    func start() async -> String? {
        guard HandTrackingProvider.isSupported else {
            return "이 기기(또는 시뮬레이터)는 손 추적을 지원하지 않습니다"
        }
        let result = await session.requestAuthorization(for: [.handTracking])
        guard result[.handTracking] == .allowed else {
            return "손 추적 권한이 없습니다(설정 > 개인정보 보호 및 보안 > 손 추적)"
        }
        do {
            try await session.run([handProvider, worldProvider])
            return nil
        } catch {
            return "손 추적을 시작하지 못했습니다: \(error.localizedDescription)"
        }
    }

    func stop() {
        session.stop()
    }

    /// 지금 손 상태. 추적되지 않는 손은 빠진다.
    func currentHands() -> [String: Hand] {
        let now = CACurrentMediaTime()
        guard let device = worldProvider.queryDeviceAnchor(atTimestamp: now) else {
            return [:]
        }
        let headFromWorld = device.originFromAnchorTransform.inverse
        let anchors = handProvider.latestAnchors
        var hands: [String: Hand] = [:]
        for (label, anchor) in [("Left", anchors.leftHand), ("Right", anchors.rightHand)] {
            guard let anchor, anchor.isTracked, let skeleton = anchor.handSkeleton else {
                continue
            }
            var world: [SIMD3<Float>] = []
            var image: [SIMD3<Float>] = []
            for name in Self.mediaPipeJoints {
                let transform = anchor.originFromAnchorTransform * skeleton.joint(name).anchorFromJointTransform
                let position = transform.columns.3
                world.append(SIMD3(position.x, position.y, position.z))
                let head = headFromWorld * position
                image.append(SIMD3(head.x, -head.y, -head.z))
            }
            hands[label] = Hand(world: world, image: image)
        }
        return hands
    }
}
