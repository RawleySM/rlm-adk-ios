import UIKit
import XCTest
@testable import RLMADK

/// Phone inspection: the snapshot encodes the app-owned facts, commands report observed results
/// (applied only when the action ran and was seen to take effect; failed with a reason; unsupported
/// for unknown kinds; replayed from the cache when pushed again), the prayer ASR journal is
/// sequence-keyed and written before any control phrase acts, and a capture that settles with no
/// segments is a failure.
@MainActor
final class DeviceInspectionTests: XCTestCase {
    private func decodeArgs(_ json: String) -> [String: AnyCodableValue] {
        try! JSONDecoder().decode([String: AnyCodableValue].self, from: Data(json.utf8))
    }

    private func results(_ model: PortalModel) -> () -> [CommandResult] {
        var out: [CommandResult] = []
        model.commandResultSink = { out.append($0) }
        return { out }
    }

    private func waitUntil(_ timeout: TimeInterval = 3, _ condition: @escaping () -> Bool) async {
        let deadline = Date().addingTimeInterval(timeout)
        while !condition(), Date() < deadline { try? await Task.sleep(nanoseconds: 50_000_000) }
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
        for key in ["install_id", "client_time", "scene_phase", "selected_tab", "connection", "mode", "audio_owner_state", "engine_running", "keyboard_visible", "captures", "recent_log"] {
            XCTAssertNotNil(json[key], key)
        }
        XCTAssertLessThanOrEqual((json["recent_log"] as! [String]).count, 40)
        model.keyboardChanged(visible: false, frame: nil)
        XCTAssertNil(model.makeSnapshot().keyboard_frame)
        XCTAssertEqual(model.lastSnapshotReason, "keyboard")
        model.scenePhaseChanged(.background)
        XCTAssertEqual(model.makeSnapshot().scene_phase, "background")
        XCTAssertEqual(model.lastSnapshotReason, "background", "leaving the foreground is reported, not only returning")
    }

    func testSelectTabCommandAppliesKnownTabAndFailsUnknown() {
        let model = PortalModel()
        let got = results(model)
        model.handleCommand(["type": "command", "command_id": "c1", "kind": "select_tab", "args": ["tab": "diagnostics"]])
        XCTAssertEqual(model.selectedTab, "diagnostics")
        XCTAssertEqual(got().last?.status, .applied)
        XCTAssertEqual(got().last?.detail["tab"], "diagnostics")
        model.handleCommand(["type": "command", "command_id": "c2", "kind": "select_tab", "args": ["tab": "settings"]])
        XCTAssertEqual(model.selectedTab, "diagnostics", "unknown tab leaves the selection alone")
        XCTAssertEqual(got().last?.status, .failed)
        XCTAssertTrue(got().last!.detail["reason"]!.contains("unknown tab settings"))
        model.handleCommand(["type": "command", "command_id": "c3", "kind": "select_tab"])
        XCTAssertEqual(got().last?.detail["reason"], "missing tab")
    }

    func testRepeatedCommandIsAnsweredFromTheCacheAndNeverRunTwice() {
        let model = PortalModel()
        let got = results(model)
        let id = "dup-\(UUID().uuidString.prefix(8))"
        model.handleCommand(["type": "command", "command_id": id, "kind": "select_tab", "args": ["tab": "work"]])
        XCTAssertEqual(model.selectedTab, "work")
        model.selectedTab = "portal" // the user moved on
        model.handleCommand(["type": "command", "command_id": id, "kind": "select_tab", "args": ["tab": "work"]])
        XCTAssertEqual(model.selectedTab, "portal", "a re-pushed command (lost acknowledgement) is not executed again")
        XCTAssertEqual(got().count, 2, "but its result is reported again")
        XCTAssertEqual(got().last?.commandId, id)
        XCTAssertEqual(got().last?.detail["tab"], "work")
        // The cache survives a relaunch.
        let again = PortalModel()
        let got2 = results(again)
        again.handleCommand(["type": "command", "command_id": id, "kind": "select_tab", "args": ["tab": "work"]])
        XCTAssertEqual(again.selectedTab, "portal")
        XCTAssertEqual(got2().last?.status, .applied)
    }

    func testDismissKeyboardReportsObservedPostState() async {
        let model = PortalModel()
        let got = results(model)
        let token = model.keyboardDismissToken
        model.handleCommand(["type": "command", "command_id": "k1", "kind": "dismiss_keyboard"])
        await waitUntil { got().count == 1 }
        XCTAssertEqual(got().last?.status, .applied)
        XCTAssertEqual(got().last?.detail["keyboard_was_visible"], "no")
        XCTAssertEqual(got().last?.detail["keyboard_visible_after"], "no")
        XCTAssertEqual(model.keyboardDismissToken, token + 1, "fields are asked to resign even when no keyboard is known")
        // A keyboard that stays up (no keyboard-did-hide arrives here) is a failure, with the pre-state kept.
        model.keyboardChanged(visible: true, frame: CGRect(x: 0, y: 500, width: 390, height: 300))
        model.focusChanged("typedInput")
        model.handleCommand(["type": "command", "command_id": "k2", "kind": "dismiss_keyboard"])
        await waitUntil(PortalModel.dismissObservationWindow + 2) { got().count == 2 }
        XCTAssertEqual(got().last?.status, .failed)
        XCTAssertEqual(got().last?.detail["keyboard_was_visible"], "yes")
        XCTAssertEqual(got().last?.detail["focused_field_was"], "typedInput")
        XCTAssertEqual(got().last?.detail["keyboard_visible_after"], "yes")
        XCTAssertTrue(got().last!.detail["reason"]!.hasPrefix("keyboard still visible"))
        model.handleCommand(["type": "command", "command_id": "k3", "kind": "reboot"])
        XCTAssertEqual(got().last?.status, .unsupported)
        XCTAssertEqual(got().last?.payload()["command_id"] as? String, "k3")
    }

    func testImageCommandFailsWithReasonWithoutForegroundScene() async {
        let model = PortalModel()
        let got = results(model)
        model.handleCommand(["type": "command", "command_id": "img", "kind": "image"])
        await waitUntil { got().count == 1 }
        if case .failure(let error) = model.renderAppWindow() {
            XCTAssertEqual(got().last?.status, .failed)
            XCTAssertEqual(got().last?.detail["reason"], error.reason)
        } else {
            XCTAssertEqual(got().last?.status, .failed, "no backend: the upload fails, never applied")
        }
    }

    func testCopyPlanCoversManifestOrphanAndOpenFileCases() {
        var capture = CaptureManifest(captureId: "prayer-1", kind: "prayer", createdAt: Date())
        capture.segments = [
            SegmentRecord(index: 0, filename: "00000000.wav", bytes: 10, sha256: "a", startedAt: Date(), endedAt: Date()),
            SegmentRecord(index: 1, filename: "00000001.wav", bytes: 10, sha256: "b", startedAt: Date(), endedAt: Date()),
            SegmentRecord(index: 2, filename: "00000002.wav", bytes: 0, sha256: "", startedAt: Date(), endedAt: nil), // still open
        ]
        // On disk: 0 (manifest), 3 (closed orphan the manifest never saw); 1 is gone; 2 is the open writer file (excluded).
        let disk: (String) -> [Int: String] = { id in id == "prayer-1" ? [0: "00000000.wav", 3: "00000003.wav"] : (id == "ambient-orphan-dir" ? [5: "00000005.wav"] : [:]) }
        if case .failure(let e) = RemoteCommandPlanner.copyPlan(decodeArgs(#"{"capture_id":"nope"}"#), captures: [capture], closedFilesOnDisk: disk) {
            XCTAssertEqual(e.reason, "unknown capture nope")
        } else { XCTFail("unknown capture must fail") }
        guard case .success(let all) = RemoteCommandPlanner.copyPlan(decodeArgs(#"{"capture_id":"prayer-1"}"#), captures: [capture], closedFilesOnDisk: disk) else { return XCTFail() }
        XCTAssertEqual(all.files.map(\.index), [0, 3])
        XCTAssertEqual(all.files.map(\.manifestSha256), ["a", nil])
        XCTAssertEqual(all.missingIndexes, [1], "closed segment without a file is reported; the open one is never named")
        XCTAssertTrue(all.inManifest)
        guard case .success(let some) = RemoteCommandPlanner.copyPlan(decodeArgs(#"{"capture_id":"prayer-1","indexes":[1,2,7]}"#), captures: [capture], closedFilesOnDisk: disk) else { return XCTFail() }
        XCTAssertEqual(some.files, [])
        XCTAssertEqual(some.missingIndexes, [1, 2, 7], "the open file is not copyable as complete")
        guard case .success(let orphan) = RemoteCommandPlanner.copyPlan(decodeArgs(#"{"capture_id":"ambient-orphan-dir"}"#), captures: [capture], closedFilesOnDisk: disk) else { return XCTFail() }
        XCTAssertEqual(orphan.files, [RemoteCommandPlanner.PlannedFile(index: 5, filename: "00000005.wav", manifestSha256: nil)])
        XCTAssertFalse(orphan.inManifest)
    }

    func testCopyCommandForUnknownCaptureReportsFailure() {
        let model = PortalModel()
        let got = results(model)
        model.handleCommand(["type": "command", "command_id": "f1", "kind": "copy_capture_files", "args": ["capture_id": "prayer-does-not-exist"]])
        XCTAssertEqual(got().last?.status, .failed)
        XCTAssertEqual(got().last?.detail["reason"], "unknown capture prayer-does-not-exist")
    }

    func testOpenWriterFileIsNeverListedAsClosed() throws {
        let store = LocalStore()
        let captureId = "scan-test-\(UUID().uuidString.prefix(8))"
        let dir = store.captureDirectory(captureId)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data("closed".utf8).write(to: dir.appendingPathComponent("00000000.wav"))
        let open = dir.appendingPathComponent("00000001.wav")
        try Data("still being written".utf8).write(to: open)
        XCTAssertEqual(store.closedSegmentFiles(captureId, knownIndexes: [], excluding: open).map(\.index), [0])
        XCTAssertEqual(store.closedSegmentFiles(captureId, knownIndexes: [], excluding: nil).map(\.index), [0, 1], "without a writer, both are closed")
        XCTAssertEqual(store.segmentFilesOnDisk(captureId, excluding: open), [0: "00000000.wav"])
        try? FileManager.default.removeItem(at: dir)
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
        var reloaded = PrayerASRJournal(captureId: "prayer-1", lines: journal.lines.reversed(), syncedThrough: 2)
        XCTAssertEqual(reloaded.lines.map(\.sequence), [1, 2])
        XCTAssertTrue(reloaded.unsynced.isEmpty)
        XCTAssertEqual(reloaded.append("amen").sequence, 3)
    }

    func testPrayerASRJournalPersistsAcrossModels() {
        let model = PortalModel()
        let captureId = "prayer-test-\(UUID().uuidString.prefix(8))"
        model.journalPrayerASR(captureId: captureId, text: "Lord, hear us")
        model.journalPrayerASR(captureId: captureId, text: "amen")
        XCTAssertNil(model.lastJournalError)
        let again = PortalModel()
        let journal = again.prayerASRJournal(captureId)
        XCTAssertEqual(journal?.lines.map(\.text), ["Lord, hear us", "amen"])
        XCTAssertEqual(journal?.unsynced.count, 2, "nothing was acknowledged while disconnected")
    }

    func testPrayerTextIsJournaledBeforeTheEndPhraseActs() {
        let model = PortalModel()
        model.deliveryProbe = { false }
        model.beginPrayer()
        let captureId = model.mode.prayerCaptureId!
        XCTAssertEqual(model.recognizer.nextOwner, captureId, "every request opened from now on belongs to this prayer")
        model.finalTranscriptReceived("Lord keep my son safe tonight", owner: captureId)
        model.finalTranscriptReceived("and give us peace amen end prayer", owner: captureId)
        XCTAssertFalse(model.mode.isPraying, "the end phrase still ends the prayer")
        XCTAssertNil(model.recognizer.nextOwner)
        XCTAssertEqual(model.prayerASRJournal(captureId)?.lines.map(\.text), ["Lord keep my son safe tonight", "and give us peace amen end prayer"], "raw recognized text, including the control phrase, journaled before the control acted")
        XCTAssertNil(model.mode.currentTurnId)
    }

    func testDelayedPrayerFinalStaysWithItsCaptureAndNewConversationIsATurn() async {
        let model = PortalModel()
        model.deliveryProbe = { false }
        model.beginPrayer()
        let captureId = model.mode.prayerCaptureId!
        model.endPrayer() // manual End: the open prayer request is ended, not dropped
        // The new conversation request (owner nil) settles immediately: a turn, never prayer text.
        model.finalTranscriptReceived("what does the shepherd psalm say about rest", owner: nil)
        XCTAssertNotNil(model.mode.currentTurnId, "conversation right after End prayer is a turn")
        XCTAssertTrue(model.prayerASRJournal(captureId)?.lines.isEmpty ?? false, "and is not journaled as prayer")
        // The prayer request's own final settles well after the ordinary endpoint window (> 3 s):
        // it is identified by the request it came from, not by when it arrives.
        try? await Task.sleep(nanoseconds: 3_500_000_000)
        model.finalTranscriptReceived("in your name amen", owner: captureId)
        XCTAssertEqual(model.prayerASRJournal(captureId)?.lines.map(\.text), ["in your name amen"])
        XCTAssertEqual(model.lastTranscript, "in your name amen")
        XCTAssertNotNil(model.mode.currentTurnId, "the delayed prayer final did not start or replace a turn")
        model.stopOutput()
    }

    func testConsecutivePrayersKeepTheirOwnFinals() {
        let model = PortalModel()
        model.deliveryProbe = { false }
        model.beginPrayer()
        let first = model.mode.prayerCaptureId!
        model.endPrayer()
        model.beginPrayer()
        let second = model.mode.prayerCaptureId!
        XCTAssertNotEqual(first, second)
        XCTAssertEqual(model.recognizer.nextOwner, second)
        model.finalTranscriptReceived("thank you for today", owner: first)   // first prayer's request settles late
        model.finalTranscriptReceived("bless this night", owner: second)
        XCTAssertEqual(model.prayerASRJournal(first)?.lines.map(\.text), ["thank you for today"])
        XCTAssertEqual(model.prayerASRJournal(second)?.lines.map(\.text), ["bless this night"])
        XCTAssertTrue(model.mode.isPraying)
        // A conversation request that was still open when the prayer began is neither prayer nor a turn.
        model.finalTranscriptReceived("remind me later", owner: nil)
        XCTAssertNil(model.mode.currentTurnId)
        XCTAssertEqual(model.prayerASRJournal(second)?.lines.count, 1)
        model.endPrayer()
    }

    func testControlPhrasesFromAnEndedPrayerOrAStaleConversationNeverTouchTheCurrentPrayer() {
        let model = PortalModel()
        model.deliveryProbe = { false }
        model.beginPrayer()
        let first = model.mode.prayerCaptureId!
        model.endPrayer()
        model.beginPrayer()
        let second = model.mode.prayerCaptureId!
        // The first prayer's request settles late and happens to end with the control phrase.
        model.finalTranscriptReceived("thank you amen end prayer", owner: first)
        XCTAssertEqual(model.prayerASRJournal(first)?.lines.map(\.text), ["thank you amen end prayer"], "raw text kept with the first prayer")
        XCTAssertTrue(model.mode.isPraying)
        XCTAssertEqual(model.mode.prayerCaptureId, second, "the second prayer stays open")
        // A conversation request that was open before Begin prayer settles with the same phrase.
        model.finalTranscriptReceived("end prayer", owner: nil)
        XCTAssertEqual(model.mode.prayerCaptureId, second)
        XCTAssertNil(model.mode.currentTurnId)
        XCTAssertTrue(model.prayerASRJournal(second)?.lines.isEmpty ?? false)
        // The current prayer's own end phrase works.
        model.finalTranscriptReceived("amen end prayer", owner: second)
        XCTAssertFalse(model.mode.isPraying)
        XCTAssertEqual(model.prayerASRJournal(second)?.lines.map(\.text), ["amen end prayer"])
    }

    func testRelaunchWithAnOpenPrayerRestoresTheRecognitionOwner() {
        let model = PortalModel()
        model.deliveryProbe = { false }
        model.beginPrayer()
        let captureId = model.mode.prayerCaptureId!
        model.finalTranscriptReceived("Lord before the relaunch", owner: captureId)
        let relaunched = PortalModel() // mode.json restores the open prayer
        XCTAssertEqual(relaunched.mode.prayerCaptureId, captureId)
        XCTAssertEqual(relaunched.recognizer.nextOwner, captureId, "every request after relaunch belongs to the restored prayer")
        relaunched.finalTranscriptReceived("and after it", owner: relaunched.recognizer.nextOwner)
        XCTAssertEqual(relaunched.prayerASRJournal(captureId)?.lines.map(\.text), ["Lord before the relaunch", "and after it"])
        relaunched.endPrayer()
        XCTAssertNil(relaunched.recognizer.nextOwner)
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
    }

    func testRetainedZeroAudioManifestsAreRelabelledNotDeleted() {
        var old = CaptureManifest(captureId: "prayer-1791894626-ad50a645", kind: "prayer", createdAt: Date(timeIntervalSince1970: 1791894626))
        old.expectedSegmentCount = 0
        old.closedAt = Date(timeIntervalSince1970: 1791894797)
        var openAmbient = CaptureManifest(captureId: "ambient-1", kind: "ambient", createdAt: Date())
        openAmbient.expectedSegmentCount = nil
        var finalized = CaptureManifest(captureId: "prayer-2", kind: "prayer", createdAt: Date())
        finalized.expectedSegmentCount = 0
        finalized.closedAt = Date()
        finalized.status = .finalized(recordingId: "r")
        var list = [old, openAmbient, finalized]
        XCTAssertEqual(CaptureManifest.relabelRetainedZeroAudio(&list), ["prayer-1791894626-ad50a645"])
        XCTAssertEqual(list[0].status, .failed(reason: "no audio captured"))
        XCTAssertEqual(list[0].closedAt, old.closedAt)
        XCTAssertEqual(list[1].status, .savedLocally)
        XCTAssertEqual(list[2].status, .finalized(recordingId: "r"))
        XCTAssertEqual(list.count, 3)
    }

    func testBeginPrayerWarningTracksDeliveryAndClearsWhenBuffersResume() throws {
        let model = PortalModel()
        var delivering = false
        model.deliveryProbe = { delivering }
        model.beginPrayer()
        XCTAssertTrue(model.mode.isPraying)
        let warning = try XCTUnwrap(model.captureWarning)
        XCTAssertTrue(warning.hasPrefix("Not recording as of 20"), "states what was observed and when: \(warning)")
        XCTAssertTrue(warning.contains("Prayer text is still journaled"))
        delivering = true
        model.refreshDeliveryWarning()
        XCTAssertNil(model.captureWarning, "cleared as soon as buffers arrive again")
        XCTAssertTrue(model.log.contains(where: { $0.contains("audio delivery resumed") }))
        delivering = false
        model.refreshDeliveryWarning()
        XCTAssertNotNil(model.captureWarning)
        let captureId = model.mode.prayerCaptureId!
        model.captureSettledForTest(captureId, produced: 0)
        XCTAssertEqual(model.captures.first(where: { $0.captureId == captureId })!.status, .failed(reason: "no audio captured"))
        XCTAssertTrue(model.log.contains(where: { $0.contains("NO segments") }))
        model.endPrayer()
        XCTAssertNil(model.captureWarning)
    }

    func testRecentLogIsDurableAcrossModels() {
        let marker = "marker-\(UUID().uuidString.lowercased())"
        let model = PortalModel()
        model.handleCommand(["type": "command", "command_id": "log-1", "kind": marker]) // unsupported kind: the log line names it in full
        XCTAssertTrue(model.log.contains(where: { $0.contains(marker) }))
        let again = PortalModel()
        XCTAssertTrue(again.log.contains(where: { $0.contains(marker) }), "the durable log is reloaded on launch")
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
