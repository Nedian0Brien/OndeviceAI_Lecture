import SwiftUI

/// Mac 앱 HUD(261018/pinch_cheonjiin.py, common/hud.py)와 같은 색·모양.
/// 선 HUD(2026-10-23): 가는 선과 눈금으로 정보를 나타내고 강조는 채도 낮은 파랑 한 가지다.
enum HUD {
    static func rgba(_ r: Double, _ g: Double, _ b: Double, _ a: Double = 255) -> Color {
        Color(.sRGB, red: r / 255, green: g / 255, blue: b / 255, opacity: a / 255)
    }

    static let white = rgba(250, 250, 252)
    static let muted = rgba(230, 231, 236, 215)  // 보조 문구
    static let dim = rgba(200, 202, 210, 150)
    static let outline = rgba(28, 29, 34, 170)  // 밝은 배경 위 글자 외곽선
    static let deleteRed = rgba(255, 128, 116)
    static let promptYellow = rgba(255, 214, 102)

    // common/hud.py 선 HUD 색과 같은 값
    static let line = rgba(236, 240, 248, 225)  // 기본 선
    static let lineDim = rgba(236, 240, 248, 95)  // 보조 선·꺼진 눈금
    static let accent = rgba(158, 197, 255)  // 강조 선·켜진 눈금(#9EC5FF)
    static let accentFill = rgba(46, 74, 128, 150)  // 강조 면(#2E4A80 반투명)
    static let pressedFill = rgba(46, 74, 128, 210)  // 누르는 손끝 버튼 안쪽(Mac: 강조 면 + 60)
    static let backdrop = rgba(8, 10, 16, 120)  // 밝은 배경 위에서도 선·글자가 보이게 까는 어두운 면
}

/// 각도는 12시 0°에서 시계 방향(화면 좌표, y 아래). common.hud.HudCanvas.polar와 같다.
enum Polar {
    static func point(_ center: CGPoint, _ radius: CGFloat, _ degrees: Double) -> CGPoint {
        let a = degrees * .pi / 180
        return CGPoint(x: center.x + radius * CGFloat(sin(a)), y: center.y - radius * CGFloat(cos(a)))
    }

    static func arc(_ center: CGPoint, _ radius: CGFloat, _ start: Double, _ end: Double, step: Double = 2) -> [CGPoint] {
        let count = max(2, Int(abs(end - start) / step) + 1)
        return (0..<count).map { point(center, radius, start + (end - start) * Double($0) / Double(count - 1)) }
    }
}

/// 선 HUD 그리기 도구(common.hud.HudCanvas의 ticks·arc·band·dashed_circle·pointer와 같은 규칙).
extension GraphicsContext {
    /// 반지름 radius에서 바깥으로 length만큼의 짧은 눈금 count개. 12시(start)부터 시계 방향으로 lit개는 litColor(켜짐).
    func ticks(center: CGPoint, radius: CGFloat, length: CGFloat, count: Int, color: Color, lit: Int = 0,
               litColor: Color? = nil, litLength: CGFloat? = nil, width: CGFloat = 1, litWidth: CGFloat? = nil,
               start: Double = 0, end: Double = 360) {
        let span = end - start
        let step = span >= 360 ? span / Double(count) : span / Double(max(1, count - 1))
        for k in 0..<count {
            let on = k < lit
            let angle = start + Double(k) * step
            var path = Path()
            path.move(to: Polar.point(center, radius, angle))
            path.addLine(to: Polar.point(center, radius + (on ? (litLength ?? length) : length), angle))
            stroke(path, with: .color(on ? (litColor ?? color) : color), lineWidth: on ? (litWidth ?? width) : width)
        }
    }

    /// 일부만 그린 원.
    func arc(center: CGPoint, radius: CGFloat, start: Double, end: Double, color: Color, width: CGFloat = 1) {
        var path = Path()
        path.addLines(Polar.arc(center, radius, start, end))
        stroke(path, with: .color(color), lineWidth: width)
    }

    /// 반지름 inner~outer의 두꺼운 호(반투명 띠).
    func band(center: CGPoint, inner: CGFloat, outer: CGFloat, start: Double, end: Double, color: Color) {
        var path = Path()
        path.addLines(Polar.arc(center, outer, start, end) + Polar.arc(center, inner, end, start))
        path.closeSubpath()
        fill(path, with: .color(color))
    }

    /// 파선 원(보조 기준선).
    func dashedCircle(center: CGPoint, radius: CGFloat, color: Color, dashes: Int = 32, width: CGFloat = 1) {
        let dash = 2 * .pi * radius / CGFloat(dashes) / 2
        let circle = Path(ellipseIn: CGRect(x: center.x - radius, y: center.y - radius, width: 2 * radius,
                                             height: 2 * radius))
        stroke(circle, with: .color(color), style: StrokeStyle(lineWidth: width, dash: [dash, dash]))
    }

    /// 원 바깥(반지름 radius)에서 degrees 방향을 가리키는 작은 삼각형.
    func pointer(center: CGPoint, radius: CGFloat, degrees: Double, size: CGFloat, color: Color) {
        let spread = Double(size / max(radius, 1)) * 40
        var path = Path()
        path.addLines([Polar.point(center, radius + size, degrees), Polar.point(center, radius, degrees - spread),
                       Polar.point(center, radius, degrees + spread)])
        path.closeSubpath()
        fill(path, with: .color(color))
    }
}

/// 선 HUD 패널(common.hud.HudCanvas.panel): 어두운 면 + 가는 테두리. active면 강조 면·테두리.
struct LinePanel: View {
    var active = false
    var cornerRadius: CGFloat = 6

    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(active ? HUD.accentFill : HUD.backdrop)
            .overlay(RoundedRectangle(cornerRadius: cornerRadius).stroke(active ? HUD.accent : HUD.lineDim, lineWidth: 1))
    }
}

/// 가는 원 + 안쪽 면(common.hud.HudCanvas.hairline_circle).
struct HairlineCircle: View {
    var edge: Color
    var fill: Color = HUD.backdrop
    var lineWidth: CGFloat = 1

    var body: some View {
        Circle()
            .fill(fill)
            .overlay(Circle().stroke(edge, lineWidth: lineWidth))
    }
}

extension View {
    /// 외곽선을 두른 글자(Mac HUD의 write(..., outline=True)): 밝은 배경 위에서도 읽히게 한다.
    func outlined(_ radius: CGFloat = 1) -> some View {
        shadow(color: HUD.outline, radius: radius)
            .shadow(color: HUD.outline, radius: radius)
    }

    /// 강조 요소의 번짐(Mac: 번짐 층을 흐려 아래 깐다).
    func glow(_ on: Bool, radius: CGFloat) -> some View {
        shadow(color: on ? HUD.accent.opacity(0.8) : .clear, radius: radius)
    }
}
