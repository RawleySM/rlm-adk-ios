import XCTest
@testable import RLMADK

final class PortalReducerTests: XCTestCase {
    private func run(_ events: [PortalEvent], from start: PortalMode = PortalMode()) -> (PortalMode, [PortalEffect]) {
        var state = start
        var all: [PortalEffect] = []
        for e in events {
            let (next, effects) = PortalReducer.apply(e, to: state)
            state = next
            all += effects
        }
        return (state, all)
    }

    func testQuietSurvivesReconnectAndRelaunch() {
        let (state, _) = run([.enterQuiet, .connectionLost, .connectionRestored, .relaunch])
        XCTAssertTrue(state.quiet)
        XCTAssertFalse(SpeechGate.mayEnqueue(state))
        // Only an explicit wake clears it.
        let (woken, effects) = run([.wake], from: state)
        XCTAssertFalse(woken.quiet)
        XCTAssertTrue(SpeechGate.mayEnqueue(woken))
        XCTAssertEqual(effects, [.sendClientMode(.available)])
    }

    func testPrayerLongPauseProducesNoOutputAndNoEnd() {
        let (state, effects) = run([.enterPrayer(captureId: "c1"), .silence(seconds: 5), .silence(seconds: 300), .silence(seconds: 1800)])
        XCTAssertTrue(state.isPraying)
        XCTAssertEqual(state.prayerCaptureId, "c1")
        XCTAssertFalse(SpeechGate.mayEnqueue(state))
        XCTAssertFalse(effects.contains(.savePrayer(captureId: "c1")))
        XCTAssertFalse(effects.contains(.waitForUser))
        // Bot speech that arrives during prayer is stopped again and the server is re-told.
        let (still, more) = run([.botStartedSpeaking(turnId: "b1")], from: state)
        XCTAssertTrue(still.isPraying)
        XCTAssertEqual(more, [.stopVoice, .sendClientMode(.prayer)])
    }

    func testExplicitFinishSavesAndWaits() {
        let (state, effects) = run([.enterPrayer(captureId: "c2"), .silence(seconds: 120), .endPrayer])
        XCTAssertEqual(state.activity, .idle)
        XCTAssertEqual(effects.suffix(3), [.savePrayer(captureId: "c2"), .waitForUser, .sendClientMode(.available)])
    }

    func testExistingQuietRemainsAfterPrayerAndReconnect() {
        let (state, effects) = run([.enterQuiet, .enterPrayer(captureId: "c3"), .endPrayer, .connectionLost, .connectionRestored])
        XCTAssertTrue(state.quiet)
        XCTAssertEqual(state.activity, .idle)
        XCTAssertFalse(SpeechGate.mayEnqueue(state))
        XCTAssertEqual(effects.last, .sendClientMode(.quiet), "after the prayer the server is told quiet, not available")
    }

    func testEnterPrayerStopsVoiceAndTellsTheServer() {
        var speaking = PortalMode()
        speaking.activity = .responding(turnId: "bot-1")
        let (state, effects) = PortalReducer.apply(.enterPrayer(captureId: "c4"), to: speaking)
        XCTAssertTrue(state.isPraying)
        XCTAssertEqual(effects, [.stopVoice, .sendClientMode(.prayer)])
    }

    func testBotSpeechTracksActivityAndReturnsToAvailableNotQuiet() {
        let (speaking, start) = run([.botStartedSpeaking(turnId: "bot-1")])
        XCTAssertEqual(speaking.activity, .responding(turnId: "bot-1"))
        XCTAssertTrue(start.isEmpty)
        let (done, _) = run([.botStoppedSpeaking], from: speaking)
        XCTAssertEqual(done.activity, .idle)
        XCTAssertFalse(done.quiet)
        XCTAssertTrue(SpeechGate.mayEnqueue(done))
    }

    func testQuietStopsVoiceAndStopRequestStopsVoice() {
        let (state, effects) = run([.botStartedSpeaking(turnId: "bot-2"), .enterQuiet])
        XCTAssertTrue(state.quiet)
        XCTAssertEqual(state.activity, .idle)
        XCTAssertEqual(effects, [.stopVoice, .sendClientMode(.quiet)])
        let (s2, e2) = run([.botStartedSpeaking(turnId: "bot-3"), .stopRequested])
        XCTAssertEqual(s2.activity, .idle)
        XCTAssertEqual(e2, [.stopVoice])
        // Bot speech while quiet is stopped again; activity never becomes speaking.
        let (s3, e3) = run([.botStartedSpeaking(turnId: "bot-4")], from: state)
        XCTAssertEqual(s3.activity, .idle)
        XCTAssertEqual(e3, [.stopVoice, .sendClientMode(.quiet)])
    }

    func testQuietDuringPrayerKeepsTheServerInPrayer() {
        let (state, effects) = run([.enterPrayer(captureId: "c5"), .enterQuiet, .wake])
        XCTAssertTrue(state.isPraying)
        XCTAssertFalse(effects.contains(.sendClientMode(.quiet)))
        XCTAssertFalse(effects.contains(.sendClientMode(.available)))
    }

    func testRelaunchDropsAStaleBotTurnButKeepsPrayerAndQuiet() {
        var stale = PortalMode()
        stale.quiet = true
        stale.activity = .responding(turnId: "old")
        XCTAssertEqual(PortalReducer.apply(.relaunch, to: stale).0.activity, .idle)
        XCTAssertTrue(PortalReducer.apply(.relaunch, to: stale).0.quiet)
        var praying = PortalMode()
        praying.activity = .prayer(captureId: "c6")
        XCTAssertEqual(PortalReducer.apply(.relaunch, to: praying).0.prayerCaptureId, "c6")
    }

    func testClientFloorModeOfMode() {
        var mode = PortalMode()
        XCTAssertEqual(ClientFloorMode.of(mode), .available)
        mode.quiet = true
        XCTAssertEqual(ClientFloorMode.of(mode), .quiet)
        mode.activity = .prayer(captureId: "c7")
        XCTAssertEqual(ClientFloorMode.of(mode), .prayer)
    }

    func testModePersistsThroughCodable() throws {
        var mode = PortalMode()
        mode.quiet = true
        mode.activity = .prayer(captureId: "c9")
        mode.devotionalPassage = "PSA.23"
        mode.devotionalPosition = 4
        let data = try JSONEncoder().encode(mode)
        let back = try JSONDecoder().decode(PortalMode.self, from: data)
        XCTAssertEqual(back, mode)
    }

    func testModeFilesFromEarlierBuildsStillDecode() throws {
        // Build 8 wrote turn activities; they must still load so quiet is never lost.
        let old = #"{"quiet":true,"activity":{"listening":{"turnId":"t1"}},"devotionalPosition":2}"#
        let mode = try JSONDecoder().decode(PortalMode.self, from: Data(old.utf8))
        XCTAssertTrue(mode.quiet)
        XCTAssertEqual(PortalReducer.apply(.relaunch, to: mode).0.activity, .idle)
    }
}

extension PortalReducerTests {
    func testDevotionalRevisionPersists() throws {
        var mode = PortalMode()
        mode.devotionalRevision = "abc"
        let back = try JSONDecoder().decode(PortalMode.self, from: JSONEncoder().encode(mode))
        XCTAssertEqual(back.devotionalRevision, "abc")
    }
}
