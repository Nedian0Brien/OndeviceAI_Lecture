import RealityKit
import SwiftUI
import UIKit
import simd

/// 몰입 공간: Mac 앱 HUD(draw_hud)의 손 위 요소를 손 관절 3D 위치에 그린다.
/// 골격선·엄지 끝 점, 손끝 라벨(유리 원), 순환 대기 글자, 다가옴·떼기 진행 호, 손 이름, 주먹 지움 호.
/// 손 추적은 이 공간이 열려 있는 동안만 돈다.
struct ImmersiveView: View {
    @Environment(AppModel.self) private var model
    @State private var skeleton = SkeletonEntities()

    static let slots = [1, 2]  // 순환 대기 글자 자리(Mac CYCLE_SHOWN 3 → 대기 2)

    static var attachmentIDs: [String] {
        AppModel.hands.flatMap { hand in
            (0..<4).flatMap { finger in
                ["tip", "arc", "prev"].map { "\(hand)-\(finger)-\($0)" } + slots.map { "\(hand)-\(finger)-q\($0)" }
            } + ["\(hand)-caption", "\(hand)-fist"]
        }
    }

    var body: some View {
        RealityView { content, attachments in
            content.add(skeleton.root)
            for id in Self.attachmentIDs {
                if let entity = attachments.entity(for: id) {
                    entity.components.set(BillboardComponent())  // 항상 사용자를 향한다
                    entity.isEnabled = false
                    content.add(entity)
                }
            }
        } update: { _, attachments in
            skeleton.update(model.showGuides ? model.layouts : [:])
            let placements = HUDPlacement.compute(model: model)
            for id in Self.attachmentIDs {
                guard let entity = attachments.entity(for: id) else {
                    continue
                }
                if let position = placements[id] {
                    entity.position = position
                    entity.isEnabled = true
                } else {
                    entity.isEnabled = false
                }
            }
        } attachments: {
            ForEach(AppModel.hands, id: \.self) { hand in
                ForEach(0..<4, id: \.self) { finger in
                    Attachment(id: "\(hand)-\(finger)-tip") { TipLabelView(hand: hand, finger: finger) }
                    Attachment(id: "\(hand)-\(finger)-arc") { ApproachArcView(hand: hand, finger: finger) }
                    Attachment(id: "\(hand)-\(finger)-prev") { RisingLetterView(hand: hand, finger: finger) }
                    ForEach(Self.slots, id: \.self) { slot in
                        Attachment(id: "\(hand)-\(finger)-q\(slot)") {
                            WaitingLetterView(hand: hand, finger: finger, slot: slot)
                        }
                    }
                }
                Attachment(id: "\(hand)-caption") { HandCaptionView(hand: hand) }
                Attachment(id: "\(hand)-fist") { FistArcView(hand: hand) }
            }
        }
        .task { await model.startHands() }
        .onDisappear { model.stopHands() }
    }
}

/// 첨부 뷰 위치(월드 m). 보이지 않을 것은 빠진다. 규칙은 Mac draw_hud와 같다.
@MainActor
enum HUDPlacement {
    static func compute(model: AppModel) -> [String: SIMD3<Float>] {
        var placements: [String: SIMD3<Float>] = [:]
        for (hand, layout) in model.layouts {
            guard let info = model.state.hands?[hand] else {
                continue
            }
            if model.showGuides {
                placements["\(hand)-caption"] = layout.captionPoint
            }
            if info.fist > 0 {
                placements["\(hand)-fist"] = layout.palmCenter
            }
            for finger in 0..<4 {
                guard let state = info.fingers[safe: finger], !state.queue.isEmpty else {
                    continue
                }
                let id = "\(hand)-\(finger)"
                if state.approach > 0 && !state.active {  // 다가옴 호는 버튼 표시라 항상(사용자, 2026-10-23)
                    placements["\(id)-arc"] = layout.tip(finger)
                }
                var center = layout.labelCenter(finger)
                let waiting = state.queue.count - 1
                if waiting > 0 {
                    let (eased, previous) = model.cycles[id]?.progress(now: model.frameTime) ?? (1, nil)
                    let step = 1 / Float(waiting)
                    for slot in ImmersiveView.slots where slot <= waiting {
                        var fraction = Float(slot) * step
                        if previous != nil && slot < waiting {  // 한 칸 아래 자리에서 올라오는 중
                            fraction = Float(slot + 1) * step + (fraction - Float(slot + 1) * step) * Float(eased)
                        }
                        placements["\(id)-q\(slot)"] = layout.pathPoint(finger, fraction: fraction)
                    }
                    if previous != nil {
                        // 방금 입력된 글자는 손끝에서 조금 올라가며 사라지고, 다음 글자는 한 칸 아래에서 손끝으로 올라온다.
                        let lift = layout.diameter * 0.8 * Float(eased)
                        placements["\(id)-prev"] = center + layout.tipDirection(finger) * lift
                        center = layout.pathPoint(finger, fraction: step * Float(1 - eased))
                    }
                }
                placements["\(id)-tip"] = center
            }
        }
        return placements
    }
}

/// 골격선(손 관절 연결선)과 엄지 끝 점. Mac: 선 (237, 242, 250, 95), 엄지 끝 (245, 248, 252, 200).
@MainActor
final class SkeletonEntities {
    let root = Entity()
    private var bones: [String: [ModelEntity]] = [:]
    private var thumbs: [String: ModelEntity] = [:]

    init() {
        var line = UnlitMaterial(color: UIColor(red: 237 / 255, green: 242 / 255, blue: 250 / 255, alpha: 1))
        line.blending = .transparent(opacity: .init(floatLiteral: 95.0 / 255))
        var dot = UnlitMaterial(color: UIColor(red: 245 / 255, green: 248 / 255, blue: 252 / 255, alpha: 1))
        dot.blending = .transparent(opacity: .init(floatLiteral: 200.0 / 255))
        let cylinder = MeshResource.generateCylinder(height: 1, radius: 0.0006)
        let sphere = MeshResource.generateSphere(radius: 0.0025)
        for hand in AppModel.hands {
            bones[hand] = HandLayout.connections.map { _ in
                let bone = ModelEntity(mesh: cylinder, materials: [line])
                bone.isEnabled = false
                root.addChild(bone)
                return bone
            }
            let thumb = ModelEntity(mesh: sphere, materials: [dot])
            thumb.isEnabled = false
            root.addChild(thumb)
            thumbs[hand] = thumb
        }
    }

    func update(_ layouts: [String: HandLayout]) {
        for hand in AppModel.hands {
            guard let layout = layouts[hand] else {
                bones[hand]?.forEach { $0.isEnabled = false }
                thumbs[hand]?.isEnabled = false
                continue
            }
            for (bone, (a, b)) in zip(bones[hand] ?? [], HandLayout.connections) {
                let start = layout.points[a], end = layout.points[b]
                let length = simd_distance(start, end)
                guard length > 1e-5 else {
                    bone.isEnabled = false
                    continue
                }
                bone.position = (start + end) / 2
                bone.orientation = simd_quatf(from: SIMD3(0, 1, 0), to: (end - start) / length)
                bone.scale = SIMD3(1, length, 1)
                bone.isEnabled = true
            }
            thumbs[hand]?.position = layout.points[4]
            thumbs[hand]?.isEnabled = true
        }
    }
}

// MARK: - 첨부 뷰. 크기는 손끝 라벨 지름(m)을 포인트로 바꿔 Mac과 같은 비율로 그린다.

private struct HandMetrics {
    let diameter: CGFloat  // 손끝 라벨 지름(pt)

    @MainActor
    init(model: AppModel, hand: String, physicalMetrics: PhysicalMetricsConverter) {
        let meters = CGFloat(model.layouts[hand]?.diameter ?? HandLayout.maxDiameter)
        diameter = physicalMetrics.convert(meters, from: .meters)
    }

    /// Mac: 지름 26px일 때 1px 비율(글꼴·선 두께 기준)
    var unit: CGFloat { diameter / 26 }
    var ink: Color { HUD.white }
}

/// 손끝 라벨: 다음 꼬집기로 입력될 글자. 다가오면 밝게(hover), 꼬집는 중이면 흰색(pressed)과 진행 호.
struct TipLabelView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String
    let finger: Int

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        if let state = model.finger(hand, finger), let text = state.queue.first {
            let single = text.count == 1
            let font = Font.system(size: single ? metrics.diameter * 0.55 : 16 * metrics.unit, weight: .medium)
            let pressed = state.active
            ZStack {
                Text(text)
                    .font(font)
                    .foregroundStyle(pressed ? HUD.darkInk : metrics.ink)
                    .padding(.horizontal, single ? 0 : 5 * metrics.unit)
                    .frame(minWidth: metrics.diameter, minHeight: metrics.diameter)
                    .background(GlassShape(circle: single, hover: state.approach > 0.5, pressed: pressed,
                                           lineWidth: max(1, metrics.unit)))
                if pressed {
                    let ring = metrics.diameter * 0.72 * 2
                    Circle()
                        .stroke(HUD.ringBase, lineWidth: max(2, 2 * metrics.unit))
                        .frame(width: ring, height: ring)
                    let progress = model.state.holdMode == true ? state.hold : state.release
                    if progress > 0 {
                        ProgressArc(progress: progress, color: HUD.okGreen, lineWidth: max(2, 3 * metrics.unit))
                            .frame(width: ring, height: ring)
                    }
                }
            }
            .frame(width: metrics.diameter * 1.6, height: metrics.diameter * 1.6)
            .padding(4 * metrics.unit)  // 첨부 뷰는 자기 크기만큼 잘려 그려진다: 진행 호 선 두께만큼 여백
        }
    }
}

/// 손끝의 다가옴 호(Mac: 손끝 점 둘레 반지름 6px, 기준값보다 다가온 정도).
struct ApproachArcView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String
    let finger: Int

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        let size = 12 * metrics.unit
        ProgressArc(progress: model.finger(hand, finger)?.approach ?? 0, color: metrics.ink,
                    lineWidth: max(1.5, 2 * metrics.unit))
            .frame(width: size, height: size)
            .padding(3 * metrics.unit)  // 선 두께 절반이 뷰 밖으로 나가 잘리지 않게
    }
}

/// 순환 대기 글자: 손가락을 따라 손바닥 마디 쪽에서 기다린다(작은 유리 원). 마지막 자리는 나타나는 중이면 글자만.
struct WaitingLetterView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String
    let finger: Int
    let slot: Int

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        if let queue = model.finger(hand, finger)?.queue, slot < queue.count {
            let small = metrics.diameter * 0.7
            let (eased, previous) = model.cycles["\(hand)-\(finger)"]?.progress(now: model.frameTime) ?? (1, nil)
            let appearing = previous != nil && slot == queue.count - 1
            Text(queue[slot])
                .font(.system(size: small * 0.6, weight: .medium))
                .foregroundStyle(HUD.white.opacity(appearing ? 230.0 / 255 * eased : 215.0 / 255))
                .frame(width: small, height: small)
                .background(appearing ? nil : GlassShape(circle: true, lineWidth: max(1, metrics.unit)))
                .modifier(OutlineIf(appearing))
                .padding(3 * metrics.unit)  // 테두리·외곽선 그림자가 잘리지 않게
        }
    }
}

/// 방금 입력된 글자: 손끝에서 조금 올라가며 사라진다.
struct RisingLetterView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String
    let finger: Int

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        let (eased, previous) = model.cycles["\(hand)-\(finger)"]?.progress(now: model.frameTime) ?? (1, nil)
        if let letter = previous?.first {
            Text(letter)
                .font(.system(size: metrics.diameter * 0.55, weight: .medium))
                .foregroundStyle(HUD.white.opacity(1 - eased))
                .outlined()
                .padding(4 * metrics.unit)  // 외곽선 그림자가 잘리지 않게
        }
    }
}

/// 손 이름과 방향(손목 아래). 엄지가 가려지는 자세면 노란 안내를 덧붙인다.
struct HandCaptionView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        VStack(spacing: 4 * metrics.unit) {
            Text(model.caption(hand))
                .foregroundStyle(HUD.muted)
            if model.state.hands?[hand]?.occluded == true {
                Text("엄지 쪽을 카메라로 돌려 주세요")
                    .foregroundStyle(HUD.promptYellow)
            }
        }
        .font(.system(size: 16 * metrics.unit))
        .outlined()
        .fixedSize()
        .padding(4 * metrics.unit)  // 외곽선 그림자가 잘리지 않게
    }
}

/// 주먹 진행: 손바닥 가운데 빨간 호와 "지움". 쥐고 있으면 다음 지움까지의 진행이다.
struct FistArcView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        let size = 44 * metrics.unit
        ZStack {
            ProgressArc(progress: model.state.hands?[hand]?.fist ?? 0, color: HUD.deleteRed,
                        lineWidth: max(2, 3 * metrics.unit))
            Text("지움")
                .font(.system(size: 16 * metrics.unit))
                .foregroundStyle(HUD.deleteRed)
                .outlined()
        }
        .frame(width: size, height: size)
        .padding(6 * metrics.unit)  // 호의 선 두께 절반과 글자 외곽선 그림자가 사각형으로 잘리던 문제(사용자, 2026-10-23)
    }
}

private struct OutlineIf: ViewModifier {
    let enabled: Bool

    init(_ enabled: Bool) {
        self.enabled = enabled
    }

    func body(content: Content) -> some View {
        if enabled {
            content.outlined()
        } else {
            content
        }
    }
}
