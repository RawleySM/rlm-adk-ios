import Foundation
@testable import RLMADK

/// Records what the controller asks of the voice output.
final class FakeVoiceOutput: VoiceOutput {
    var onPlayingChange: ((Bool) -> Void)?
    private(set) var stops = 0
    private(set) var flushes = 0
    private(set) var turnEnds = 0
    private(set) var gateHistory: [Bool] = []
    var gateOpen: Bool = true { didSet { gateHistory.append(gateOpen) } }
    var isPlaying: Bool { false }
    var statusLine: String { "fake voice: gate \(gateOpen ? "open" : "closed") stops \(stops)" }
    func stop() { stops += 1 }
    func flush() { flushes += 1 }
    func botTurnEnded() { turnEnds += 1 }
}

/// A talk client that is "ready" as soon as it connects and records every message sent.
@MainActor
final class FakeTalkClient: TalkClient {
    let kind: TalkTransportKind
    private(set) var state: TalkConnectionState = .disconnected(nil)
    let fakeVoice = FakeVoiceOutput()
    var voice: VoiceOutput { fakeVoice }
    var onEvent: ((TalkEvent) -> Void)?
    var onState: ((TalkConnectionState) -> Void)?
    var onLog: ((String) -> Void)?
    private(set) var sent: [TalkClientMessage] = []
    private(set) var connectedTo: URL?
    var detail: String { "fake \(kind.rawValue)" }

    init(kind: TalkTransportKind) { self.kind = kind }

    func connect(baseURL: URL) {
        connectedTo = baseURL
        state = .ready
        onState?(.ready)
    }

    func disconnect() {
        state = .disconnected(nil)
    }

    func send(_ message: TalkClientMessage) { sent.append(message) }

    /// Floor modes sent, in order.
    var modes: [ClientFloorMode] {
        sent.compactMap { if case .clientMode(let mode, _) = $0 { return mode } else { return nil } }
    }

    var texts: [String] {
        sent.compactMap { if case .clientText(let text) = $0 { return text } else { return nil } }
    }
}
