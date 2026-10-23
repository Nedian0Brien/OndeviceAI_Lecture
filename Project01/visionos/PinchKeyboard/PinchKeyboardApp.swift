import SwiftUI

/// PinchKeyboard: 손끝 꼬집기 한글 입력기(Vision Pro 시험 앱). 판정은 앱에 넣은 Python(Project01/visionos/pinch_vision.py와 Project01의 판정 모듈)이 한다.
@main
struct PinchKeyboardApp: App {
    static let settingsID = "settings"
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(model)
        }
        .defaultSize(width: 760, height: 620)

        WindowGroup(id: Self.settingsID) {
            SettingsView()
                .environment(model)
        }
        .defaultSize(width: 620, height: 640)

        ImmersiveSpace(id: AppModel.immersiveID) {
            ImmersiveView()
                .environment(model)
                // 손바닥을 보면 뜨는 시스템 표시(홈)를 숨겨 달라는 요청. 시스템이 따르지 않을 수 있다.
                .persistentSystemOverlays(.hidden)
        }
        .immersionStyle(selection: .constant(.mixed), in: .mixed)
        .upperLimbVisibility(model.limbVisibility)
        // 장면에도 건다(Apple 포럼 예시: ImmersiveSpace 장면에 걸어야 효과가 있다는 보고). 손바닥을 보는 것만으로는 홈 표시가
        // 바로 뜨지 않게 되지만, 손바닥을 보고 엄지-검지 탭을 하면 여전히 홈이 열린다(Apple 엔지니어 답변, 끄는 API 없음).
        .persistentSystemOverlays(.hidden)
    }
}
