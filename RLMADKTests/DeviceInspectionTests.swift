import UIKit
import XCTest
@testable import RLMADK

/// Phone inspection: the snapshot encodes the app-owned facts, commands report truthful results
/// (applied only when the action ran; failed with a reason; unsupported for unknown kinds), the
/// prayer ASR journal is sequence-keyed, and a capture that settles with no segments is a failure.
@MainActor
final class DeviceInspectionTests: XCTestCase {
    private func decodeArgs(_ json: String) -> [String: AnyCodableValue] {
        try! JSONDecoder().decode([String: AnyCodableValue].self, from: Data(json.utf8))
    }

    func testSnapshotEncodesAppOwnedFacts() throws {
        let model = PortalModel()
        model.typedInput = "hello"
        model.keyboardChanged(visible: true, frame: CGRect(x: 0, y: 500, width: 390, height: 300))
        model.focusChanged("typedInput")
        let snap = model.makeSnapshot()
        XCTAssertTrue(snap.install_id.hasPrefix("ios-"))
        XCTAssertEqual(snap.keyboard_visible, true)
        XCTAssertEqual(snap.keyboard_frame, [0, 500, 390, 300])
        XCTAssertEqual(snap.focused_field, "typedInput")
        XCTAssertEqual(snap.typed_input, "hello")
        XCTAssertFalse(snap.engine_running, "no engine runs under XCTest")
        XCTAssertNil(snap.last_buffer_age_s)
        let json = try JSONSerialization.jsonObject(with: JSONEncoder().encode(snap)) as! [String: Any]
        for key in ["install_id", "client_time", "selected_tab", "connection", "mode", "audio_owner_state", "engine_running", "keyboard_visible", "captures", "recent_log"] {
            XCTAssertNotNil(json[key], key)
        }
        XCTAssertLessThanOrEqual((json["recent_log"] as! [String]).count, 40)
        model.keyboardChanged(visible: false, frame: nil)
        XCTAssertNil(model.makeSnapshot().keyboard_frame)
        XCTAssertEqual(model.lastSnapshotReason, "keyboard")
    }

    func testSelectTabCommandAppliesKnownTabAndFailsUnknown() {
        let model = PortalModel()
        var results: [CommandResult] = []
        model.commandResultSink = { results.append($0) }
        model.handleCommand(["type": "command", "command_id": "c1", "kind": "select_tab", "args": ["tab": "diagnostics"]])
        XCTAssertEqual(model.selectedTab, "diagnostics")
        XCTAssertEqual(results.last?.status, .applied)
        XCTAssertEqual(results.last?.detail["tab"], "diagnostics")
        model.handleCommand(["type": "command", "command_id": "c2", "kind": "select_tab", "args": ["tab": "settings"]])
        XCTAssertEqual(model.selectedTab, "diagnostics", "unknown tab leaves the selection alone")
        XCTAssertEqual(results.last?.status, .failed)
        XCTAssertTrue(results.last!.detail["reason"]!.contains("unknown tab settings"))
        model.handleCommand(["type": "command", "command_id": "c3", "kind": "select_tab"])
        XCTAssertEqual(results.last?.detail["reason"], "missing tab")
    }

    func testDismissKeyboardWithNoKeyboardIsAppliedAndUnknownKindIsUnsupported() {
        let model = PortalModel()
        var results: [CommandResult] = []
        model.commandResultSink = { results.append($0) }
        let token = model.keyboardDismissToken
        model.handleCommand(["type": "command", "command_id": "k1", "kind": "dismiss_keyboard"])
        XCTAssertEqual(results.last?.status, .applied)
        XCTAssertEqual(results.last?.detail["keyboard_was_visible"], "no")
        XCTAssertEqual(model.keyboardDismissToken, token + 1, "fields are asked to resign even when no keyboard is known")
        model.handleCommand(["type": "command", "command_id": "k2", "kind": "reboot"])
        XCTAssertEqual(results.last?.status, .unsupported)
        XCTAssertEqual(results.last?.payload()["status"] as? String, "unsupported")
        XCTAssertEqual(results.last?.payload()["command_id"] as? String, "k2")
    }

    func testCopyPlanRejectsUnknownCaptureAndReportsMissingFiles() {
        var capture = CaptureManifest(captureId: "prayer-1", kind: "prayer", createdAt: Date())
        capture.segments = [
            SegmentRecord(index: 0, filename: "00000000.wav", bytes: 10, sha256: "a", startedAt: Date(), endedAt: Date()),
            SegmentRecord(index: 1, filename: "00000001.wav", bytes: 10, sha256: "b", startedAt: Date(), endedAt: Date()),
            SegmentRecord(index: 2, filename: "00000002.wav", bytes: 0, sha256: "", startedAt: Date(), endedAt: nil), // still open
        ]
        let onDisk: Set<String> = ["00000000.wav"]
        let exists: (String, String) -> Bool = { id, name in id == "prayer-1" && onDisk.contains(name) }
        if case .failure(let e) = RemoteCommandPlanner.copyPlan(decodeArgs(#"{"capture_id":"nope"}"#), captures: [capture], fileExists: exists) {
            XCTAssertEqual(e.reason, "unknown capture nope")
        } else { XCTFail("unknown capture must fail") }
        guard case .success(let all) = RemoteCommandPlanner.copyPlan(decodeArgs(#"{"capture_id":"prayer-1"}"#), captures: [capture], fileExists: exists) else { return XCTFail() }
        XCTAssertEqual(all.files.map(\.index), [0])
        XCTAssertEqual(all.missingIndexes, [1], "closed segment without a file is reported; the open one is not requested")
        guard case .success(let some) = RemoteCommandPlanner.copyPlan(decodeArgs(#"{"capture_id":"prayer-1","indexes":[1,7]}"#), captures: [capture], fileExists: exists) else { return XCTFail() }
        XCTAssertEqual(some.files, [])
        XCTAssertEqual(some.missingIndexes, [1, 7])
    }

    func testCopyCommandForUnknownCaptureReportsFailure() {
        let model = PortalModel()
        var results: [CommandResult] = []
        model.commandResultSink = { results.append($0) }
        model.handleCommand(["type": "command", "command_id": "f1", "kind": "copy_capture_files", "args": ["capture_id": "prayer-does-not-exist"]])
        XCTAssertEqual(results.last?.status, .failed)
        XCTAssertEqual(results.last?.detail["reason"], "unknown capture prayer-does-not-exist")
    }

    func testPrayerASRJournalSequencesAndSyncMarks() {
        var journal = PrayerASRJournal(captureId: "prayer-1")
        journal.append("Lord")
        journal.append("thank you")
        XCTAssertEqual(journal.unsynced.map(\.sequence), [1, 2])
        journal.markSynced(through: 1)
        XCTAssertEqual(journal.unsynced.map(\.text), ["thank you"])
        journal.markSynced(through: 0) // never regresses
        XCTAssertEqual(journal.syncedThrough, 1)
        let reloaded = PrayerASRJournal(captureId: "prayer-1", lines: journal.lines.reversed(), syncedThrough: 2)
        XCTAssertEqual(reloaded.lines.map(\.sequence), [1, 2])
        XCTAssertTrue(reloaded.unsynced.isEmpty)
        XCTAssertEqual(reloaded.append("amen").sequence, 3)
    }

    func testPrayerASRJournalPersistsAcrossModels() {
        let model = PortalModel()
        let captureId = "prayer-test-\(UUID().uuidString.prefix(8))"
        model.journalPrayerASR(captureId: captureId, text: "Lord, hear us")
        model.journalPrayerASR(captureId: captureId, text: "amen")
        let again = PortalModel()
        let journal = again.prayerASRJournal(captureId)
        XCTAssertEqual(journal?.lines.map(\.text), ["Lord, hear us", "amen"])
        XCTAssertEqual(journal?.unsynced.count, 2, "nothing was acknowledged while disconnected")
    }

    func testZeroSegmentSettleIsAFailureNotSavedLocally() {
        var capture = CaptureManifest(captureId: "prayer-0", kind: "prayer", createdAt: Date())
        capture.settle(produced: 0)
        XCTAssertEqual(capture.status, .failed(reason: "no audio captured"))
        XCTAssertTrue(capture.isClosed)
        XCTAssertEqual(capture.expectedSegmentCount, 0)
        XCTAssertNil(capture.finalizeSegments())
        var good = CaptureManifest(captureId: "prayer-1", kind: "prayer", createdAt: Date())
        good.segments = [SegmentRecord(index: 0, filename: "00000000.wav", bytes: 10, sha256: "a", startedAt: Date(), endedAt: Date())]
        good.settle(produced: 1)
        XCTAssertEqual(good.status, .savedLocally)
        XCTAssertEqual(good.status.label, "Saved locally")
    }

    func testBeginPrayerWithoutDeliveringAudioWarnsAndSettlesAsFailed() {
        let model = PortalModel()
        model.beginPrayer()
        XCTAssertTrue(model.mode.isPraying)
        XCTAssertNotNil(model.captureWarning, "no engine runs under XCTest, so the prayer is not being recorded and the UI must say so")
        XCTAssertTrue(model.captureWarning!.contains("not being recorded"))
        let captureId = model.mode.prayerCaptureId!
        model.captureSettledForTest(captureId, produced: 0)
        let capture = model.captures.first(where: { $0.captureId == captureId })!
        XCTAssertEqual(capture.status, .failed(reason: "no audio captured"))
        XCTAssertTrue(model.log.contains(where: { $0.contains("NO segments") }))
        model.endPrayer()
    }

    func testArgumentDecoding() {
        let args = decodeArgs(#"{"tab":"work","indexes":[0,2],"flag":true,"n":null}"#)
        XCTAssertEqual(args["tab"]?.stringValue, "work")
        XCTAssertEqual(args["indexes"]?.intArray, [0, 2])
        XCTAssertEqual(args["flag"], .bool(true))
        XCTAssertEqual(args["n"], .null)
        XCTAssertNil(args["tab"]?.intArray)
    }
}
