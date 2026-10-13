import AVFoundation
import Foundation
import Speech

/// The part of one speech request the recognizer drives. The real handle wraps
/// `SFSpeechAudioBufferRecognitionRequest` and its task; tests substitute a fake to exercise the
/// request lifecycle (accepting, ended, settled, cancelled) without the Speech framework.
protocol RecognitionRequestHandle: AnyObject {
    func append(_ buffer: AVAudioPCMBuffer)
    /// Apple's contract: no more audio will be appended; the final result (or an error) follows.
    func endAudio()
    func cancel()
}

/// Opens recognition requests. `onResult(text, isFinal, error)` may be called several times
/// (partials) and ends with a final or an error.
protocol RecognitionEngine {
    func open(onResult: @escaping (String?, Bool, Error?) -> Void) -> RecognitionRequestHandle
}

/// On-device `SFSpeechRecognizer` requests.
final class SpeechEngine: RecognitionEngine {
    private let recognizer: SFSpeechRecognizer

    init(_ recognizer: SFSpeechRecognizer) { self.recognizer = recognizer }

    final class Handle: RecognitionRequestHandle {
        let request = SFSpeechAudioBufferRecognitionRequest()
        var task: SFSpeechRecognitionTask?
        func append(_ buffer: AVAudioPCMBuffer) { request.append(buffer) }
        func endAudio() { request.endAudio() }
        func cancel() { task?.cancel() }
    }

    func open(onResult: @escaping (String?, Bool, Error?) -> Void) -> RecognitionRequestHandle {
        let handle = Handle()
        handle.request.shouldReportPartialResults = true
        handle.request.requiresOnDeviceRecognition = true
        handle.request.taskHint = .dictation
        handle.task = recognizer.recognitionTask(with: handle.request) { result, error in
            onResult(result?.bestTranscription.formattedString, result?.isFinal ?? false, error)
        }
        return handle
    }
}

/// On-device recognition fed from the audio owner's tap. Restarted per utterance without touching
/// the recorder. Every request is bound, immutably, to the owner in force when it was opened (a
/// prayer capture id, or nil for conversation); that identity travels with its final however late
/// it settles. Requests move through: accepting audio -> ended (draining: no more buffers, final
/// pending) -> settled. Rebinding or restarting ENDS the accepting request and opens the next one
/// at once; nothing pending is cancelled, so no final is lost and the new owner gets audio
/// immediately. Only an explicit `stop()` cancels.
final class Recognizer: NSObject {
    struct Availability: Equatable {
        var authorized: Bool
        var recognizerAvailable: Bool
        var onDeviceSupported: Bool
        var locale: String
        var detail: String

        var usable: Bool { authorized && recognizerAvailable && onDeviceSupported }
        var label: String {
            if usable { return "On-device recognition (\(locale))" }
            return "Live voice unavailable: \(detail)"
        }
    }

    /// One request. `owner` never changes after opening.
    final class OpenRequest {
        let id: Int
        let owner: String?
        var handle: RecognitionRequestHandle?
        /// True until `endAudio`; buffers are appended only while accepting.
        private(set) var accepting = true
        private(set) var cancelled = false
        init(id: Int, owner: String?) { self.id = id; self.owner = owner }
        func end() { guard accepting else { return }; accepting = false; handle?.endAudio() }
        /// The engine delivered the final or an error: nothing more can be appended, no call needed.
        func markSettled() { accepting = false }
        func cancelNow() { accepting = false; cancelled = true; handle?.cancel() }
    }

    private let recognizer: SFSpeechRecognizer?
    private let engine: RecognitionEngine?
    private(set) var availability: Availability
    /// The request accepting audio now, if any.
    private(set) var current: OpenRequest?
    /// Ended requests whose final has not settled yet. Never cancelled by rebinding or restarting.
    private(set) var draining: [OpenRequest] = []
    /// Owner for the next request opened (prayer capture id, or nil for conversation).
    private(set) var nextOwner: String?
    /// Supervised mode: after every final, no-speech or error the request is reopened so the
    /// cascade stays available between exchanges without touching the recorder.
    private(set) var continuous = false
    private var nextId = 0
    private var lastFinalByOwner: [String: (text: String, at: Date)] = [:]
    private(set) var restartCount = 0
    private(set) var lastRestartReason = ""
    var onPartial: ((String) -> Void)?
    /// Final text with the owner the request was opened under; nil = conversation.
    var onFinal: ((String, String?) -> Void)?
    var onGap: ((GapRecord) -> Void)?
    var onAvailability: ((Availability) -> Void)?
    var onRestart: ((String) -> Void)?

    var isListening: Bool { current?.accepting == true }
    var currentOwner: String? { current?.owner }

    override init() {
        let locale = Locale(identifier: "en-US")
        let recognizer = SFSpeechRecognizer(locale: locale)
        self.recognizer = recognizer
        engine = recognizer.map { SpeechEngine($0) }
        let supported = recognizer?.supportsOnDeviceRecognition ?? false
        availability = Availability(authorized: SFSpeechRecognizer.authorizationStatus() == .authorized, recognizerAvailable: recognizer?.isAvailable ?? false, onDeviceSupported: supported, locale: locale.identifier, detail: supported ? "" : "on-device recognition is not supported for this locale/device")
        super.init()
        recognizer?.delegate = self
    }

    /// Test seam: a fake engine and a chosen availability; no Speech framework involved.
    init(engine: RecognitionEngine, availability: Availability) {
        recognizer = nil
        self.engine = engine
        self.availability = availability
        super.init()
    }

    func requestAuthorization(_ completion: @escaping (Availability) -> Void) {
        SFSpeechRecognizer.requestAuthorization { [weak self] status in
            DispatchQueue.main.async {
                guard let self else { return }
                self.availability.authorized = status == .authorized
                if status != .authorized { self.availability.detail = "speech recognition not authorized (\(status.rawValue))" }
                self.onAvailability?(self.availability)
                completion(self.availability)
            }
        }
    }

    /// Keeps a recognition request open continuously (restarted per utterance) until `stop()`.
    /// Idempotent: a second call while already listening continuously is a no-op.
    func startContinuous() {
        if continuous, isListening { return }
        continuous = true
        start()
    }

    /// Opens a request under `nextOwner`. A request still accepting audio is ended first (its final
    /// is still delivered), never cancelled. Audio keeps recording regardless.
    func start() {
        guard availability.usable, let engine else { return }
        endCurrent()
        nextId += 1
        let request = OpenRequest(id: nextId, owner: nextOwner)
        request.handle = engine.open { [weak self, weak request] text, isFinal, error in
            guard let self, let request else { return }
            self.handleResult(request, text: text, isFinal: isFinal, error: error)
        }
        current = request
    }

    /// Binds future requests to `owner`. If the accepting request has a different owner it is ended
    /// now (it drains and delivers its final under its own owner) and, in supervised mode, the next
    /// request opens immediately under the new owner so no audio is lost between the two.
    func rebindOwner(_ owner: String?) {
        nextOwner = owner
        guard let request = current, request.owner != owner else { return }
        endCurrent()
        if continuous { start() }
    }

    /// Ends the utterance; its final (if any) follows through `onFinal`, then the supervised
    /// restart opens the next request.
    func endUtterance() { endCurrent() }

    func append(_ buffer: AVAudioPCMBuffer) {
        guard let request = current, request.accepting else { return }
        request.handle?.append(buffer)
    }

    func stop() {
        continuous = false
        current?.cancelNow()
        current = nil
        draining.forEach { $0.cancelNow() }
        draining.removeAll()
    }

    private func endCurrent() {
        guard let request = current else { return }
        request.end()
        draining.append(request)
        current = nil
    }

    private func handleResult(_ request: OpenRequest, text: String?, isFinal: Bool, error: Error?) {
        guard !request.cancelled else { return }
        if let text, !isFinal, current == nil || request === current { onPartial?(text) }
        var settled = false
        var restartDelay = 0.2
        var reason = "final"
        if isFinal {
            settled = true
            if let text { deliverFinal(text, owner: request.owner) }
        }
        if let error {
            settled = true
            let code = (error as NSError).code
            // 1110 = no speech detected; 216/301 = cancelled. Report others as recognition gaps.
            if code != 1110, code != 216, code != 301 {
                onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "recognition_error_\(code)", kind: "recognition"))
                restartDelay = 1.0
                reason = "error_\(code)"
            } else if code == 1110 {
                reason = "no_speech"
            } else {
                reason = "cancelled"
            }
        }
        guard settled else { return }
        request.markSettled()
        draining.removeAll { $0 === request }
        if current === request { current = nil }
        // Only when nothing is accepting audio does the supervised cascade reopen; a request that
        // settles while a newer one is already open restarts nothing.
        if current == nil, continuous, reason != "cancelled" { scheduleRestart(after: restartDelay, reason: reason) }
    }

    /// Identical final text within three seconds from the same owner (restart overlap) is
    /// delivered once. The same words under another owner are a different utterance.
    private func deliverFinal(_ text: String, owner: String?) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let key = owner ?? ""
        if let last = lastFinalByOwner[key], last.text == trimmed, Date().timeIntervalSince(last.at) < 3 { return }
        lastFinalByOwner[key] = (trimmed, Date())
        onFinal?(trimmed, owner)
    }

    private func scheduleRestart(after delay: Double, reason: String) {
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.continuous, self.current == nil else { return }
            self.restartCount += 1
            self.lastRestartReason = reason
            self.onRestart?(reason)
            self.start()
        }
    }
}

extension Recognizer: SFSpeechRecognizerDelegate {
    func speechRecognizer(_ speechRecognizer: SFSpeechRecognizer, availabilityDidChange available: Bool) {
        availability.recognizerAvailable = available
        if !available { availability.detail = "recognizer unavailable" }
        onAvailability?(availability)
    }
}
