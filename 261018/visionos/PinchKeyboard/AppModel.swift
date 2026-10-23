import Foundation
import Observation
import SwiftUI
import QuartzCore
import simd

/// 앱 상태: 판정 Python 세션, 손 추적 루프, 화면에 그릴 상태와 손 배치.
@MainActor
@Observable
final class AppModel {
    static let immersiveID = "hands"
    static let hands = ["Left", "Right"]

    var state = PinchState()
    var status = "준비 중"
    var handsRunning = false
    var showMap = false  // 자판 지도(Mac의 K)
    /// 손(팔) 가림. 혼합 몰입에서 .visible은 깊이와 관계없이 손이 항상 위(라벨이 손에 가려짐), .hidden은 가상 콘텐츠가
    /// 항상 손 위, .automatic은 깊이에 따라 정한다(WWDC24 "Render Metal with passthrough in visionOS"). 시스템이 따르지
    /// 않을 수 있는 요청이라 기기에서 고를 수 있게 둔다. 기본은 Mac처럼 HUD를 손 위에 그리는 .hidden.
    var limbVisibility: Visibility = .hidden
    /// 손 골격선·엄지 끝 점·손 이름. Vision Pro에서는 기본으로 숨기고 버튼(손끝 라벨·다가옴 호·순환 대기 글자)과
    /// 주먹 지움 표시만 띄운다(사용자, 2026-10-23). 설정 창에서 켤 수 있고 기기에 저장된다.
    var showGuides = UserDefaults.standard.bool(forKey: "PinchKeyboard.showGuides") {
        didSet { UserDefaults.standard.set(showGuides, forKey: "PinchKeyboard.showGuides") }
    }
    /// 판정 규칙 켜기·끄기(설정 창). 바꾸면 저장하고 판정 Python에 바로 적용한다.
    var settings = RuleSettings.load() {
        didSet {
            guard settings != oldValue else {
                return
            }
            settings.save()
            Task { await self.applySettings() }
        }
    }
    var layouts: [String: HandLayout] = [:]  // 손 → 이번 프레임 배치
    var frameTime: Double = 0  // 이번 프레임 시각(순환 애니메이션)
    var cycles: [String: CycleMotion] = [:]  // "Left-0" → 순환 애니메이션 상태
    /// 모음 끌기 궤적(월드 m): 손 → 꼬집은 지점(엄지 끝과 손끝의 가운데) 목록. 판정 Python이 끌기에 쓰는 프레임과
    /// 같은 프레임들이다(dragFrames). 모음 버튼을 꼬집는 동안만 있다.
    var trails: [String: [SIMD3<Float>]] = [:]

    private let engine = PythonEngine()
    private let tracker = HandTracker()
    private var loop: Task<Void, Never>?
    private var pythonReady = false
    /// 손마다 판정 Python에 넘긴 프레임의 관절(월드). 끌기 궤적을 같은 프레임으로 그리려고 최근 것만 둔다.
    private var history: [String: [[SIMD3<Float>]]] = [:]
    private static let historyLimit = 300  // 약 6.7초(45Hz)

    /// 기록 파일 폴더(앱 Documents). 파일 앱의 "나의 Vision Pro"에서 보인다.
    var logDirectory: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    func prepare() async {
        if pythonReady {
            return
        }
        if let error = await engine.start(logDirectory: logDirectory) {
            status = "Python 시작 실패: \(error)"
            print("PinchKeyboard: \(status)")
            return
        }
        pythonReady = true
        await applySettings()  // 저장된 판정 규칙 설정
        // 자기 점검: 내장 Python 버전과 고정 데이터 재생 결과(콘솔에도 남겨 Mac에서 확인할 수 있게)
        apply(await engine.call("command", #"{"cmd": "info"}"#))
        print("PinchKeyboard: \(status)")
        status += " · 손 입력 시작을 누르세요"
    }

    func applySettings() async {
        guard pythonReady else {
            return
        }
        apply(await engine.call("command", settings.command))
    }

    func command(_ name: String) async {
        await prepare()
        guard pythonReady else {
            return
        }
        apply(await engine.call("command", #"{"cmd": "\#(name)"}"#))
    }

    func startHands() async {
        await prepare()
        guard pythonReady, loop == nil else {
            return
        }
        if let error = await tracker.start() {
            status = error
            return
        }
        handsRunning = true
        status = "손 입력 중"
        loop = Task { [weak self] in
            await self?.runLoop()
        }
    }

    func stopHands() {
        loop?.cancel()
        loop = nil
        tracker.stop()
        handsRunning = false
        layouts = [:]
        trails = [:]
        history = [:]
        status = "손 입력 멈춤"
    }

    /// 프레임마다: 손 관절 → 판정 Python → 상태·손 배치. 약 45Hz.
    private func runLoop() async {
        while !Task.isCancelled {
            let frameStart = CACurrentMediaTime()
            let hands = tracker.currentHands()
            for (label, hand) in hands {
                history[label, default: []].append(hand.world)
                if let count = history[label]?.count, count > Self.historyLimit {
                    history[label]?.removeFirst(count - Self.historyLimit)
                }
            }
            apply(await engine.call("step", Self.payload(time: frameStart, hands: hands)))
            layouts = hands.mapValues { HandLayout(points: $0.world) }
            trails = dragTrails()
            let rest = 1.0 / 45.0 - (CACurrentMediaTime() - frameStart)
            try? await Task.sleep(for: .seconds(max(0.002, rest)))
        }
    }

    private func apply(_ json: String) {
        guard let decoded = PinchState.decode(json) else {
            status = "판정 결과를 읽지 못했습니다"
            return
        }
        if let error = decoded.error {
            status = "판정 오류: " + (error.split(separator: "\n").last.map(String.init) ?? error)
            print(error)
            return
        }
        state = decoded
        frameTime = CACurrentMediaTime()
        for hand in Self.hands {
            for (finger, info) in (decoded.hands?[hand]?.fingers ?? []).enumerated() {
                cycles["\(hand)-\(finger)", default: CycleMotion()].update(info.queue, now: frameTime)
            }
        }
        if let notice = decoded.notice {
            status = notice
        }
    }

    /// 모음 버튼을 꼬집는 손마다: 판정이 쓰는 마지막 dragFrames 프레임의 꼬집은 지점(엄지 끝과 그 손가락 끝의 가운데).
    private func dragTrails() -> [String: [SIMD3<Float>]] {
        var trails: [String: [SIMD3<Float>]] = [:]
        for hand in Self.hands {
            guard let info = state.hands?[hand], let finger = info.dragging, info.dragFrames > 0,
                  let frames = history[hand], let tip = HandLayout.fingerTips[safe: finger] else {
                continue
            }
            trails[hand] = frames.suffix(info.dragFrames).map { ($0[4] + $0[tip]) / 2 }
        }
        return trails
    }

    static func payload(time: Double, hands: [String: HandTracker.Hand]) -> String {
        func rows(_ points: [SIMD3<Float>]) -> [[Double]] {
            points.map { [Double($0.x), Double($0.y), Double($0.z)] }
        }
        var handsJSON: [String: Any] = [:]
        for (label, hand) in hands {
            handsJSON[label] = ["world": rows(hand.world), "image": rows(hand.image)]
        }
        let object: [String: Any] = ["t": time, "hands": handsJSON]
        guard let data = try? JSONSerialization.data(withJSONObject: object),
              let text = String(data: data, encoding: .utf8) else {
            return #"{"t": 0, "hands": {}}"#
        }
        return text
    }

    func finger(_ hand: String, _ index: Int) -> PinchState.Finger? {
        state.hands?[hand]?.fingers[safe: index]
    }

    /// 손 이름과 방향(Mac draw_hud의 손목 아래 문구).
    func caption(_ hand: String) -> String {
        guard let info = state.hands?[hand] else {
            return ""
        }
        var caption = hand == "Left" ? "왼손" : "오른손"
        switch info.face {
        case "back":
            caption += " · " + (hand == "Left" ? "손등 모음·편집" : "손등 확정/공백")
        case "palm":
            caption += " · 손바닥"
        case "turning":
            caption += " · 회전 중"
        default:
            break
        }
        if !info.armed {
            caption += " · 손을 펴세요"
        }
        return caption
    }
}

extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
