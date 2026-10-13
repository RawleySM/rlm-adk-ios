import AVFoundation
import Foundation
import Speech

/// Which capture a recognition request belongs to. Each request is bound to the owner in force
/// when it was opened; that identity travels with the request's final, however late it settles.
/// Changing the owner ends the open request (its final still arrives under the old owner) and the
/// next request opens under the new one. No timing is involved.
struct UtteranceLedger: Equatable {
    /// Owner for the next request (a prayer capture id, or nil for conversation).
    private(set) var nextOwner: String?
    /// Owner bound to the request currently open, if any.
    private(set) var openOwner: String?
    private(set) var openId: Int?

    /// A request opens: it takes the owner in force now.
    mutating func opened(id: Int) { openId = id; openOwner = nextOwner }

    /// The open request settled (final, no speech or error).
    mutating func closed(id: Int) { if openId == id { openId = nil; openOwner = nil } }

    /// Owner carried by a final from request `id` (nil when no such request is open).
    func owner(of id: Int) -> String? { openId == id ? openOwner : nil }

    /// Rebinds future requests; returns true when an open request must be ended so that its
    /// final is delivered under the old owner before the new owner takes effect.
    mutating func rebind(_ owner: String?) -> Bool {
        nextOwner = owner
        return openId != nil && openOwner != owner
    }
}

/// On-device `SFSpeechRecognizer` fed from the audio owner's tap. Restarted per utterance without
/// touching the recorder. Reports whether on-device recognition is actually supported here.
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

    private let recognizer: SFSpeechRecognizer?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private(set) var availability: Availability
    private(set) var isListening = false
    /// Supervised mode: after every final, no-speech or error the request is reopened so the
    /// cascade stays available between exchanges without touching the recorder.
    private(set) var continuous = false
    private var utteranceId = 0
    private(set) var ledger = UtteranceLedger()
    private var lastFinal: (text: String, at: Date)?
    private(set) var restartCount = 0
    private(set) var lastRestartReason = ""
    var onPartial: ((String) -> Void)?
    /// Final text with the owner (capture id) the request was opened under; nil = conversation.
    var onFinal: ((String, String?) -> Void)?
    var onGap: ((GapRecord) -> Void)?
    var onAvailability: ((Availability) -> Void)?
    var onRestart: ((String) -> Void)?

    override init() {
        let locale = Locale(identifier: "en-US")
        recognizer = SFSpeechRecognizer(locale: locale)
        let supported = recognizer?.supportsOnDeviceRecognition ?? false
        availability = Availability(authorized: SFSpeechRecognizer.authorizationStatus() == .authorized, recognizerAvailable: recognizer?.isAvailable ?? false, onDeviceSupported: supported, locale: locale.identifier, detail: supported ? "" : "on-device recognition is not supported for this locale/device")
        super.init()
        recognizer?.delegate = self
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

    /// Starts one utterance. Audio keeps recording regardless; this only opens a recognition request.
    func start() {
        guard availability.usable, let recognizer else { return }
        cancelTask()
        utteranceId += 1
        let id = utteranceId
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = true
        request.taskHint = .dictation
        self.request = request
        isListening = true
        ledger.opened(id: id)
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self, id == self.utteranceId else { return } // stale utterance: never re-submit
            if let result {
                let text = result.bestTranscription.formattedString
                if result.isFinal {
                    self.isListening = false
                    let owner = self.ledger.owner(of: id)
                    self.ledger.closed(id: id)
                    self.deliverFinal(text, owner: owner)
                    self.scheduleRestart(after: 0.2, reason: "final")
                } else {
                    self.onPartial?(text)
                }
            }
            if let error {
                self.isListening = false
                self.ledger.closed(id: id)
                let code = (error as NSError).code
                // 1110 = no speech detected; 216/301 = cancelled. Report others as recognition gaps.
                if code != 1110, code != 216, code != 301 {
                    self.onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "recognition_error_\(code)", kind: "recognition"))
                    self.scheduleRestart(after: 1.0, reason: "error_\(code)")
                } else if code == 1110 {
                    self.scheduleRestart(after: 0.2, reason: "no_speech")
                }
            }
        }
    }

    /// Identical final text within three seconds (restart overlap) is delivered once.
    private func deliverFinal(_ text: String, owner: String?) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        if let last = lastFinal, last.text == trimmed, Date().timeIntervalSince(last.at) < 3 { return }
        lastFinal = (trimmed, Date())
        onFinal?(trimmed, owner)
    }

    /// Binds future requests to `owner` (a prayer capture id, or nil for conversation). An open
    /// request under a different owner is ended now: its final, whenever it settles, is delivered
    /// under the owner it was opened with, and the supervised restart opens the next request under
    /// the new owner. Audio recording is untouched.
    func rebindOwner(_ owner: String?) {
        if ledger.rebind(owner), isListening {
            request?.endAudio()
        }
    }

    private func scheduleRestart(after delay: Double, reason: String) {
        guard continuous else { return }
        let id = utteranceId
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.continuous, id == self.utteranceId, !self.isListening else { return }
            self.restartCount += 1
            self.lastRestartReason = reason
            self.onRestart?(reason)
            self.start()
        }
    }

    private func cancelTask() {
        task?.cancel()
        task = nil
        request?.endAudio()
        request = nil
        isListening = false
        if let id = ledger.openId { ledger.closed(id: id) }
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        guard isListening else { return }
        request?.append(buffer)
    }

    /// Ends the utterance; a final result (if any) follows through `onFinal`.
    func endUtterance() { request?.endAudio() }

    func stop() {
        continuous = false
        utteranceId += 1
        cancelTask()
    }
}

extension Recognizer: SFSpeechRecognizerDelegate {
    func speechRecognizer(_ speechRecognizer: SFSpeechRecognizer, availabilityDidChange available: Bool) {
        availability.recognizerAvailable = available
        if !available { availability.detail = "recognizer unavailable" }
        onAvailability?(availability)
    }
}
