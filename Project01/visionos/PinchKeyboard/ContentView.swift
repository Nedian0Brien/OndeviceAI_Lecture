import SwiftUI

/// 입력 창(visionOS 기본 모양). 손 입력 전에는 제목과 모드 버튼 두 개(자유 입력 / 단어 입력), 입력 중에는 입력 화면.
/// 손 위 HUD(선 HUD)와 달리 창은 시스템 기본 모양으로 둔다(2026-10-23 사용자).
struct ContentView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openImmersiveSpace) private var openImmersiveSpace
    @Environment(\.dismissImmersiveSpace) private var dismissImmersiveSpace
    @Environment(\.openWindow) private var openWindow

    private var solved: Bool { model.handsRunning && model.state.word?.solved == true }

    var body: some View {
        Group {
            if model.handsRunning {
                typing
            } else {
                home
            }
        }
        .padding(40)
        .background(solved ? Color.green.opacity(0.35) : Color.clear)  // 단어를 맞히면 창 전체를 초록으로
        .animation(.easeOut(duration: 0.2), value: solved)
        .task { await model.prepare() }
    }

    // MARK: 첫 화면

    private var home: some View {
        VStack(spacing: 28) {
            Text("PinchKeyboard")
                .font(.extraLargeTitle)
            HStack(spacing: 24) {
                modeButton("자유 입력", systemImage: "keyboard", mode: "free")
                modeButton("단어 입력", systemImage: "character.book.closed", mode: "words")
            }
            Text("엄지로 손끝을 꼬집어 천지인으로 입력합니다.\n단어 입력은 짧은 단어를 하나씩 보여 주고, 다 치면 다음 단어로 넘어갑니다.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("설정", systemImage: "gearshape") {
                openWindow(id: PinchKeyboardApp.settingsID)
            }
            Text(model.status)
                .font(.footnote)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func modeButton(_ title: String, systemImage: String, mode: String) -> some View {
        Button {
            Task { await start(mode) }
        } label: {
            VStack(spacing: 14) {
                Image(systemName: systemImage)
                    .font(.system(size: 44))
                Text(title)
                    .font(.title2)
            }
            .frame(width: 200, height: 170)
        }
        .buttonBorderShape(.roundedRectangle(radius: 28))
    }

    // MARK: 입력 화면

    private var wordMode: Bool { model.state.mode == "words" }

    private var typing: some View {
        VStack(spacing: 24) {
            HStack {
                Text(wordMode ? "단어 입력" : "자유 입력")
                    .font(.title)
                Spacer()
                Button("끝내기", systemImage: "xmark") {
                    Task { await dismissImmersiveSpace() }
                }
            }
            if wordMode {
                WordPrompt()
            }
            InputLine()
            HStack(spacing: 16) {
                Button("지우기", systemImage: "delete.left") {
                    Task { await model.command("clear") }
                }
                if wordMode && !solved {
                    Button("건너뛰기", systemImage: "forward") {
                        Task { await model.command("skip") }
                    }
                }
                Spacer()
                Button("설정", systemImage: "gearshape") {
                    openWindow(id: PinchKeyboardApp.settingsID)
                }
            }
            Text(model.status)
                .font(.footnote)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
    }

    /// 모드를 정하고 손 입력(몰입 공간)을 연다.
    private func start(_ mode: String) async {
        await model.setMode(mode)
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

/// 단어 입력: 칠 단어(맞게 친 글자까지 강조색), 진행. 맞히면 "정답"과 "다음 단어" 버튼(창은 초록), 건너뛰면 잠깐 "건너뜀".
private struct WordPrompt: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let word = model.state.word
        VStack(spacing: 8) {
            if let target = word?.target, word?.solved == true {
                Text("정답")
                    .font(.headline)
                Text(target)
                    .font(.system(size: 64, weight: .semibold))
                Button("다음 단어", systemImage: "arrow.right") {
                    Task { await model.command("next") }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            } else if let target = word?.target {
                Text("이 단어를 입력하세요")
                    .font(.headline)
                    .foregroundStyle(.secondary)
                Text(highlighted(target, matched: word?.matched ?? 0))
                    .font(.system(size: 64, weight: .semibold))
            } else if let last = word?.last, last.count == 2 {  // 건너뛰고 다음 단어를 내기 전
                Text("건너뜀")
                    .font(.headline)
                    .foregroundStyle(.secondary)
                Text(last[0])
                    .font(.system(size: 64, weight: .semibold))
                    .foregroundStyle(.secondary)
            }
            Text(progress(word))
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 20)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24))
    }

    private func highlighted(_ target: String, matched: Int) -> AttributedString {
        var text = AttributedString(target)
        let end = text.index(text.startIndex, offsetByCharacters: min(matched, target.count))
        text[text.startIndex..<end].foregroundColor = .accentColor
        return text
    }

    private func progress(_ word: PinchState.WordState?) -> String {
        guard let word else {
            return ""
        }
        var parts = ["완료 \(word.done)", "건너뜀 \(word.skipped)"]
        if let seconds = word.seconds {
            parts.append("평균 " + String(format: "%.1f", seconds) + "초")
        }
        return parts.joined(separator: " · ")
    }
}

/// 입력된 글자(마지막 줄). 확정 전 후보 글자는 밑줄.
private struct InputLine: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let text = (model.state.text ?? "").components(separatedBy: "\n").last ?? ""
        Group {
            if text.isEmpty {
                Text("손끝을 꼬집어 입력")
                    .foregroundStyle(.tertiary)
            } else {
                Text(attributed(text))
                    .lineLimit(1)
                    .truncationMode(.head)
            }
        }
        .font(.largeTitle)
        .frame(maxWidth: .infinity, minHeight: 72)
        .padding(.horizontal, 24)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
    }

    private func attributed(_ text: String) -> AttributedString {
        var shown = AttributedString(text)
        if model.state.pending == true, model.state.pendingPunct != true, let last = shown.characters.indices.last {
            shown[last...].underlineStyle = .single
        }
        return shown
    }
}
