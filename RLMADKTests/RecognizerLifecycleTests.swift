import AVFoundation
import XCTest
@testable import RLMADK

/// The request lifecycle behind prayer/conversation identity, driven through a fake engine: a
/// request keeps the owner it opened under; rebinding or restarting ends the accepting request
/// (never cancels it) and opens the next one at once; no buffer reaches an ended request; a late
/// final is delivered under its own owner; only `stop()` cancels.
final class RecognizerLifecycleTests: XCTestCase {
    final class FakeEngine: RecognitionEngine {
        final class Request: RecognitionRequestHandle {
            var appended = 0
            var ended = false
            var cancelled = false
            var emit: ((String?, Bool, Error?) -> Void)!
            func append(_ buffer: AVAudioPCMBuffer) { appended += 1 }
            func endAudio() { ended = true }
            func cancel() { cancelled = true }
            func partial(_ text: String) { emit(text, false, nil) }
            func final(_ text: String) { emit(text, true, nil) }
            func noSpeech() { emit(nil, false, NSError(domain: "kAFAssistantErrorDomain", code: 1110)) }
        }

        var requests: [Request] = []
        func open(onResult: @escaping (String?, Bool, Error?) -> Void) -> RecognitionRequestHandle {
            let request = Request()
            request.emit = onResult
            requests.append(request)
            return request
        }
    }

    private var engine: FakeEngine!
    private var recognizer: Recognizer!
    private var finals: [(text: String, owner: String?)] = []
    private let buffer = AVAudioPCMBuffer(pcmFormat: AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!, frameCapacity: 160)!

    override func setUp() {
        engine = FakeEngine()
        recognizer = Recognizer(engine: engine, availability: Recognizer.Availability(authorized: true, recognizerAvailable: true, onDeviceSupported: true, locale: "en-US", detail: ""))
        finals = []
        recognizer.onFinal = { [weak self] text, owner in self?.finals.append((text, owner)) }
    }

    func testBeginPrayerEndsTheConversationRequestAndOpensThePrayerRequestAtOnce() {
        recognizer.startContinuous()
        let conversation = engine.requests[0]
        recognizer.append(buffer)
        XCTAssertEqual(conversation.appended, 1)
        recognizer.rebindOwner("prayer-A")
        XCTAssertTrue(conversation.ended, "ended, so its final still comes")
        XCTAssertFalse(conversation.cancelled)
        XCTAssertEqual(engine.requests.count, 2, "the prayer request is open immediately")
        let prayer = engine.requests[1]
        XCTAssertTrue(recognizer.isListening)
        XCTAssertEqual(recognizer.currentOwner, "prayer-A")
        recognizer.append(buffer)
        XCTAssertEqual(conversation.appended, 1, "no buffer after endAudio")
        XCTAssertEqual(prayer.appended, 1, "the new owner receives audio without waiting for the old final")
        // The conversation final settles late, while the prayer request is accepting.
        conversation.final("what time is it")
        XCTAssertEqual(finals.map { $0.text }, ["what time is it"])
        XCTAssertNil(finals[0].owner)
        XCTAssertTrue(recognizer.isListening, "the prayer request is untouched")
        XCTAssertEqual(engine.requests.count, 2, "no restart stampede while a request is accepting")
        prayer.final("Lord hear us")
        XCTAssertEqual(finals.last!.owner, "prayer-A")
    }

    func testEndPrayerKeepsThePendingPrayerFinalUnderThePrayer() {
        recognizer.startContinuous()
        recognizer.rebindOwner("prayer-A")
        let prayer = engine.requests[1]
        engine.requests[0].noSpeech()
        recognizer.rebindOwner(nil)
        XCTAssertTrue(prayer.ended)
        XCTAssertFalse(prayer.cancelled)
        let conversation = engine.requests[2]
        XCTAssertNil(recognizer.currentOwner)
        conversation.final("remind me later")           // the new conversation settles first
        prayer.final("amen")                             // the prayer final arrives after it
        XCTAssertEqual(finals.map { "\($0.owner ?? "nil"):\($0.text)" }, ["nil:remind me later", "prayer-A:amen"])
    }

    func testSameWordsUnderTwoOwnersAreBothDelivered() {
        recognizer.startContinuous()
        recognizer.rebindOwner("prayer-A")
        engine.requests[0].final("amen")
        engine.requests[1].final("amen")
        XCTAssertEqual(finals.map { $0.owner }, [nil, "prayer-A"])
        // Identical text twice from the same owner within three seconds (restart overlap) is one.
        recognizer.rebindOwner(nil)
        engine.requests[2].final("amen")
        XCTAssertEqual(finals.count, 2)
    }

    func testManualRestartEndsButDoesNotDropThePendingFinal() {
        recognizer.startContinuous()
        let first = engine.requests[0]
        recognizer.start()                               // manual Listen while a request is open
        XCTAssertTrue(first.ended)
        XCTAssertFalse(first.cancelled)
        XCTAssertEqual(engine.requests.count, 2)
        first.final("still delivered")
        XCTAssertEqual(finals.map { $0.text }, ["still delivered"])
        XCTAssertTrue(recognizer.isListening)
    }

    func testStopCancelsEverythingAndLaterResultsAreIgnored() {
        recognizer.startContinuous()
        let first = engine.requests[0]
        recognizer.endUtterance()                        // draining
        XCTAssertFalse(recognizer.isListening)
        recognizer.stop()
        XCTAssertTrue(first.cancelled)
        first.final("too late")
        XCTAssertTrue(finals.isEmpty)
        XCTAssertNil(recognizer.current)
        XCTAssertTrue(recognizer.draining.isEmpty)
    }

    func testPartialsComeOnlyFromTheAcceptingRequest() {
        var partials: [String] = []
        recognizer.onPartial = { partials.append($0) }
        recognizer.startContinuous()
        recognizer.rebindOwner("prayer-A")
        engine.requests[0].partial("old words")           // ended request, newer one accepting
        engine.requests[1].partial("new words")
        XCTAssertEqual(partials, ["new words"])
    }
}
