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
        let (woken, _) = run([.wake], from: state)
        XCTAssertFalse(woken.quiet)
        XCTAssertTrue(SpeechGate.mayEnqueue(woken))
    }

    func testPrayerLongPauseProducesNoOutputAndNoEnd() {
        let (state, effects) = run([.enterPrayer(captureId: "c1"), .silence(seconds: 5), .silence(seconds: 300), .silence(seconds: 1800)])
        XCTAssertTrue(state.isPraying)
        XCTAssertEqual(state.prayerCaptureId, "c1")
        XCTAssertFalse(SpeechGate.mayEnqueue(state))
        XCTAssertFalse(effects.contains(.savePrayer(captureId: "c1")))
        XCTAssertFalse(effects.contains(.waitForUser))
        // A recognized final transcript during prayer never starts a turn.
        let (still, more) = run([.finalTranscript(turnId: "t1")], from: state)
        XCTAssertTrue(still.isPraying)
        XCTAssertFalse(more.contains(.startTurn(turnId: "t1")))
    }

    func testExplicitFinishSavesAndWaits() {
        let (state, effects) = run([.enterPrayer(captureId: "c2"), .silence(seconds: 120), .endPrayer])
        XCTAssertEqual(state.activity, .idle)
        XCTAssertEqual(effects.suffix(2), [.savePrayer(captureId: "c2"), .waitForUser])
        XCTAssertFalse(effects.contains { if case .startTurn = $0 { return true } else { return false } })
    }

    func testExistingQuietRemainsAfterPrayerAndReconnect() {
        let (state, _) = run([.enterQuiet, .enterPrayer(captureId: "c3"), .endPrayer, .connectionLost, .connectionRestored])
        XCTAssertTrue(state.quiet)
        XCTAssertEqual(state.activity, .idle)
        XCTAssertFalse(SpeechGate.mayEnqueue(state))
    }

    func testBargeInCancelsCurrentAndQueuedOutput() {
        let (state, effects) = run([.finalTranscript(turnId: "t1"), .assistantDelta(turnId: "t1"), .userSpeechStarted])
        XCTAssertEqual(state.activity, .idle)
        XCTAssertTrue(effects.contains(.cancelOutput(turnId: "t1")))
        XCTAssertTrue(effects.contains(.sendCancel(turnId: "t1")))
        // A late delta for the old turn does not resurrect it.
        let (after, late) = run([.assistantDelta(turnId: "t1"), .assistantDone(turnId: "t1")], from: state)
        XCTAssertEqual(after.activity, .idle)
        XCTAssertTrue(late.isEmpty)
    }

    func testOrdinaryCompletionReturnsToAvailableNotQuiet() {
        let (state, effects) = run([.finalTranscript(turnId: "t2"), .assistantDelta(turnId: "t2"), .assistantDone(turnId: "t2")])
        XCTAssertEqual(state.activity, .idle)
        XCTAssertFalse(state.quiet)
        XCTAssertTrue(SpeechGate.mayEnqueue(state))
        XCTAssertEqual(effects, [.startTurn(turnId: "t2")])
    }

    func testQuietCancelsOutputAndStopCancelsTurn() {
        let (state, effects) = run([.finalTranscript(turnId: "t3"), .enterQuiet])
        XCTAssertTrue(state.quiet)
        XCTAssertEqual(state.activity, .idle)
        XCTAssertTrue(effects.contains(.sendCancel(turnId: "t3")))
        let (s2, e2) = run([.finalTranscript(turnId: "t4"), .stopRequested])
        XCTAssertEqual(s2.activity, .idle)
        XCTAssertTrue(e2.contains(.cancelOutput(turnId: "t4")))
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
}
