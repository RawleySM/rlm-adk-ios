import Accelerate
import AVFoundation
import CryptoKit
import Foundation

/// The single native audio owner: one `AVAudioSession` configuration and one `AVAudioEngine` with
/// voice processing. Captured buffers fan out to the local segment writer (always, including quiet
/// and prayer), to the on-device recognizer, and to the talk uplink when the WebSocket transport is
/// active. The WebSocket transport's bot audio plays through `player` on this same engine, so voice
/// processing cancels the app's own speech from the capture. File writes, hashing and resampling
/// stay off the real-time callback.
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
    /// Bot speech for the WebSocket transport, mixed into this engine's (voice-processed) output.
    private let player = AVAudioPlayerNode()
    private var playerAttached = false
    /// Format of buffers handed to `schedulePlayback` (the talk tier's 24 kHz mono, as float).
    let playbackFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: PCMFraming.downlinkSampleRate, channels: 1, interleaved: false)!
    /// The most recent captured audio, kept so an address-triggered prayer capture can begin with
    /// the words spoken just before the phone recognized the address.
    private let preroll = PrerollRing(capacitySeconds: 8)
    private var levels = VoiceLevelTracker()
    private var talkTap: ((AVAudioPCMBuffer) -> Void)?
    private var configurationObserver: NSObjectProtocol?
    /// Whether voice processing was on when the engine started (not read from the input node,
    /// which must not be touched before a microphone is wanted).
    private(set) var voiceProcessingOn = false
    /// Whether voice processing's automatic gain control was on when the engine started.
    private(set) var agcOn = false
    private(set) var state: State = .idle
    var onStateChange: ((State) -> Void)?
    var onBuffer: ((AVAudioPCMBuffer, AVAudioTime) -> Void)?
    var onGap: ((GapRecord) -> Void)?
    var onSegmentClosed: ((String, SegmentRecord) -> Void)? // captureId, segment
    var maxSegmentSeconds: Double = 60
    private var openGap: GapRecord?
    private(set) var inputFormat: AVAudioFormat?
    /// Observability for "is audio actually arriving": the engine's own running flag and the time
    /// of the last tap buffer. `state == .running` is the owner's intent; these are the facts.
    private let factsLock = NSLock()
    private var lastBufferUptime: TimeInterval?
    private var lastWriteErrorText: String?
    var onWriteError: ((String) -> Void)?

    var isRunning: Bool { engine.isRunning }

    /// Voice energy facts from the level tracker (anchor for the amen silence window).
    var voiceFacts: VoiceFacts {
        factsLock.lock(); defer { factsLock.unlock() }
        let now = ProcessInfo.processInfo.systemUptime
        let end = levels.lastVoiceEndUptime.map { Date().addingTimeInterval($0 - now) }
        return VoiceFacts(active: levels.active, lastVoiceEnd: end)
    }

    /// Receives every captured buffer on the real-time thread (the talk uplink copies and returns).
    /// The audio owner remains the only microphone owner; nil detaches.
    func setTalkTap(_ tap: ((AVAudioPCMBuffer) -> Void)?) {
        factsLock.lock(); talkTap = tap; factsLock.unlock()
    }

    /// Current AVAudioSession facts for coexistence evidence.
    var sessionDescription: String {
        let session = AVAudioSession.sharedInstance()
        let inputs = session.currentRoute.inputs.map { $0.portType.rawValue }.joined(separator: ",")
        let outputs = session.currentRoute.outputs.map { $0.portType.rawValue }.joined(separator: ",")
        return "category \(session.category.rawValue) mode \(session.mode.rawValue) options \(session.categoryOptions.rawValue) rate \(Int(session.sampleRate)) io \(String(format: "%.3f", session.ioBufferDuration)) in [\(inputs)] out [\(outputs)] vp \(voiceProcessingOn) agc \(agcOn) tuning[\(AudioTuning.current.description)]"
    }
    var lastBufferAge: TimeInterval? {
        factsLock.lock(); defer { factsLock.unlock() }
        return lastBufferUptime.map { ProcessInfo.processInfo.systemUptime - $0 }
    }
    var lastWriteError: String? { factsLock.lock(); defer { factsLock.unlock() }; return lastWriteErrorText }
    /// True when the engine runs and a buffer arrived within the last two seconds.
    var isDelivering: Bool { engine.isRunning && (lastBufferAge ?? .infinity) < 2 }
    /// The segment file currently being written (not closed, not hashed, must not be copied as complete).
    var openSegmentURL: URL? { writer?.openFileURL }

    override init() {
        super.init()
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(handleInterruption(_:)), name: AVAudioSession.interruptionNotification, object: nil)
        center.addObserver(self, selector: #selector(handleRouteChange(_:)), name: AVAudioSession.routeChangeNotification, object: nil)
        center.addObserver(self, selector: #selector(handleReset(_:)), name: AVAudioSession.mediaServicesWereResetNotification, object: nil)
        // NOTE [Context]: another audio client in the process (libwebrtc's audio unit on the WebRTC
        // transport) can change the session's category, mode or sample rate; the engine then stops
        // with a configuration change and must be restarted, or capture silently ends.
        configurationObserver = center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
            guard let self else { return }
            self.onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "engine_configuration_change", kind: "capture"))
            self.restartIfNeeded(reason: "configuration_change")
        }
    }

    func configureSession() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: AudioTuning.current.mode.sessionMode, options: [.allowBluetooth, .defaultToSpeaker])
        try session.setActive(true, options: [])
    }

    /// Starts the engine and tap. Captured audio goes to `captureId`'s segment files, continuing
    /// after the highest index already present (manifest or disk); nothing is overwritten.
    func start(captureId: String, directory: URL, startIndex: Int) throws {
        if state == .running { return }
        do {
            try configureSession()
            let input = engine.inputNode
            let tuning = AudioTuning.current
            if input.isVoiceProcessingEnabled != tuning.voiceProcessing {
                try? input.setVoiceProcessingEnabled(tuning.voiceProcessing)
            }
            voiceProcessingOn = input.isVoiceProcessingEnabled
            if voiceProcessingOn {
                // NOTE [Context]: voice processing's automatic gain control is one suspect for the
                // near-end attenuation during bot playback (double talk); it is a launch knob.
                input.isVoiceProcessingAGCEnabled = tuning.agc
                agcOn = input.isVoiceProcessingAGCEnabled
            } else {
                agcOn = false
            }
            player.volume = tuning.botGain
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                throw NSError(domain: "AudioOwner", code: 1, userInfo: [NSLocalizedDescriptionKey: "no input format (no microphone on this device/simulator)"])
            }
            inputFormat = format
            let writer = makeWriter(captureId: captureId, directory: directory, format: format, startIndex: startIndex)
            self.writer = writer
            attachPlayerIfNeeded()
            installCaptureTap(format: format)
            engine.prepare()
            try engine.start()
            player.play()
            setState(.running)
        } catch {
            setState(.failed(error.localizedDescription))
            throw error
        }
    }

    /// One tap on the input node for every consumer. The ring append and the writer read happen
    /// under one lock, so a capture switch with pre-roll sees every buffer either in the ring (and
    /// so in the pre-roll) or in the new writer, never in neither.
    private func installCaptureTap(format: AVAudioFormat) {
        let input = engine.inputNode
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, time in
            guard let self else { return }
            let level = VoiceLevelTracker.levelDb(buffer)
            let duration = Double(buffer.frameLength) / buffer.format.sampleRate
            let now = ProcessInfo.processInfo.systemUptime
            self.factsLock.lock()
            self.lastBufferUptime = now
            self.levels.observe(levelDb: level, duration: duration, at: now)
            self.preroll.append(buffer)
            let target = self.writer
            let tap = self.talkTap
            self.factsLock.unlock()
            target?.append(buffer)
            self.onBuffer?(buffer, time)
            tap?(buffer)
        }
    }

    /// Switches capture (for example when prayer starts or ends) without stopping the engine. The
    /// previous writer is closed; `settled` fires on the main queue AFTER its last segment-closed
    /// callback was delivered, with the total number of segments that writer produced.
    ///
    /// `prerollSeconds` > 0 starts the new capture with that much of the most recent audio (an
    /// address-triggered prayer: the address words were spoken before the phone recognized them).
    /// The previous capture keeps its own copy, so nothing is lost from either. Returns the seconds
    /// of pre-roll actually written.
    @discardableResult
    func beginCapture(captureId: String, directory: URL, startIndex: Int, prerollSeconds: Double = 0, settled: ((Int) -> Void)? = nil) -> Double {
        if formatRestartPending {
            // An input-format restart is closing the current writer; the switch is applied when it
            // completes, with the new format, so the restart cannot overwrite it. The closed
            // writer's settlement goes to the first switch away from it. A capture that was only
            // ever a waiting target (begun and ended inside the restart) had no writer: it settles
            // at once with its start index, i.e. nothing written.
            if let waiting = deferredCapture {
                settled?(waiting.startIndex)
                deferredCapture = (captureId, directory, startIndex, waiting.settled)
            } else {
                deferredCapture = (captureId, directory, startIndex, settled)
            }
            return 0
        }
        guard let format = inputFormat else { settled?(0); return 0 }
        let writer = makeWriter(captureId: captureId, directory: directory, format: format, startIndex: startIndex)
        factsLock.lock()
        let old = self.writer
        var written = 0.0
        if prerollSeconds > 0 {
            let buffers = preroll.recent(seconds: prerollSeconds).filter { $0.format == format }
            for buffer in buffers { writer.append(buffer) }
            written = buffers.reduce(0) { $0 + Double($1.frameLength) / $1.format.sampleRate }
        }
        self.writer = writer
        factsLock.unlock()
        if let old {
            old.close(settled: settled)
        } else {
            settled?(0)
        }
        return written
    }

    private func makeWriter(captureId: String, directory: URL, format: AVAudioFormat, startIndex: Int) -> SegmentWriter {
        let writer = SegmentWriter(captureId: captureId, directory: directory, format: format, maxSeconds: maxSegmentSeconds, queue: writeQueue, startIndex: startIndex)
        writer.onSegmentClosed = { [weak self] seg in self?.onSegmentClosed?(captureId, seg) }
        writer.onWriteError = { [weak self] text in
            guard let self else { return }
            self.factsLock.lock(); self.lastWriteErrorText = text; self.factsLock.unlock()
            self.onWriteError?(text)
        }
        return writer
    }

    /// Closes the current segment so it can be hashed and uploaded.
    func rotateSegment() { writer?.rotate() }

    /// Restarts a stopped engine while the owner intends to run (interruption, route change, media
    /// reset, return to foreground). A failed restart is reported as `.failed`, never hidden.
    @discardableResult
    func restartIfNeeded(reason: String) -> Bool {
        guard state == .running, !engine.isRunning, !formatRestartPending else { return engine.isRunning }
        let current = engine.inputNode.outputFormat(forBus: 0)
        if let previous = inputFormat, current.sampleRate > 0, current.channelCount > 0,
           current.sampleRate != previous.sampleRate || current.channelCount != previous.channelCount {
            restartWithNewInputFormat(current, reason: reason)
            return false
        }
        do {
            try AVAudioSession.sharedInstance().setActive(true)
            try engine.start()
            player.play()
            return true
        } catch {
            setState(.failed("restart after \(reason) failed: \(error.localizedDescription)"))
            onGap?(GapRecord(startedAt: Date(), endedAt: nil, reason: "engine_restart_failed_\(reason)", kind: "capture"))
            return false
        }
    }

    private var formatRestartPending = false
    /// A capture switch requested while a format restart was pending (applied when it completes);
    /// `settled` is the settlement owed for the writer the restart is closing.
    private var deferredCapture: (captureId: String, directory: URL, startIndex: Int, settled: ((Int) -> Void)?)?

    /// The hardware input format changed (Bluetooth HFP, another audio client's preferred sample
    /// rate). A tap and a WAV segment are bound to one format, so the current segment is closed,
    /// the tap is reinstalled for the new format and the same capture continues at the next index.
    /// The gap is recorded; nothing already written is touched.
    private func restartWithNewInputFormat(_ format: AVAudioFormat, reason: String) {
        formatRestartPending = true
        onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "input_format_change_\(Int(inputFormat?.sampleRate ?? 0))_to_\(Int(format.sampleRate))_\(reason)", kind: "capture"))
        engine.inputNode.removeTap(onBus: 0)
        factsLock.lock()
        let old = writer
        writer = nil
        preroll.reset()
        factsLock.unlock()
        let resume: (String, URL, Int) -> Void = { [weak self] captureId, directory, nextIndex in
            guard let self else { return }
            self.formatRestartPending = false
            // A capture switch that arrived meanwhile wins: the closed writer settles to its caller
            // and the new writer belongs to the capture it asked for.
            var target = (captureId: captureId, directory: directory, startIndex: nextIndex)
            if let deferred = self.deferredCapture {
                self.deferredCapture = nil
                deferred.settled?(nextIndex)
                target = (deferred.captureId, deferred.directory, deferred.startIndex)
            }
            guard self.state == .running else { return }
            self.inputFormat = format
            let fresh = self.makeWriter(captureId: target.captureId, directory: target.directory, format: format, startIndex: target.startIndex)
            self.factsLock.lock(); self.writer = fresh; self.factsLock.unlock()
            self.installCaptureTap(format: format)
            do {
                try AVAudioSession.sharedInstance().setActive(true)
                try self.engine.start()
                self.player.play()
            } catch {
                self.setState(.failed("restart after format change (\(reason)) failed: \(error.localizedDescription)"))
            }
        }
        if let old {
            let captureId = old.captureId, directory = old.directory
            old.close { nextIndex in resume(captureId, directory, nextIndex) }
        } else {
            formatRestartPending = false
        }
    }

    // MARK: bot playback (WebSocket transport)

    /// Attaches the player before the engine first starts: changing the graph of a running
    /// voice-processing engine can itself force a configuration change.
    private func attachPlayerIfNeeded() {
        guard !playerAttached else { return }
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playbackFormat)
        playerAttached = true
    }

    /// Schedules bot audio (`playbackFormat`). Returns false when the engine is not running.
    /// `completion` runs once the buffer was played or the player was stopped.
    func schedulePlayback(_ buffer: AVAudioPCMBuffer, completion: @escaping () -> Void) -> Bool {
        guard playerAttached, engine.isRunning else { return false }
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in completion() }
        if !player.isPlaying { player.play() }
        return true
    }

    /// Drops every scheduled bot buffer at once and leaves the player ready for the next turn.
    func stopPlayback() {
        guard playerAttached else { return }
        player.stop()
        if engine.isRunning { player.play() }
    }

    func stop(settled: ((Int) -> Void)? = nil) {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        if let writer { writer.close(settled: settled) } else { settled?(0) }
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
            restartIfNeeded(reason: "interruption")
            endGap()
        @unknown default:
            break
        }
    }

    @objc private func handleRouteChange(_ note: Notification) {
        guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt, let reason = AVAudioSession.RouteChangeReason(rawValue: raw) else { return }
        if reason == .oldDeviceUnavailable || reason == .newDeviceAvailable {
            onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "route_change_\(reason.rawValue)", kind: "capture"))
            restartIfNeeded(reason: "route_change")
        }
    }

    @objc private func handleReset(_ note: Notification) {
        onGap?(GapRecord(startedAt: Date(), endedAt: Date(), reason: "media_services_reset", kind: "capture"))
        restartIfNeeded(reason: "media_services_reset")
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
    private let openLock = NSLock()
    private var openURL: URL?
    /// URL of the segment being written, readable from any thread; nil between segments.
    var openFileURL: URL? { openLock.lock(); defer { openLock.unlock() }; return openURL }
    private var closed = false // set on the write queue by close(); later appends are dropped
    private var index: Int
    private let firstIndex: Int
    private var startedAt = Date()
    private var frames: AVAudioFramePosition = 0
    var onSegmentClosed: ((SegmentRecord) -> Void)?
    var onWriteError: ((String) -> Void)?

    init(captureId: String, directory: URL, format: AVAudioFormat, maxSeconds: Double, queue: DispatchQueue, startIndex: Int) {
        self.captureId = captureId
        self.directory = directory
        self.format = format
        self.maxSeconds = maxSeconds
        self.queue = queue
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        // Never reuse an index that already has bytes on disk, whatever the manifest says.
        let onDisk = SegmentWriter.highestIndexOnDisk(in: directory)
        let start = max(startIndex, onDisk + 1)
        self.index = start
        self.firstIndex = start
    }

    /// Highest `NNNNNNNN.wav` index present in `directory`, or -1.
    static func highestIndexOnDisk(in directory: URL) -> Int {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.compactMap { name -> Int? in
            guard name.hasSuffix(".wav") else { return nil }
            return Int(name.dropLast(4))
        }.max() ?? -1
    }

    /// Number of segments this writer has produced (closed) so far.
    var producedCount: Int { index - firstIndex }

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
            if closed { return } // the tap may still hold this writer briefly after a capture switch
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
                    openLock.lock(); openURL = currentURL; openLock.unlock()
                    startedAt = Date()
                    frames = 0
                }
                try file?.write(from: copy)
                frames += AVAudioFramePosition(copy.frameLength)
                if Double(frames) / format.sampleRate >= maxSeconds { closeCurrent() }
            } catch {
                NSLog("segment write failed: \(error)")
                let text = "segment \(index) write failed: \(error.localizedDescription)"
                DispatchQueue.main.async { [self] in onWriteError?(text) }
            }
        }
    }

    func rotate() { queue.async { [self] in closeCurrent() } }

    /// Closes the open segment. `settled` runs on the main queue after the last segment-closed
    /// callback (also main queue, FIFO), with the index after the last segment this writer wrote.
    func close(settled: ((Int) -> Void)? = nil) {
        queue.async { [self] in
            closeCurrent()
            closed = true
            let last = index
            if let settled { DispatchQueue.main.async { settled(last) } }
        }
    }

    /// Test seam: blocks until every queued append/close has run.
    func drain() { queue.sync {} }

    private func closeCurrent() {
        guard file != nil else { return }
        let url = currentURL
        file = nil // AVAudioFile flushes on deinit
        openLock.lock(); openURL = nil; openLock.unlock()
        let data = (try? Data(contentsOf: url)) ?? Data()
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let record = SegmentRecord(index: index, filename: url.lastPathComponent, bytes: data.count, sha256: digest, startedAt: startedAt, endedAt: Date())
        index += 1
        DispatchQueue.main.async { [self] in onSegmentClosed?(record) }
    }
}

/// Fixed-length history of captured buffers (copies), newest last. Appended on the real-time
/// thread under the owner's facts lock.
final class PrerollRing {
    private var buffers: [AVAudioPCMBuffer] = []
    private var seconds: Double = 0
    let capacitySeconds: Double

    init(capacitySeconds: Double) { self.capacitySeconds = capacitySeconds }

    func append(_ buffer: AVAudioPCMBuffer) {
        guard let copy = PCMUplink.copy(buffer) else { return }
        buffers.append(copy)
        seconds += Double(copy.frameLength) / copy.format.sampleRate
        while seconds > capacitySeconds, let first = buffers.first {
            seconds -= Double(first.frameLength) / first.format.sampleRate
            buffers.removeFirst()
        }
    }

    func reset() {
        buffers.removeAll()
        seconds = 0
    }

    /// The most recent buffers covering at least `seconds` (or all held), oldest first.
    func recent(seconds wanted: Double) -> [AVAudioPCMBuffer] {
        var out: [AVAudioPCMBuffer] = []
        var total = 0.0
        for buffer in buffers.reversed() where total < wanted {
            out.append(buffer)
            total += Double(buffer.frameLength) / buffer.format.sampleRate
        }
        return out.reversed()
    }
}

/// Energy-based voice activity on the captured (voice-processed) signal: a noise floor that drops
/// at once to quieter levels and rises slowly, with voice when a buffer is 9 dB above it. Used only
/// to anchor the amen window; it never decides a mode change by itself.
struct VoiceLevelTracker: Equatable {
    private(set) var floorDb: Double = -50
    private(set) var active = false
    private(set) var lastVoiceEndUptime: TimeInterval?
    static let marginDb: Double = 9
    static let absoluteMinimumDb: Double = -62
    /// The floor never follows the signal below this level.
    static let floorMinimumDb: Double = -75
    /// dB per second the floor rises toward a louder signal.
    static let floorRiseDbPerSecond: Double = 2.0

    mutating func observe(levelDb: Double, duration: Double, at uptime: TimeInterval) {
        // NOTE [Context]: an audio unit starting or stopping in the process (libwebrtc's, on the
        // WebRTC transport) hands the tap buffers of digital silence. Following them dragged the
        // floor to -120 dB, after which ordinary room noise counted as voice for minutes (device
        // run 2026-10-14: "voice active, ended never"). Silence like that says nothing about the room.
        guard levelDb > -100 else { return }
        let level = max(levelDb, VoiceLevelTracker.floorMinimumDb)
        if level < floorDb { floorDb = level } else { floorDb = min(level, floorDb + VoiceLevelTracker.floorRiseDbPerSecond * duration) }
        let voiced = level > max(floorDb + VoiceLevelTracker.marginDb, VoiceLevelTracker.absoluteMinimumDb)
        if active, !voiced { lastVoiceEndUptime = uptime }
        active = voiced
    }

    /// RMS level of channel 0 in dBFS.
    static func levelDb(_ buffer: AVAudioPCMBuffer) -> Double {
        guard let data = buffer.floatChannelData, buffer.frameLength > 0 else { return -120 }
        var rms: Float = 0
        vDSP_rmsqv(data[0], buffer.format.isInterleaved ? vDSP_Stride(buffer.format.channelCount) : 1, &rms, vDSP_Length(buffer.frameLength))
        return rms > 0 ? Double(20 * log10(rms)) : -120
    }
}
