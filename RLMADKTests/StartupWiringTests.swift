import AVFoundation
import XCTest
@testable import RLMADK

/// VOICE-START-1: recognition startup must follow the authoritative audio/availability transitions,
/// whichever order the callbacks arrive in, and must never be requested twice for one session.
@MainActor
final class StartupWiringTests: XCTestCase {
    private func usable() -> Recognizer.Availability {
        Recognizer.Availability(authorized: true, recognizerAvailable: true, onDeviceSupported: true, locale: "en-US", detail: "")
    }

    private func unusable() -> Recognizer.Availability {
        Recognizer.Availability(authorized: false, recognizerAvailable: true, onDeviceSupported: true, locale: "en-US", detail: "not authorized")
    }

    func testRunningThenAvailabilityStartsOnce() {
        let model = PortalModel()
        var starts = 0
        model.recognitionStarter = { starts += 1 }
        model.audioStateChanged(.running)      // running arrives first; availability still unknown/unusable
        XCTAssertEqual(starts, 0)
        model.availabilityChanged(usable())    // now both hold -> exactly one start
        XCTAssertEqual(starts, 1)
        model.audioStateChanged(.running)      // repeated callbacks do not duplicate
        model.availabilityChanged(usable())
        XCTAssertEqual(starts, 1)
        XCTAssertTrue(model.recognitionRequested)
    }

    func testAvailabilityThenRunningStartsOnce() {
        let model = PortalModel()
        var starts = 0
        model.recognitionStarter = { starts += 1 }
        model.availabilityChanged(usable())    // availability first
        XCTAssertEqual(starts, 0)
        model.audioStateChanged(.running)      // running later -> one start
        XCTAssertEqual(starts, 1)
        model.reconcileRecognition()
        XCTAssertEqual(starts, 1)
    }

    func testUnusableAvailabilityNeverStartsAndRecoversWhenUsable() {
        let model = PortalModel()
        var starts = 0
        model.recognitionStarter = { starts += 1 }
        model.audioStateChanged(.running)
        model.availabilityChanged(unusable())
        XCTAssertEqual(starts, 0)
        XCTAssertFalse(model.recognitionRequested)
        model.availabilityChanged(usable())
        XCTAssertEqual(starts, 1)
    }

    func testAudioStopResetsSoTheNextRunStartsAgain() {
        let model = PortalModel()
        var starts = 0
        model.recognitionStarter = { starts += 1 }
        model.availabilityChanged(usable())
        model.audioStateChanged(.running)
        XCTAssertEqual(starts, 1)
        model.audioStateChanged(.idle)
        XCTAssertFalse(model.recognitionRequested)
        model.audioStateChanged(.running)
        XCTAssertEqual(starts, 2)
    }
}

final class SegmentWriterCloseTests: XCTestCase {
    func testAppendAfterCloseOpensNoNewFile() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("rlmadk-writer-close-\(UUID().uuidString)")
        let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
        let writer = SegmentWriter(captureId: "c", directory: dir, format: format, maxSeconds: 60, queue: DispatchQueue(label: "t"), startIndex: 0)
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 1600)!
        buffer.frameLength = 1600
        writer.append(buffer)
        var closedSegments: [SegmentRecord] = []
        let closedExpectation = expectation(description: "segment closed")
        writer.onSegmentClosed = { seg in closedSegments.append(seg); closedExpectation.fulfill() }
        let settledExpectation = expectation(description: "settled")
        var settledIndex = -1
        writer.close { last in settledIndex = last; settledExpectation.fulfill() }
        wait(for: [closedExpectation, settledExpectation], timeout: 5)
        XCTAssertEqual(closedSegments.map(\.index), [0])
        XCTAssertEqual(settledIndex, 1)
        // A late append from a tap that still held the old writer must not open 00000001.wav.
        writer.append(buffer)
        writer.append(buffer)
        writer.drain()
        let names = try FileManager.default.contentsOfDirectory(atPath: dir.path).sorted()
        XCTAssertEqual(names, ["00000000.wav"])
        XCTAssertEqual(writer.producedCount, 1)
    }
}
