import XCTest
@testable import RLMADK

/// Phone-session follow-up (2026-10-13): a restarted recognition request must not inherit the previous
/// utterance's silence age, a new final supersedes a pending turn, and barge-in semantics on partials
/// are unchanged (speech during a pending turn cancels it).
final class EndpointingTests: XCTestCase {
    func testFreshRequestGetsAFullSilenceWindow() {
        let t0 = Date(timeIntervalSince1970: 1_000)
        var clock = EndpointClock(now: t0)
        clock.speech(at: t0.addingTimeInterval(1))
        clock.requestStarted(at: t0.addingTimeInterval(4)) // restarted 3 s after the last partial
        XCTAssertFalse(clock.shouldEndUtterance(now: t0.addingTimeInterval(4.5), listening: true, praying: false), "must not end the new request at once")
        XCTAssertFalse(clock.shouldEndUtterance(now: t0.addingTimeInterval(6.4), listening: true, praying: false))
        XCTAssertTrue(clock.shouldEndUtterance(now: t0.addingTimeInterval(6.6), listening: true, praying: false))
    }

    func testSpeechResetsTheWindowAndPrayerNeverEndpoints() {
        let t0 = Date(timeIntervalSince1970: 2_000)
        var clock = EndpointClock(now: t0)
        clock.speech(at: t0.addingTimeInterval(10))
        XCTAssertFalse(clock.shouldEndUtterance(now: t0.addingTimeInterval(12), listening: true, praying: false))
        XCTAssertTrue(clock.shouldEndUtterance(now: t0.addingTimeInterval(13), listening: true, praying: false))
        XCTAssertFalse(clock.shouldEndUtterance(now: t0.addingTimeInterval(600), listening: true, praying: true), "prayer: a ten-minute pause ends nothing")
        XCTAssertFalse(clock.shouldEndUtterance(now: t0.addingTimeInterval(13), listening: false, praying: false))
    }

    @MainActor
    func testPartialStillBargesInOnAPendingTurn() {
        let model = PortalModel()
        model.recognitionStarter = {}
        model.typedInput = "What does Psalm 23 say"
        model.submitTyped() // pending turn (no connection in tests)
        XCTAssertNotNil(model.mode.currentTurnId)
        model.partialTranscriptReceived("Actually")
        XCTAssertEqual(model.mode.activity, .idle, "speech during a pending turn cancels it so the old answer cannot start over the user")
    }

    func testNewFinalSupersedesPendingTurn() {
        let (afterFirst, _) = PortalReducer.apply(.finalTranscript(turnId: "t1"), to: PortalMode())
        XCTAssertEqual(afterFirst.currentTurnId, "t1")
        let (afterSecond, effects) = PortalReducer.apply(.finalTranscript(turnId: "t2"), to: afterFirst)
        XCTAssertEqual(afterSecond.currentTurnId, "t2")
        XCTAssertEqual(effects, [.cancelOutput(turnId: "t1"), .sendCancel(turnId: "t1"), .startTurn(turnId: "t2")])
        let (same, again) = PortalReducer.apply(.finalTranscript(turnId: "t2"), to: afterSecond)
        XCTAssertEqual(same.currentTurnId, "t2")
        XCTAssertEqual(again, [.startTurn(turnId: "t2")])
    }

    @MainActor
    func testRestartResetsSilenceClock() {
        let model = PortalModel()
        model.recognitionStarter = {}
        model.recognitionRestarted("final")
        XCTAssertLessThan(model.endpointClock.silentFor(), 1)
    }
}
