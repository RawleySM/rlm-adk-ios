import AVFoundation
import CryptoKit
import Foundation

/// The single native audio owner: one `AVAudioSession` configuration and one `AVAudioEngine` input tap.
/// Captured buffers fan out to the local segment writer (always, including quiet and prayer) and to the
/// on-device recognizer when a turn is open. File writes and hashing stay off the real-time callback.
final class AudioOwner: NSObject {
    enum State: Equatable {
        case idle
        case running
        case failed(String)

        var label: String {
            switch self {
            case .idle: return "Idle"
            case .running: return "Recording"
            case .failed(let m): return "Failed: \(m)"
            }
        }
    }

    private let engine = AVAudioEngine()
    private let writeQueue = DispatchQueue(label: "rlmadk.audio.write", qos: .utility)
    private var writer: SegmentWriter?
    private(set) var state: State = .idle
    var onStateChange: ((State) -> Void)?
    var onBuffer: ((AVAudioPCMBuffer, AVAudioTime) -> Void)?
    var onGap: ((GapRecord) -> Void)?
    var onSegmentClosed: ((String, SegmentRecord) -> Void)? // captureId, segment
    var maxSegmentSeconds: Double = 60
    private var openGap: GapRecord?
    private(set) var inputFormat: AVAudioFormat?

    override init() {
        super.init()
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(handleInterruption(_:)), name: AVAudioSession.interruptionNotification, object: nil)
        center.addObserver(self, selector: #selector(handleRouteChange(_:)), name: AVAudioSession.routeChangeNotification, object: nil)
        center.addObserver(self, selector: #selector(handleReset(_:)), name: AVAudioSession.mediaServicesWereResetNotification, object: nil)
    }

    func configureSession() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.allowBluetooth, .defaultToSpeaker])
        try session.setActive(true, options: [])
    }

    /// Starts the engine and tap. Captured audio goes to `captureId`'s segment files.
    func start(captureId: String, directory: URL) throws {
        if state == .running { return }
        do {
            try configureSession()
            let input = engine.inputNode
            if input.isVoiceProcessingEnabled == false {
                try? input.setVoiceProcessingEnabled(true)
            }
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                throw NSError(domain: "AudioOwner", code: 1, userInfo: [NSLocalizedDescriptionKey: "no input format (no microphone on this device/simulator)"])
            }
            inputFormat = format
            let writer = SegmentWriter(captureId: captureId, directory: directory, format: format, maxSeconds: maxSegmentSeconds, queue: writeQueue)
            writer.onSegmentClosed = { [weak self] seg in self?.onSegmentClosed?(captureId, seg) }
            self.writer = writer
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, time in
                guard let self else { return }
                self.writer?.append(buffer)
                self.onBuffer?(buffer, time)
            }
            engine.prepare()
            try engine.start()
            setState(.running)
        } catch {
            setState(.failed(error.localizedDescription))
            throw error
        }
    }

    /// Begins a new capture (for example when prayer starts) without stopping the engine.
    func beginCapture(captureId: String, directory: URL) {
        guard let format = inputFormat else { return }
        let old = writer
        let writer = SegmentWriter(captureId: captureId, directory: directory, format: format, maxSeconds: maxSegmentSeconds, queue: writeQueue)
        writer.onSegmentClosed = { [weak self] seg in self?.onSegmentClosed?(captureId, seg) }
        self.writer = writer
        old?.close()
    }

    /// Closes the current segment so it can be hashed and uploaded.
    func rotateSegment() { writer?.rotate() }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        writer?.close()
        writer = nil
        setState(.idle)
    }

    private func setState(_ s: State) {
        state = s
        onStateChange?(s)
    }

    private func beginGap(_ reason: String) {
        guard openGap == nil else { return }
        openGap = GapRecord(startedAt: Date(), endedAt: nil, reason: reason, kind: "capture")
    }

    private func endGap() {
        guard var gap = openGap else { return }
        gap.endedAt = Date()
        openGap = nil
        onGap?(gap)
    }

    @objc private func handleInterruption(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt, let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
        switch type {
        case .began:
            beginGap("interruption_began")
        case .ended:
            if state == .running, !engine.isRunning {
                try? AVAudioSession.sharedInstance().setActive(true)
                try? engine.start()
            }
            endGap()
        @unknown default:
            break
        }
    }

    @objc private func handleRouteChange(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt, let reason = AVAudioSession.RouteChangeReason(rawValue: raw) else { return }
        if reason == .oldDeviceUnavailable || reason == .newDeviceAvailable {
            onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "route_change_\(reason.rawValue)", kind: "capture"))
            if state == .running, !engine.isRunning { try? engine.start() }
        }
    }

    @objc private func handleReset(_ note: Notification) {
        onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "media_services_reset", kind: "capture"))
        if state == .running { try? engine.start() }
    }
}

/// Writes PCM segments to disk; each closed segment is hashed (SHA-256) and reported.
final class SegmentWriter {
    let captureId: String
    let directory: URL
    let format: AVAudioFormat
    let maxSeconds: Double
    private let queue: DispatchQueue
    private var file: AVAudioFile?
    private var index = 0
    private var startedAt = Date()
    private var frames: AVAudioFramePosition = 0
    var onSegmentClosed: ((SegmentRecord) -> Void)?

    init(captureId: String, directory: URL, format: AVAudioFormat, maxSeconds: Double, queue: DispatchQueue) {
        self.captureId = captureId
        self.directory = directory
        self.format = format
        self.maxSeconds = maxSeconds
        self.queue = queue
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    private var currentURL: URL { directory.appendingPathComponent(String(format: "%08d.wav", index)) }

    func append(_ buffer: AVAudioPCMBuffer) {
        // Copy the buffer so the real-time thread is never blocked on disk.
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else { return }
        copy.frameLength = buffer.frameLength
        let channels = Int(buffer.format.channelCount)
        if let src = buffer.floatChannelData, let dst = copy.floatChannelData {
            for c in 0..<channels { memcpy(dst[c], src[c], Int(buffer.frameLength) * MemoryLayout<Float>.size) }
        } else if let src = buffer.int16ChannelData, let dst = copy.int16ChannelData {
            for c in 0..<channels { memcpy(dst[c], src[c], Int(buffer.frameLength) * MemoryLayout<Int16>.size) }
        }
        queue.async { [self] in
            do {
                if file == nil {
                    // WAV with 16-bit PCM is what the prayer intake's ffmpeg path reads without issues.
                    let settings: [String: Any] = [
                        AVFormatIDKey: kAudioFormatLinearPCM,
                        AVSampleRateKey: format.sampleRate,
                        AVNumberOfChannelsKey: format.channelCount,
                        AVLinearPCMBitDepthKey: 16,
                        AVLinearPCMIsFloatKey: false,
                        AVLinearPCMIsBigEndianKey: false,
                        AVLinearPCMIsNonInterleaved: false,
                    ]
                    file = try AVAudioFile(forWriting: currentURL, settings: settings, commonFormat: format.commonFormat, interleaved: format.isInterleaved)
                    startedAt = Date()
                    frames = 0
                }
                try file?.write(from: copy)
                frames += AVAudioFramePosition(copy.frameLength)
                if Double(frames) / format.sampleRate >= maxSeconds { closeCurrent() }
            } catch {
                NSLog("segment write failed: \(error)")
            }
        }
    }

    func rotate() { queue.async { [self] in closeCurrent() } }
    func close() { queue.async { [self] in closeCurrent() } }

    private func closeCurrent() {
        guard file != nil else { return }
        let url = currentURL
        file = nil // AVAudioFile flushes on deinit
        let data = (try? Data(contentsOf: url)) ?? Data()
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let record = SegmentRecord(index: index, filename: url.lastPathComponent, bytes: data.count, sha256: digest, startedAt: startedAt, endedAt: Date())
        index += 1
        DispatchQueue.main.async { [self] in onSegmentClosed?(record) }
    }
}
