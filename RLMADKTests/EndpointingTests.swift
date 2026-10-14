import XCTest
@testable import RLMADK

/// Phone-session follow-up (2026-10-13): a restarted recognition request must not inherit the previous
/// utterance's silence age, and prayer never end-points on silence. (Turns and barge-in now belong
/// to the talk tier; the on-device recognizer only serves floor rules and control phrases.)
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
    func testRestartResetsSilenceClock() {
        let model = PortalModel()
        model.recognitionStarter = {}
        model.recognitionRestarted("final")
        XCTAssertLessThan(model.endpointClock.silentFor(), 1)
    }
}
