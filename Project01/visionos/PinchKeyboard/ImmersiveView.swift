import RealityKit
import SwiftUI
import UIKit
import simd

/// 몰입 공간: Mac 앱 HUD(draw_hud)의 손 위 요소를 손 관절 3D 위치에 그린다.
/// 선 HUD(가는 선·눈금·파랑 강조): 손끝 버튼(가는 원 + 진행 눈금), 순환 대기 글자, 손바닥 원형 표시(다가옴 막대,
/// 누르는 손가락 삼각형, 주먹 지움 막대 링), 모음 끌기(지나온 길, 끌기 기준 원, 들어갈 획). 안내 표시를 켜면 골격선·손 이름.
/// 손 추적은 이 공간이 열려 있는 동안만 돈다.
struct ImmersiveView: View {
    @Environment(AppModel.self) private var model
    @State private var skeleton = SkeletonEntities()
    @State private var trail = TrailEntities()

    static let slots = [1, 2]  // 순환 대기 글자 자리(Mac CYCLE_SHOWN 3 → 대기 2)

    static var attachmentIDs: [String] {
        AppModel.hands.flatMap { hand in
            (0..<4).flatMap { finger in
                ["tip", "prev"].map { "\(hand)-\(finger)-\($0)" } + slots.map { "\(hand)-\(finger)-q\($0)" }
            } + ["\(hand)-hub", "\(hand)-caption", "\(hand)-dragring", "\(hand)-dragbadge"]
        }
    }

    var body: some View {
        RealityView { content, attachments in
            content.add(skeleton.root)
            content.add(trail.root)
            for id in Self.attachmentIDs {
                if let entity = attachments.entity(for: id) {
                    if !id.hasSuffix("-hub") {  // 손바닥 원형 표시는 손바닥 평면에 눕힌다(update)
                        entity.components.set(BillboardComponent())  // 항상 사용자를 향한다
                    }
                    entity.isEnabled = false
                    content.add(entity)
                }
            }
        } update: { _, attachments in
            skeleton.update(model.showGuides ? model.layouts : [:])
            trail.update(model.trails, passed: model.state.hands?.mapValues(\.dragPassed) ?? [:])
            let placements = HUDPlacement.compute(model: model)
            for id in Self.attachmentIDs {
                guard let entity = attachments.entity(for: id) else {
                    continue
                }
                if let position = placements[id] {
                    entity.position = position
                    if id.hasSuffix("-hub"), let layout = model.layouts[String(id.dropLast(4))] {
                        // 원형 표시는 손바닥 평면에 눕혀 손과 함께 돌고 기운다(HandLayout.palmFrame).
                        let frame = layout.palmFrame(head: model.headPosition)
                        entity.position = frame.surface
                        entity.orientation = frame.rotation
                    }
                    entity.isEnabled = true
                } else {
                    entity.isEnabled = false
                }
            }
        } attachments: {
            ForEach(AppModel.hands, id: \.self) { hand in
                ForEach(0..<4, id: \.self) { finger in
                    Attachment(id: "\(hand)-\(finger)-tip") { TipLabelView(hand: hand, finger: finger) }
                    Attachment(id: "\(hand)-\(finger)-prev") { RisingLetterView(hand: hand, finger: finger) }
                    ForEach(Self.slots, id: \.self) { slot in
                        Attachment(id: "\(hand)-\(finger)-q\(slot)") {
                            WaitingLetterView(hand: hand, finger: finger, slot: slot)
                        }
                    }
                }
                Attachment(id: "\(hand)-caption") { HandCaptionView(hand: hand) }
                Attachment(id: "\(hand)-hub") { PalmHubView(hand: hand) }
                Attachment(id: "\(hand)-dragring") { DragRingView(hand: hand) }
                Attachment(id: "\(hand)-dragbadge") { DragBadgeView(hand: hand) }
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
            placements["\(hand)-hub"] = layout.hubCenter  // 손바닥 원형 표시: 켜기만 정하고 위치·회전은 update에서 손바닥 평면으로
            if let path = model.trails[hand], let start = path.first, let end = path.last {
                // 끌기 기준 원은 꼬집기 시작점에, 획 미리 보기는 꼬집은 지점에서 손끝 라벨 줄 바깥에(Mac과 같다).
                placements["\(hand)-dragring"] = start
                placements["\(hand)-dragbadge"] = end + layout.forward * layout.diameter * 2.6
            }
            // 손끝 버튼 펼침: 손바닥 가운데와 제자리 사이(주먹이면 모여 사라지고, 펴면 펼쳐진다. Mac spread_motion).
            let spread = Float(model.spreads[hand] ?? 0)
            guard spread >= 0.05 else {
                continue
            }
            let palm = layout.palmCenter
            func place(_ point: SIMD3<Float>) -> SIMD3<Float> {
                palm + (point - palm) * spread
            }
            for finger in 0..<4 {
                guard let state = info.fingers[safe: finger], !state.queue.isEmpty else {
                    continue
                }
                let id = "\(hand)-\(finger)"
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
                        placements["\(id)-q\(slot)"] = place(layout.pathPoint(finger, fraction: fraction))
                    }
                    if previous != nil {
                        // 방금 입력된 글자는 손끝에서 조금 올라가며 사라지고, 다음 글자는 한 칸 아래에서 손끝으로 올라온다.
                        let lift = layout.diameter * 0.8 * Float(eased)
                        placements["\(id)-prev"] = place(center + layout.tipDirection(finger) * lift)
                        center = layout.pathPoint(finger, fraction: step * Float(1 - eased))
                    }
                }
                placements["\(id)-tip"] = place(center)
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

/// 모음 끌기 궤적: 꼬집은 지점이 지나온 길을 이은 선(Mac: 흰 선 → 기준을 넘으면 파랑)과 끝 점.
@MainActor
final class TrailEntities {
    static let limit = 300  // 손마다 선분 수(AppModel.historyLimit와 같다)
    let root = Entity()
    private var segments: [String: [ModelEntity]] = [:]
    private var heads: [String: ModelEntity] = [:]
    private var passedState: [String: Bool] = [:]
    private var shown: [String: Int] = [:]  // 직전 프레임에 켠 선분 수(그 뒤는 이미 꺼져 있다)
    private let white: UnlitMaterial
    private let accent: UnlitMaterial

    init() {
        var white = UnlitMaterial(color: UIColor(red: 236 / 255, green: 240 / 255, blue: 248 / 255, alpha: 1))
        white.blending = .transparent(opacity: .init(floatLiteral: 225.0 / 255))
        var accent = UnlitMaterial(color: UIColor(red: 158 / 255, green: 197 / 255, blue: 255 / 255, alpha: 1))  // 강조 파랑
        accent.blending = .transparent(opacity: .init(floatLiteral: 1))
        self.white = white
        self.accent = accent
        let cylinder = MeshResource.generateCylinder(height: 1, radius: 0.0013)
        let sphere = MeshResource.generateSphere(radius: 0.0024)
        for hand in AppModel.hands {
            segments[hand] = (0..<Self.limit).map { _ in
                let segment = ModelEntity(mesh: cylinder, materials: [white])
                segment.isEnabled = false
                root.addChild(segment)
                return segment
            }
            let head = ModelEntity(mesh: sphere, materials: [white])
            head.isEnabled = false
            root.addChild(head)
            heads[hand] = head
            passedState[hand] = false
        }
    }

    func update(_ trails: [String: [SIMD3<Float>]], passed: [String: Bool]) {
        for hand in AppModel.hands {
            let path = trails[hand] ?? []
            let isPassed = passed[hand] ?? false
            if passedState[hand] != isPassed {
                let material = isPassed ? accent : white
                segments[hand]?.forEach { $0.model?.materials = [material] }
                heads[hand]?.model?.materials = [material]
                passedState[hand] = isPassed
            }
            let pool = segments[hand] ?? []
            let used = min(pool.count, max(path.count - 1, 0))
            for (index, segment) in pool.prefix(max(used, shown[hand] ?? 0)).enumerated() {
                guard index < used else {
                    segment.isEnabled = false
                    continue
                }
                let start = path[index], end = path[index + 1]
                let length = simd_distance(start, end)
                guard length > 1e-5 else {
                    segment.isEnabled = false
                    continue
                }
                segment.position = (start + end) / 2
                segment.orientation = simd_quatf(from: SIMD3(0, 1, 0), to: (end - start) / length)
                segment.scale = SIMD3(1, length, 1)
                segment.isEnabled = true
            }
            shown[hand] = used
            if let last = path.last {
                heads[hand]?.position = last
                heads[hand]?.isEnabled = true
            } else {
                heads[hand]?.isEnabled = false
            }
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
}

/// 손끝 버튼(직선 도넛, Mac draw_hud와 같다): 짧은 직선 bars개를 원형으로 늘어놓은 도넛. 진행만큼 12시부터
/// 시계 방향으로 직선이 켜진다. 글자는 도넛 안쪽 어두운 원 위.
/// - 다가옴: 흰 직선. - 누르는 중: 안쪽이 파랑 면, 꺼진 직선이 옅은 파랑, 떼기(또는 누르고 유지) 진행만큼 밝은 파랑(번짐).
struct TipLabelView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String
    let finger: Int
    static let bars = 30  // Mac DONUT_BARS
    static let inner: CGFloat = 0.84  // Mac DONUT_INNER(버튼 반지름 비율)
    static let outer: CGFloat = 1.04  // Mac DONUT_OUTER

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        if let state = model.finger(hand, finger), let text = state.queue.first {
            let unit = metrics.unit
            let single = text.count == 1
            let pressed = state.active
            let radius = (single ? metrics.diameter : max(metrics.diameter, CGFloat(text.count) * 15 * unit + 6 * unit)) / 2
            let done = model.state.holdMode == true ? state.hold : state.release
            let spread = model.spreads[hand] ?? 0
            let lit = pressed ? Int((done * Double(Self.bars)).rounded())
                : spread >= 1 ? Int((state.approach * Double(Self.bars)).rounded()) : 0
            let side = 2 * radius * Self.outer + 2 * unit
            ZStack {
                Circle()
                    .fill(pressed ? HUD.pressedFill : HUD.backdrop)
                    .frame(width: 2 * radius * Self.inner, height: 2 * radius * Self.inner)
                Canvas { context, size in
                    context.ticks(center: CGPoint(x: size.width / 2, y: size.height / 2), radius: radius * Self.inner,
                                  length: radius * (Self.outer - Self.inner), count: Self.bars,
                                  color: pressed ? HUD.accent.opacity(90.0 / 255) : HUD.rgba(236, 240, 248, 70), lit: lit,
                                  litColor: pressed ? HUD.accent : HUD.line, width: max(1, 1.3 * unit))
                }
                .frame(width: side, height: side)
                .glow(pressed, radius: 2 * unit)
                Text(text)
                    .font(.system(size: single ? 2 * radius * 0.55 : 15 * unit, weight: .medium))
                    .foregroundStyle(HUD.white)
            }
            .frame(width: side, height: side)
            .padding(4 * unit)  // 첨부 뷰는 자기 크기만큼 잘려 그려진다: 번짐만큼 여백
            .modifier(SpreadFade(hand: hand))
        }
    }
}

/// 순환 대기 글자: 손가락을 따라 손바닥 마디 쪽에서 기다린다(작은 가는 원, 뒤 글자일수록 옅게).
/// 마지막 자리는 나타나는 중이면 글자만.
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
            let rank = Double(slot - 1)
            Text(queue[slot])
                .font(.system(size: small * 0.6, weight: .medium))
                .foregroundStyle(HUD.white.opacity(appearing ? 230.0 / 255 * eased : (215 - 50 * rank) / 255))
                .frame(width: small, height: small)
                .background {
                    if !appearing {
                        HairlineCircle(edge: HUD.rgba(236, 240, 248, 150 - 40 * rank), lineWidth: max(1, metrics.unit))
                    }
                }
                .modifier(OutlineIf(appearing))
                .padding(3 * metrics.unit)  // 테두리·외곽선 그림자가 잘리지 않게
                .modifier(SpreadFade(hand: hand))
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
                .modifier(SpreadFade(hand: hand))
        }
    }
}

/// 손 이름과 방향(손목 아래, 설정 창의 안내 표시를 켰을 때만). 엄지가 가려지는 자세면 노란 안내를 덧붙인다.
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

/// 손바닥 원형 표시(Mac draw_palm_hub와 같은 구성·값). 모든 층이 정보를 나타낸다.
/// - 안쪽 파랑 반투명 띠: 손가락 방향마다 막대 3개, 길이 = 다가옴 진행. 누르는 손가락 방향의 띠는 밝게.
/// - 눈금자 호(손가락 쪽): 삼각형이 누르는 손가락(없으면 가장 많이 다가온 손가락) 방향을 가리킨다.
/// - 바깥 막대 링: 손목 쪽을 비운 촘촘한 막대. 주먹 지움 진행만큼 빨갛게 켜진다.
/// - 가운데: "주먹 쥐기로 지움"(주먹이면 "지움"), 입력하면 그 글자를 잠깐 크게.
/// 원형 표시에서 손가락으로 잇는 선은 그리지 않는다(2026-10-23 사용자).
/// 주먹이면(손끝 버튼 펼침 0) 안쪽 층은 사라지고 바깥 막대 링(지움)은 남는다.
struct PalmHubView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String
    static let hint = "주먹 쥐기로 지움"  // Mac HUB_HINT

    var body: some View {
        if let layout = model.layouts[hand], let info = model.state.hands?[hand] {
            let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
            let spread = model.spreads[hand] ?? 0
            let hub = layout.hub(spread: Float(spread), waiting: info.fingers.map { max(0, $0.queue.count - 1) },
                                 head: model.headPosition)
            let echo = model.echo.flatMap { echo -> (String, Double, Double)? in
                guard echo.hand == hand, let (alpha, grow) = EchoMotion.look(age: model.frameTime - echo.start) else {
                    return nil
                }
                return (echo.text, alpha, grow)
            }
            let scale = physicalMetrics.convert(1, from: .meters)  // 1m당 포인트
            let reach = (HubDrawing.radii.ring + 0.06) * hub.size  // 바깥 막대 링까지만 덮는다
            let side = 2 * (reach * scale + 12 * metrics.unit)
            let drawing = HubDrawing(hub: hub, info: info, spread: spread, scale: scale, unit: metrics.unit,
                                     diameter: metrics.diameter)
            ZStack {
                Canvas { context, size in drawing.draw(context, size: size, accentOnly: false) }
                Canvas { context, size in drawing.draw(context, size: size, accentOnly: true) }
                    .glow(true, radius: 3 * metrics.unit)
                if info.fist > 0 {
                    Text("지움")
                        .font(.system(size: 16 * metrics.unit))
                        .foregroundStyle(HUD.deleteRed)
                        .outlined()
                } else if let (text, alpha, grow) = echo {  // 방금 입력된 글자: 크게 나타났다 커지며 사라진다
                    Text(text)
                        .font(.system(size: (text.count == 1 ? 0.18 : 0.09) * hub.size * scale * grow, weight: .medium))
                        .foregroundStyle(HUD.white.opacity(alpha))
                        .outlined()
                        .glow(true, radius: 4 * metrics.unit)
                } else if spread > 0.05 {
                    Text(Self.hint)
                        .font(.system(size: 16 * metrics.unit))
                        .foregroundStyle(HUD.muted.opacity(spread))
                        .outlined()
                }
            }
            .frame(width: side, height: side)
        }
    }
}

/// 손바닥 원형 표시 그리기. accentOnly면 번짐을 줄 강조 요소(누르는 손가락 띠·막대, 삼각형)만 그린다.
struct HubDrawing {
    let hub: PalmHub
    let info: PinchState.Hand
    let spread: Double
    let scale: CGFloat  // 1m당 포인트
    let unit: CGFloat
    let diameter: CGFloat  // 손끝 버튼 지름(pt)
    static let bars = 96  // Mac HUB_BARS
    // Mac HUB_BAND·HUB_BARS_AT·HUB_SCALE·HUB_RING(손 길이 비율). 바깥 링이 손가락 뿌리 근처를 넘지 않게 작게 둔다.
    static let radii: (band: CGFloat, bars: CGFloat, scale: CGFloat, ring: CGFloat) = (0.23, 0.28, 0.39, 0.42)
    static let gap = 70.0  // Mac HUB_GAP: 손목 쪽에 비워 두는 각도

    func draw(_ context: GraphicsContext, size: CGSize, accentOnly: Bool) {
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        let s = hub.size * scale
        let (rBand, rBars, rScale, rRing) = (Self.radii.band * s, Self.radii.bars * s, Self.radii.scale * s,
                                             Self.radii.ring * s)
        defer { clearAroundButtons(context, center: c) }
        let k = spread
        if !accentOnly {
            context.ticks(center: c, radius: rRing, length: 0.04 * s, count: Self.bars, color: HUD.rgba(236, 240, 248, 90),
                          lit: Int((info.fist * Double(Self.bars)).rounded()), litColor: HUD.deleteRed,
                          width: max(1, 1.2 * unit), start: hub.wrist + Self.gap / 2, end: hub.wrist + 360 - Self.gap / 2)
        }
        guard k > 0.05 else {
            return
        }
        let lo = (hub.angles.min() ?? 0) - 22, hi = (hub.angles.max() ?? 0) + 22
        var target = info.fingers.firstIndex { $0.active }
        if target == nil, let best = info.fingers.indices.max(by: { info.fingers[$0].approach < info.fingers[$1].approach }),
           info.fingers[best].approach > 0.2 {
            target = best
        }
        if !accentOnly {
            context.arc(center: c, radius: rScale, start: lo - 10, end: hi + 10, color: HUD.line.opacity(0.8 * k))
            context.ticks(center: c, radius: rScale - 0.02 * s, length: 0.02 * s, count: Int((hi - lo + 20) / 3) + 1,
                          color: HUD.lineDim.opacity(k), start: lo - 10, end: hi + 10)
            context.ticks(center: c, radius: rScale - 0.04 * s, length: 0.04 * s, count: Int((hi - lo + 20) / 15) + 1,
                          color: HUD.line.opacity(0.8 * k), start: lo - 10, end: hi + 10)
            context.band(center: c, inner: rBand, outer: rBars - 0.02 * s, start: lo, end: hi,
                         color: HUD.accentFill.opacity(0.7 * k))
            context.arc(center: c, radius: rBand, start: lo, end: hi, color: HUD.lineDim.opacity(k))
            context.dashedCircle(center: c, radius: 0.10 * s, color: HUD.lineDim.opacity(k), dashes: 24)
        }
        for (finger, angle) in hub.angles.enumerated() {
            guard let state = info.fingers[safe: finger] else {
                continue
            }
            let pressed = state.active
            if pressed && accentOnly {
                context.band(center: c, inner: rBand, outer: rBars - 0.02 * s, start: angle - 8, end: angle + 8,
                             color: HUD.accent.opacity(0.55 * k))
            }
            let amount = pressed ? 1 : state.approach
            if pressed == accentOnly {
                for offset in [-3.5, 0, 3.5] {
                    var bar = Path()
                    bar.move(to: Polar.point(c, rBars, angle + offset))
                    bar.addLine(to: Polar.point(c, rBars + (0.02 + 0.08 * amount) * s, angle + offset))
                    context.stroke(bar, with: .color((amount > 0 ? HUD.accent : HUD.lineDim).opacity(k)),
                                   lineWidth: max(2, 2.5 * unit))
                }
            }
        }
        if accentOnly, let target {
            context.pointer(center: c, radius: rScale + 2 * unit, degrees: hub.angles[target], size: 5 * unit,
                            color: HUD.accent.opacity(k))
        }
    }

    /// 손끝 버튼(눈금 포함)과 대기 글자 둘레를 지운다: 원형 표시 요소가 버튼을 가리지 않게(Mac clear_circle).
    private func clearAroundButtons(_ context: GraphicsContext, center c: CGPoint) {
        guard spread > 0.05 else {
            return
        }
        var eraser = context
        eraser.blendMode = .destinationOut
        func clear(_ offset: CGPoint, _ radius: CGFloat) {
            let p = CGPoint(x: c.x + offset.x * scale, y: c.y + offset.y * scale)
            eraser.fill(Path(ellipseIn: CGRect(x: p.x - radius, y: p.y - radius, width: 2 * radius, height: 2 * radius)),
                        with: .color(.black))
        }
        for (finger, home) in hub.homes.enumerated() {
            let count = info.fingers[safe: finger]?.queue.first?.count ?? 1
            let width = count == 1 ? diameter : max(diameter, CGFloat(count) * 15 * unit + 6 * unit)
            clear(home, (width / 2 * TipLabelView.outer + 3 * unit) * spread)
        }
        for slot in hub.slots {
            clear(slot, (diameter * 0.35 + 4 * unit) * spread)
        }
    }
}

/// 끌기 기준 원: 꼬집기 시작점 둘레의 파선 원과 가로·세로 축 눈금. 꼬집은 지점이 이 원 밖으로 나가면 끌기(ㅡ / ㅣ)다.
/// 반지름은 판정 Python이 준 값(m).
struct DragRingView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        let radius = physicalMetrics.convert(CGFloat(model.state.hands?[hand]?.dragRadius ?? 0), from: .meters)
        let side = 2 * (radius + 6 * metrics.unit)
        Canvas { context, size in
            let c = CGPoint(x: size.width / 2, y: size.height / 2)
            context.dashedCircle(center: c, radius: radius, color: HUD.lineDim, width: max(1, metrics.unit))
            context.ticks(center: c, radius: radius, length: 5 * metrics.unit, count: 4, color: HUD.line,
                          width: max(1, metrics.unit))
        }
        .frame(width: side, height: side)
    }
}

/// 획 미리 보기: 지금 떼면 들어갈 획(ㆍ / ㅡ / ㅣ). 끌기 기준을 넘으면 파랑 원과 끈 방향 삼각형(번짐).
struct DragBadgeView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.physicalMetrics) private var physicalMetrics
    let hand: String

    var body: some View {
        let metrics = HandMetrics(model: model, hand: hand, physicalMetrics: physicalMetrics)
        if let info = model.state.hands?[hand], let stroke = info.dragPreview {
            let size = metrics.diameter * 1.3
            let passed = info.dragPassed
            let side = size + 2 * 9 * metrics.unit
            ZStack {
                HairlineCircle(edge: passed ? HUD.accent : HUD.line, fill: passed ? HUD.accentFill : HUD.backdrop,
                               lineWidth: max(1, metrics.unit))
                    .frame(width: size, height: size)
                if passed, let pointer = info.dragPointer {
                    Canvas { context, canvas in
                        context.pointer(center: CGPoint(x: canvas.width / 2, y: canvas.height / 2),
                                        radius: size / 2 + 2 * metrics.unit, degrees: pointer, size: 5 * metrics.unit,
                                        color: HUD.accent)
                    }
                }
                Text(stroke)
                    .font(.system(size: size * 0.62, weight: .medium))
                    .foregroundStyle(HUD.white)
                    .outlined()
            }
            .frame(width: side, height: side)
            .glow(passed, radius: 3 * metrics.unit)
            .padding(3 * metrics.unit)  // 번짐·외곽선 그림자가 잘리지 않게
        }
    }
}

/// 손끝 버튼 펼침 정도만큼 크기·불투명도를 줄인다(주먹이면 손바닥 가운데로 모여 사라짐).
private struct SpreadFade: ViewModifier {
    @Environment(AppModel.self) private var model
    let hand: String

    func body(content: Content) -> some View {
        let spread = model.spreads[hand] ?? 0
        content
            .scaleEffect(spread)
            .opacity(spread)
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
