import SwiftUI

/// 입력 창: Mac 앱 HUD 위쪽(입력 칸, 순환 그룹 줄)과 아래쪽(상태 안내, 자판 지도)을 같은 모양(선 HUD)으로 그린다.
/// Mac의 단축키 대신 아래에 버튼을 둔다.
struct ContentView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openImmersiveSpace) private var openImmersiveSpace
    @Environment(\.dismissImmersiveSpace) private var dismissImmersiveSpace
    @Environment(\.openWindow) private var openWindow

    /// Mac HUD 글꼴 크기(작게 16 · 보통 21 · 크게 29)에 곱하는 배율.
    static let scale: CGFloat = 1.5
    static let small = Font.system(size: 16 * scale)
    static let normal = Font.system(size: 21 * scale)
    static let large = Font.system(size: 29 * scale)

    var body: some View {
        VStack(spacing: 8 * Self.scale) {
            InputCapsule()
            GroupRow()
            Spacer(minLength: 0)
            Text(model.status)
                .font(Self.small)
                .foregroundStyle(HUD.muted)
                .lineLimit(2)
                .padding(.horizontal, 14 * Self.scale)
                .frame(minHeight: 26 * Self.scale)
                .background(LinePanel(cornerRadius: 4 * Self.scale))
            if model.showMap {
                KeyboardMap()
            }
            controls
        }
        .padding(16 * Self.scale)
        .task { await model.prepare() }
    }

    private var controls: some View {
        HStack(spacing: 12) {
            Button(model.handsRunning ? "손 입력 끝" : "손 입력 시작") {
                Task { await toggleHands() }
            }
            Button("지우기") {
                Task { await model.command("clear") }
            }
            Button("고정 데이터 재생") {
                Task { await model.command("demo") }
            }
            Toggle("자판 지도", isOn: Bindable(model).showMap)
                .toggleStyle(.button)
            Button("설정") {
                openWindow(id: PinchKeyboardApp.settingsID)
            }
        }
    }

    private func toggleHands() async {
        if model.handsRunning {
            await dismissImmersiveSpace()
            return
        }
        switch await openImmersiveSpace(id: AppModel.immersiveID) {
        case .opened:
            break
        case .userCancelled:
            model.status = "손 입력을 취소했습니다"
        case .error:
            model.status = "몰입 공간을 열지 못했습니다"
        @unknown default:
            model.status = "몰입 공간을 열지 못했습니다"
        }
    }
}

/// 입력 한 줄 칸(선 HUD 패널 + 아래 눈금자, 큰 눈금 5칸마다). 확정 전 후보 글자 아래에 파랑 밑줄,
/// 후보가 없으면 0.5초 간격으로 깜빡이는 커서.
private struct InputCapsule: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let scale = ContentView.scale
        let text = (model.state.text ?? "").components(separatedBy: "\n").last ?? ""
        TimelineView(.periodic(from: .now, by: 0.5)) { context in
            let cursorOn = Int(context.date.timeIntervalSinceReferenceDate * 2) % 2 == 0
            HStack(spacing: 4 * scale) {
                if text.isEmpty {
                    Text("손끝을 꼬집어 입력")
                        .font(ContentView.normal)
                        .foregroundStyle(HUD.muted)
                } else {
                    Text(attributed(text))
                        .font(ContentView.large)
                        .foregroundStyle(HUD.white)
                        .lineLimit(1)
                        .truncationMode(.head)
                    if model.state.pending != true || model.state.pendingPunct == true {
                        Rectangle()
                            .fill(HUD.muted)
                            .frame(width: 2, height: 16 * scale)
                            .opacity(cursorOn ? 1 : 0)
                    }
                }
            }
            .padding(.horizontal, 20 * scale)
            .frame(minWidth: 140 * scale, minHeight: (text.isEmpty ? 34 : 40) * scale)
            .background(LinePanel(cornerRadius: 4 * scale))
            .overlay {
                Canvas { context, size in  // 아래 눈금자
                    let count = Int(size.width / (6 * scale))
                    for k in 1..<max(2, count) {
                        let x = size.width * CGFloat(k) / CGFloat(count)
                        var tick = Path()
                        tick.move(to: CGPoint(x: x, y: size.height))
                        tick.addLine(to: CGPoint(x: x, y: size.height - (k % 5 == 0 ? 5 : 3) * scale))
                        context.stroke(tick, with: .color(HUD.lineDim), lineWidth: 1)
                    }
                }
            }
        }
    }

    private func attributed(_ text: String) -> AttributedString {
        var shown = AttributedString(text)
        if model.state.pending == true, model.state.pendingPunct != true, let last = shown.characters.indices.last {
            shown[last...].underlineStyle = Text.LineStyle(pattern: .solid, color: HUD.accent)
        }
        return shown
    }
}

/// 입력 칸 아래: 순환 중인 그룹. 글자마다 아래 막대, 지금 글자만 밝게 + 파랑 막대(분절 표시).
private struct GroupRow: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let cell = 22 * ContentView.scale
        HStack(spacing: 0) {
            if let group = model.state.group {
                ForEach(Array(group.enumerated()), id: \.offset) { index, char in
                    let current = index == model.state.groupIndex
                    Text(char)
                        .font(ContentView.normal)
                        .foregroundStyle(current ? HUD.white : HUD.dim)
                        .outlined()
                        .frame(width: cell, height: cell)
                        .overlay(alignment: .bottom) {
                            Rectangle()
                                .fill(current ? HUD.accent : HUD.lineDim)
                                .frame(width: cell - 8 * ContentView.scale, height: current ? 2 * ContentView.scale : 1)
                                .glow(current, radius: 2 * ContentView.scale)
                                .offset(y: 2 * ContentView.scale)
                        }
                }
            }
        }
        .frame(height: cell)
    }
}

/// 자판 지도(Mac의 K): 손마다 지금 방향의 네 키(선 HUD 칸). 꼬집는 키는 파랑.
private struct KeyboardMap: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let scale = ContentView.scale
        HStack(spacing: 40 * scale) {
            ForEach(AppModel.hands, id: \.self) { hand in
                let info = model.state.hands?[hand]
                HStack(spacing: 5 * scale) {
                    ForEach(0..<4, id: \.self) { finger in
                        let name = info?.map[safe: finger] ?? nil
                        let active = info?.fingers[safe: finger]?.active ?? false
                        Text(name ?? "")
                            .font(name.map { $0.count <= 3 && $0 != ".,?!" } ?? true ? ContentView.normal : ContentView.small)
                            .foregroundStyle(HUD.white)
                            .frame(width: 50 * scale, height: 28 * scale)
                            .background(LinePanel(active: active, cornerRadius: 4 * scale))
                            .opacity(name == nil ? 0 : 1)
                    }
                }
            }
        }
    }
}
