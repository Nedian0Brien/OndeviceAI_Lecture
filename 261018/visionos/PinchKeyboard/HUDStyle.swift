import SwiftUI

/// Mac 앱 HUD(261018/pinch_cheonjiin.py, common/hud.py)와 같은 색·모양.
enum HUD {
    static func rgba(_ r: Double, _ g: Double, _ b: Double, _ a: Double = 255) -> Color {
        Color(.sRGB, red: r / 255, green: g / 255, blue: b / 255, opacity: a / 255)
    }

    static let white = rgba(250, 250, 252)
    static let muted = rgba(230, 231, 236, 215)  // 보조 문구
    static let dim = rgba(200, 202, 210, 150)
    static let darkInk = rgba(28, 29, 34)  // 밝게 눌린 버튼 위 글자
    static let outline = rgba(28, 29, 34, 170)  // 밝은 배경 위 글자 외곽선
    static let okGreen = rgba(126, 224, 176)
    static let deleteRed = rgba(255, 128, 116)
    static let promptYellow = rgba(255, 214, 102)
    static let ringBase = rgba(237, 242, 250, 90)  // 눌린 손끝 둘레 원

    /// 유리 버튼 색(common.hud.HudCanvas.glass): 기본은 어두운 반투명, hover는 조금 밝게, pressed는 거의 흰색.
    static func glassFill(hover: Bool, pressed: Bool, tint: Color? = nil) -> Color {
        if pressed {
            return rgba(245, 246, 250, 215)
        }
        if let tint {
            return tint.opacity(hover ? 120.0 / 255 : 70.0 / 255)
        }
        return hover ? rgba(85, 92, 104, 200) : rgba(31, 33, 40, 140)
    }

    static func glassEdge(hover: Bool, pressed: Bool) -> Color {
        rgba(255, 255, 255, hover || pressed ? 185 : 65)
    }
}

/// 유리 원(circle) 또는 캡슐 배경.
struct GlassShape: View {
    var circle: Bool
    var hover = false
    var pressed = false
    var tint: Color? = nil
    var lineWidth: CGFloat = 1

    var body: some View {
        if circle {
            Circle()
                .fill(HUD.glassFill(hover: hover, pressed: pressed, tint: tint))
                .overlay(Circle().stroke(HUD.glassEdge(hover: hover, pressed: pressed), lineWidth: lineWidth))
        } else {
            Capsule()
                .fill(HUD.glassFill(hover: hover, pressed: pressed, tint: tint))
                .overlay(Capsule().stroke(HUD.glassEdge(hover: hover, pressed: pressed), lineWidth: lineWidth))
        }
    }
}

/// 12시 방향에서 시계 방향으로 progress만큼 그리는 호(common.hud.HudCanvas.progress_arc).
struct ProgressArc: View {
    var progress: Double
    var color: Color
    var lineWidth: CGFloat

    var body: some View {
        Circle()
            .trim(from: 0, to: max(0, min(1, progress)))
            .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .butt))
            .rotationEffect(.degrees(-90))
    }
}

extension View {
    /// 외곽선을 두른 글자(Mac HUD의 write(..., outline=True)): 밝은 배경 위에서도 읽히게 한다.
    func outlined(_ radius: CGFloat = 1) -> some View {
        shadow(color: HUD.outline, radius: radius)
            .shadow(color: HUD.outline, radius: radius)
    }
}
