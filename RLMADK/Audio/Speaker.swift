import AVFoundation
import Foundation

/// `AVSpeechSynthesizer` behind the app-owned gate. `stopAll` is deterministic cancellation of the
/// current utterance and the synthesizer's own queue.
final class Speaker: NSObject, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private(set) var spokenCount = 0
    private(set) var cancelledCount = 0
    var onStateChange: ((Bool) -> Void)?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    var isSpeaking: Bool { synthesizer.isSpeaking }

    func speak(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        synthesizer.speak(utterance)
        onStateChange?(true)
    }

    func stopAll() {
        if synthesizer.isSpeaking || synthesizer.isPaused {
            cancelledCount += 1
        }
        synthesizer.stopSpeaking(at: .immediate)
        onStateChange?(false)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        spokenCount += 1
        onStateChange?(synthesizer.isSpeaking)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        onStateChange?(synthesizer.isSpeaking)
    }
}
