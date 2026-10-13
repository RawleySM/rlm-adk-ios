import AVFoundation
import Foundation

/// `AVSpeechSynthesizer` behind the app-owned gate. `stopAll` is deterministic cancellation of the
/// current utterance and the synthesizer's own queue.
final class Speaker: NSObject, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private(set) var spokenCount = 0
    private(set) var cancelledCount = 0
    private var tags: [ObjectIdentifier: Int] = [:]
    var onStateChange: ((Bool) -> Void)?
    /// Actual playback progress: an utterance tagged with `tag` started / finished speaking.
    var onUtteranceStarted: ((Int) -> Void)?
    var onUtteranceFinished: ((Int) -> Void)?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    var isSpeaking: Bool { synthesizer.isSpeaking }

    func speak(_ text: String, tag: Int? = nil) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        if let tag { tags[ObjectIdentifier(utterance)] = tag }
        synthesizer.speak(utterance)
        onStateChange?(true)
    }

    func stopAll() {
        if synthesizer.isSpeaking || synthesizer.isPaused {
            cancelledCount += 1
        }
        synthesizer.stopSpeaking(at: .immediate)
        tags.removeAll()
        onStateChange?(false)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        if let tag = tags[ObjectIdentifier(utterance)] { onUtteranceStarted?(tag) }
        onStateChange?(true)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        spokenCount += 1
        if let tag = tags.removeValue(forKey: ObjectIdentifier(utterance)) { onUtteranceFinished?(tag) }
        onStateChange?(synthesizer.isSpeaking)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        tags.removeValue(forKey: ObjectIdentifier(utterance))
        onStateChange?(synthesizer.isSpeaking)
    }
}
