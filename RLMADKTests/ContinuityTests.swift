import AVFoundation
import XCTest
@testable import RLMADK

final class ContinuityTests: XCTestCase {
    // MARK: reducer additions (gap 4)

    func testEnterPrayerAlwaysCancelsOutputEvenWhenIdle() {
        let (state, effects) = PortalReducer.apply(.enterPrayer(captureId: "c1"), to: PortalMode())
        XCTAssertTrue(state.isPraying)
        XCTAssertEqual(effects, [.cancelOutput(turnId: nil)])
    }

    func testEnterPrayerDuringResponseCancelsAndSendsCancel() {
        var state = PortalMode()
        state.activity = .responding(turnId: "t9")
        let (next, effects) = PortalReducer.apply(.enterPrayer(captureId: "c2"), to: state)
        XCTAssertTrue(next.isPraying)
        XCTAssertEqual(effects, [.cancelOutput(turnId: "t9"), .sendCancel(turnId: "t9")])
    }

    func testUserSpeechAfterDoneStillInterruptsPlayingOutput() {
        var state = PortalMode()
        state.activity = .responding(turnId: "t1")
        let (afterDone, _) = PortalReducer.apply(.assistantDone(turnId: "t1"), to: state)
        XCTAssertEqual(afterDone.activity, .idle)
        let (next, effects) = PortalReducer.apply(.userSpeechStarted, to: afterDone)
        XCTAssertEqual(next.activity, .idle)
        XCTAssertEqual(effects, [.cancelOutput(turnId: nil)]) // trailing TTS / devotional stops
    }

    func testPrayerSpeechIsNeverAnInterruption() {
        var state = PortalMode()
        state.activity = .prayer(captureId: "c3")
        let (next, effects) = PortalReducer.apply(.userSpeechStarted, to: state)
        XCTAssertTrue(next.isPraying)
        XCTAssertTrue(effects.isEmpty)
    }

    // MARK: capture settlement and indexes (gaps 2, 3)

    private func seg(_ i: Int) -> SegmentRecord {
        SegmentRecord(index: i, filename: String(format: "%08d.wav", i), bytes: 500 + i, sha256: String(repeating: "a", count: 63) + "\(i)", startedAt: Date(), endedAt: Date())
    }

    func testFinalizeWaitsForDelayedLastSegmentEvenWhenEarlierOnesUploaded() {
        var m = CaptureManifest(captureId: "prayer-x", kind: "prayer", createdAt: Date())
        m.segments = [seg(0), seg(1)]
        m.recordUpload(index: 0, intakeSha256: m.segments[0].sha256, durable: true, error: nil)
        m.recordUpload(index: 1, intakeSha256: m.segments[1].sha256, durable: true, error: nil)
        m.closedAt = Date()
        XCTAssertNil(m.finalizeSegments(), "writer has not settled: finalize must wait")
        m.expectedSegmentCount = 3 // writer settles: it produced three segments; the third callback is late
        XCTAssertFalse(m.writerSettled)
        XCTAssertNil(m.finalizeSegments())
        m.segments.append(seg(2)) // the delayed last-segment callback arrives
        XCTAssertTrue(m.writerSettled)
        XCTAssertNil(m.finalizeSegments(), "last segment not uploaded yet")
        m.recordUpload(index: 2, intakeSha256: m.segments[2].sha256, durable: true, error: nil)
        XCTAssertEqual(m.finalizeSegments()?.count, 3)
    }

    func testFinalizeRejectsNonContiguousIndexes() {
        var m = CaptureManifest(captureId: "prayer-y", kind: "prayer", createdAt: Date())
        m.segments = [seg(0), seg(2)]
        for s in m.segments { m.recordUpload(index: s.index, intakeSha256: s.sha256, durable: true, error: nil) }
        m.closedAt = Date()
        m.expectedSegmentCount = 2
        XCTAssertNil(m.finalizeSegments())
    }

    func testNextSegmentIndexContinuesAfterRestartAndAfterPrayer() {
        var ambient = CaptureManifest(captureId: "ambient-1", kind: "ambient", createdAt: Date())
        XCTAssertEqual(ambient.nextSegmentIndex, 0)
        ambient.segments = [seg(0), seg(1)]
        XCTAssertEqual(ambient.nextSegmentIndex, 2) // stop/restart or ambient -> prayer -> ambient resumes at 2
        _ = ambient.reconcileFiles([(index: 2, filename: "00000002.wav", bytes: 10, sha256: "f")])
        XCTAssertEqual(ambient.nextSegmentIndex, 3)
    }

    func testWriterNeverReusesAnIndexPresentOnDisk() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("rlmadk-writer-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data([1, 2, 3]).write(to: dir.appendingPathComponent("00000000.wav"))
        try Data([4, 5, 6]).write(to: dir.appendingPathComponent("00000004.wav"))
        XCTAssertEqual(SegmentWriter.highestIndexOnDisk(in: dir), 4)
        // A manifest that only knows index 0 would ask for startIndex 1; disk says 4 exists, so 5 is next.
        let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
        let writer = SegmentWriter(captureId: "c", directory: dir, format: format, maxSeconds: 60, queue: DispatchQueue(label: "t"), startIndex: 1)
        XCTAssertEqual(writer.producedCount, 0)
        let before = try Data(contentsOf: dir.appendingPathComponent("00000004.wav"))
        XCTAssertEqual(before, Data([4, 5, 6])) // untouched
        XCTAssertEqual(SegmentWriter.highestIndexOnDisk(in: dir), 4)
    }

    func testReconcileRecoversOrphanFilesOnce() {
        var m = CaptureManifest(captureId: "prayer-z", kind: "prayer", createdAt: Date())
        m.segments = [seg(0)]
        let files = [(index: 0, filename: "00000000.wav", bytes: 1, sha256: "x"), (index: 1, filename: "00000001.wav", bytes: 2, sha256: "y")]
        XCTAssertEqual(m.reconcileFiles(files), [1])
        XCTAssertEqual(m.reconcileFiles(files), [])
        XCTAssertEqual(m.segments.map(\.index), [0, 1])
        XCTAssertEqual(m.pendingUploads.map(\.index), [0, 1])
    }

    // MARK: playback continuity (gap 7)

    func testDevotionalProgressFollowsActualPlaybackAndResumesInterruptedSegment() {
        var p = DevotionalProgress(passage: "PSA.23", revision: "c39fdb164db6", total: 4)
        p.started(0); p.finished(0)
        p.started(1)
        p.interrupted() // paused during segment two
        XCTAssertEqual(p.resumeIndex, 1)
        XCTAssertFalse(p.finished)
        p.started(1); p.finished(1); p.started(2); p.finished(2); p.started(3); p.finished(3)
        XCTAssertTrue(p.finished)
        XCTAssertTrue(p.matches(passage: "PSA.23", revision: "c39fdb164db6"))
        XCTAssertFalse(p.matches(passage: "PSA.23", revision: "other"))
    }

    func testEnqueueOrderDoesNotAdvanceProgress() {
        var p = DevotionalProgress(passage: "JHN.15", revision: "r", total: 3)
        // Enqueueing three utterances does not touch progress; only start/finish callbacks do.
        XCTAssertEqual(p.resumeIndex, 0)
        p.started(0)
        XCTAssertEqual(p.resumeIndex, 0)
    }

    // MARK: durable client outbox (gap 6)

    func testOfflineSubmitSurvivesRelaunchAndCreatesExactlyOneServerOperation() throws {
        let item = LocalWorkItem(operationId: "op123", kind: "research", requestText: "Psalm 23 and rest", createdAt: Date())
        let data = try JSONEncoder().encode([item]) // persisted while offline
        let reloaded = try JSONDecoder().decode([LocalWorkItem].self, from: data) // relaunch
        XCTAssertEqual(reloaded, [item])
        var serverOps: Set<String> = []
        // first reconnect: server does not know it -> create (same id)
        let plan1 = WorkReconciler.plan(pending: reloaded, serverHas: { serverOps.contains($0) })
        XCTAssertEqual(plan1, [.create(operationId: "op123")])
        serverOps.insert("op123")
        // crash before local settle, second reconnect: server has it -> settle, never a second create
        let plan2 = WorkReconciler.plan(pending: reloaded, serverHas: { serverOps.contains($0) })
        XCTAssertEqual(plan2, [.settle(operationId: "op123")])
        var settled = reloaded
        settled[0].state = "recorded"
        XCTAssertEqual(WorkReconciler.plan(pending: settled, serverHas: { _ in true }), [])
    }
}
