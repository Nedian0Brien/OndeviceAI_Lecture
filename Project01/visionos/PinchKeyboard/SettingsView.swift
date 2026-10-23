import SwiftUI

/// 설정 창: 화면 표시(손 골격·안내, 손 가림). 판정 규칙은 Mac 카메라 앱과 같은 값으로 고정이다.
struct SettingsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section("화면") {
                Toggle(isOn: $model.showGuides) {
                    Text("손 골격·안내 표시")
                    Text("손 골격선, 엄지 끝 점, 손목 아래 손 이름을 띄웁니다. 끄면 버튼(다가옴 호 포함)과 지움 표시만 보입니다.")
                }
                Picker("손 가림", selection: $model.limbVisibility) {
                    Text("라벨이 손 위").tag(Visibility.hidden)
                    Text("깊이대로").tag(Visibility.automatic)
                    Text("손이 라벨 위").tag(Visibility.visible)
                }
                Text("바로 반영되지 않으면 손 입력을 껐다 켜세요.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("설정")
        .padding()
    }
}
