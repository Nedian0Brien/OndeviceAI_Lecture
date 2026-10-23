import SwiftUI

/// 설정 창: 판정 규칙 켜기·끄기와 손 가림. 바꾸면 바로 적용되고 기기에 저장된다(손 가림은 이번 실행에만).
struct SettingsView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        @Bindable var model = model
        Form {
            Section("입력 판정") {
                Toggle(isOn: $model.settings.hold) {
                    Text("누르고 유지(0.3초)")
                    Text("꼬집고 0.3초 머물러야 입력합니다. 끄면 손끝을 뗄 때 입력합니다.")
                }
                Toggle(isOn: $model.settings.settle) {
                    Text("손을 뒤집은 직후 0.3초 무시")
                    Text("손바닥·손등을 바꾼 직후의 꼬집기를 받지 않습니다. 손을 돌리며 엄지가 검지 옆에 놓이는 것이 입력되던 문제를 막습니다.")
                }
            }
            Section("Mac 카메라 기록으로 넣은 규칙") {
                Toggle(isOn: $model.settings.indexSweep) {
                    Text("오른손 검지 지나감")
                    Text("다른 손가락을 뗀 직후 0.25초 안의 검지 접촉, 검지가 굽지 않은 접촉을 버립니다. ㄱ이 잘 안 들어가면 꺼 보세요.")
                }
                Toggle(isOn: $model.settings.occlusion) {
                    Text("엄지 가림(기울기)")
                    Text("손이 소지 쪽으로 25° 넘게 기울면 손바닥 꼬집기를 받지 않습니다. 카메라 한 대에서 엄지가 가려지던 문제용입니다.")
                }
            }
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
