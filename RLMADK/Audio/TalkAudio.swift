import AVFoundation
import Foundation

/// The tagged-PCM framing of the talk tier's WebSocket path (`talk/pcm_serializer.py`): one tag
/// byte `0x01` followed by int16 little-endian mono PCM. Up: 16 kHz in 20 ms chunks (640 bytes).
/// Down: 24 kHz, any length.
enum PCMFraming {
    static let audioTag: UInt8 = 0x01
    static let uplinkSampleRate: Double = 16_000
    static let downlinkSampleRate: Double = 24_000
    /// 20 ms at 16 kHz, 2 bytes per sample.
    static let uplinkChunkBytes = 640

    /// A tagged frame for `pcm` (int16 LE bytes).
    static func frame(_ pcm: Data) -> Data {
        var out = Data([audioTag])
        out.append(pcm)
        return out
    }

    /// One 20 ms frame of digital silence.
    static let silenceFrame: Data = frame(Data(count: uplinkChunkBytes))

    /// Splits whole 640-byte chunks off the front of `pending` as tagged frames; the remainder stays.
    static func takeFrames(from pending: inout Data) -> [Data] {
        var frames: [Data] = []
        while pending.count >= uplinkChunkBytes {
            frames.append(frame(pending.prefix(uplinkChunkBytes)))
            pending.removeFirst(uplinkChunkBytes)
        }
        return frames
    }

    /// Payload of a downlink binary frame, or nil when it is not tagged audio.
    static func audioPayload(_ message: Data) -> Data? {
        guard message.first == audioTag, message.count > 1 else { return nil }
        return message.dropFirst()
    }

    /// int16 LE bytes as float samples in [-1, 1].
    static func floats(fromInt16LE data: Data) -> [Float] {
        let count = data.count / 2
        var out = [Float](repeating: 0, count: count)
        data.withUnsafeBytes { raw in
            let bytes = raw.bindMemory(to: UInt8.self)
            for i in 0..<count {
                let value = Int16(bitPattern: UInt16(bytes[2 * i]) | (UInt16(bytes[2 * i + 1]) << 8))
                out[i] = Float(value) / 32768
            }
        }
        return out
    }

    /// Float samples as int16 LE bytes (clipped).
    static func int16LE(fromFloats samples: [Float]) -> Data {
        var out = Data(count: samples.count * 2)
        out.withUnsafeMutableBytes { raw in
            let bytes = raw.bindMemory(to: UInt8.self)
            for (i, sample) in samples.enumerated() {
                let clipped = max(-1, min(1, sample))
                let value = UInt16(bitPattern: Int16((clipped * 32767).rounded()))
                bytes[2 * i] = UInt8(value & 0xFF)
                bytes[2 * i + 1] = UInt8(value >> 8)
            }
        }
        return out
    }
}

/// Feeds the audio owner's capture to the talk tier as 16 kHz int16 mono in 20 ms tagged frames.
///
/// The audio owner stays the only microphone owner: this only receives copies of the buffers its
/// tap already produces, resamples them off the real-time thread (AVAudioConverter, persistent so
/// the resampler keeps its state across buffers) and emits frames. While no captured audio arrives
/// (engine stopped, fixture mode) it sends 20 ms of silence every 20 ms, so the server's VAD and
/// turn detection always see a continuous stream.
final class PCMUplink {
    private let queue = DispatchQueue(label: "rlmadk.talk.uplink", qos: .userInitiated)
    private let outputFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: PCMFraming.uplinkSampleRate, channels: 1, interleaved: true)!
    private var converter: AVAudioConverter?
    private var converterInput: AVAudioFormat?
    private var pending = Data()
    private var lastRealAudio: TimeInterval = 0
    private var keepalive: DispatchSourceTimer?
    private let lock = NSLock()
    private var running = false
    private var sink: ((Data) -> Void)?
    private(set) var framesSent = 0
    private(set) var silenceFramesSent = 0

    /// Starts emitting frames to `sink` (called on the uplink queue).
    func start(sink: @escaping (Data) -> Void) {
        lock.lock(); running = true; self.sink = sink; lock.unlock()
        queue.async { [self] in
            pending.removeAll()
            lastRealAudio = 0
            let timer = DispatchSource.makeTimerSource(queue: queue)
            timer.schedule(deadline: .now() + .milliseconds(20), repeating: .milliseconds(20), leeway: .milliseconds(2))
            timer.setEventHandler { [weak self] in self?.keepaliveTick() }
            keepalive?.cancel()
            keepalive = timer
            timer.resume()
        }
    }

    func stop() {
        lock.lock(); running = false; sink = nil; lock.unlock()
        queue.async { [self] in
            keepalive?.cancel()
            keepalive = nil
            pending.removeAll()
        }
    }

    /// Counters for Diagnostics, read on any thread.
    var counters: (frames: Int, silence: Int) { queue.sync { (framesSent, silenceFramesSent) } }

    /// Called from the audio owner's tap (real-time thread): copies the buffer and returns at once.
    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock(); let on = running; lock.unlock()
        guard on, let copy = PCMUplink.copy(buffer) else { return }
        queue.async { [self] in convertAndSend(copy) }
    }

    private func emit(_ frame: Data) {
        lock.lock(); let target = running ? sink : nil; lock.unlock()
        target?(frame)
    }

    private func keepaliveTick() {
        // Real audio arrives in tap-sized bursts (about 85 ms); silence fills only real gaps.
        guard ProcessInfo.processInfo.systemUptime - lastRealAudio > 0.25 else { return }
        silenceFramesSent += 1
        emit(PCMFraming.silenceFrame)
    }

    private func convertAndSend(_ buffer: AVAudioPCMBuffer) {
        if converterInput != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: outputFormat)
            converterInput = buffer.format
        }
        guard let converter else { return }
        let ratio = PCMFraming.uplinkSampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, outStatus in
            if supplied {
                // NOTE [Context]: .noDataNow (not .endOfStream) keeps the resampler's state for the
                // next buffer; .endOfStream would reset it and click at every buffer boundary.
                outStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, out.frameLength > 0, let samples = out.int16ChannelData else { return }
        lastRealAudio = ProcessInfo.processInfo.systemUptime
        pending.append(Data(bytes: samples[0], count: Int(out.frameLength) * 2))
        for frame in PCMFraming.takeFrames(from: &pending) {
            framesSent += 1
            emit(frame)
        }
    }

    /// Copy of a tap buffer (the tap reuses its storage after returning).
    static func copy(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else { return nil }
        copy.frameLength = buffer.frameLength
        let channels = Int(buffer.format.channelCount)
        if let src = buffer.floatChannelData, let dst = copy.floatChannelData {
            for c in 0..<(buffer.format.isInterleaved ? 1 : channels) {
                memcpy(dst[c], src[c], Int(buffer.frameLength) * MemoryLayout<Float>.size * (buffer.format.isInterleaved ? channels : 1))
            }
        } else if let src = buffer.int16ChannelData, let dst = copy.int16ChannelData {
            for c in 0..<(buffer.format.isInterleaved ? 1 : channels) {
                memcpy(dst[c], src[c], Int(buffer.frameLength) * MemoryLayout<Int16>.size * (buffer.format.isInterleaved ? channels : 1))
            }
        } else {
            return nil
        }
        return copy
    }
}

/// Plays the WebSocket path's 24 kHz bot PCM through a player node attached to the audio owner's
/// own engine, so the input node's voice processing hears the app's speech as its echo reference
/// and cancels it from the capture and from on-device recognition.
///
/// Thread-safe: frames arrive on the WebSocket queue; gate and stop come from the main actor.
final class EngineVoiceOutput: VoiceOutput, @unchecked Sendable {
    private weak var audio: AudioOwner?
    private let lock = NSLock()
    private var _gateOpen = true
    private var suppressed = false
    private var turnActive = false
    private var scheduled = 0
    private var droppedWhileClosed = 0
    private var droppedNoEngine = 0
    private var playedBuffers = 0
    private var receivedFrames = 0
    private var receivedBytes = 0
    var onPlayingChange: ((Bool) -> Void)?

    init(audio: AudioOwner) { self.audio = audio }

    /// Dropped and played buffer counts (tests, Diagnostics).
    var counters: (droppedByGate: Int, droppedNoEngine: Int, played: Int) {
        lock.lock(); defer { lock.unlock() }
        return (droppedWhileClosed, droppedNoEngine, playedBuffers)
    }

    /// Downlink audio frames and bytes received (played or dropped).
    var received: (frames: Int, bytes: Int) { lock.lock(); defer { lock.unlock() }; return (receivedFrames, receivedBytes) }

    var gateOpen: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _gateOpen }
        set {
            lock.lock()
            let was = _gateOpen
            _gateOpen = newValue
            if newValue, !was { suppressed = false } // a turn cut by the gate is over when it reopens
            lock.unlock()
            // Closing the gate drops what is queued at once; while closed, nothing is scheduled.
            if was, !newValue { flushPlayer() }
        }
    }

    var isPlaying: Bool { lock.lock(); defer { lock.unlock() }; return scheduled > 0 }

    var statusLine: String {
        lock.lock(); defer { lock.unlock() }
        return "engine player: gate \(_gateOpen ? "open" : "closed")\(suppressed ? ", turn suppressed" : "") · queued \(scheduled) · played \(playedBuffers) · dropped (gate) \(droppedWhileClosed) · dropped (no engine) \(droppedNoEngine)"
    }

    /// One downlink payload (int16 LE, 24 kHz mono). Dropped unless the gate is open and the turn
    /// was not stopped.
    func enqueue(pcm24k payload: Data) {
        lock.lock()
        receivedFrames += 1
        receivedBytes += payload.count
        let allowed = _gateOpen && !suppressed
        if !allowed { droppedWhileClosed += 1 }
        lock.unlock()
        guard allowed, let audio else { return }
        let samples = PCMFraming.floats(fromInt16LE: payload)
        guard !samples.isEmpty, let buffer = AVAudioPCMBuffer(pcmFormat: audio.playbackFormat, frameCapacity: AVAudioFrameCount(samples.count)) else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { src in
            buffer.floatChannelData![0].update(from: src.baseAddress!, count: samples.count)
        }
        lock.lock(); scheduled += 1; let startedNow = scheduled == 1; lock.unlock()
        let accepted = audio.schedulePlayback(buffer) { [weak self] in self?.bufferDone() }
        if !accepted {
            lock.lock(); scheduled -= 1; droppedNoEngine += 1; lock.unlock()
            return
        }
        if startedNow { notify(true) }
    }

    private func bufferDone() {
        lock.lock()
        scheduled = max(0, scheduled - 1)
        playedBuffers += 1
        let idle = scheduled == 0
        lock.unlock()
        if idle { notify(false) }
    }

    private func notify(_ playing: Bool) {
        let callback = onPlayingChange
        DispatchQueue.main.async { callback?(playing) }
    }

    func stop() {
        lock.lock(); if turnActive || scheduled > 0 { suppressed = true }; lock.unlock()
        flushPlayer()
    }

    func flush() {
        lock.lock(); suppressed = false; lock.unlock()
        flushPlayer()
    }

    func botTurnStarted() {
        lock.lock(); turnActive = true; lock.unlock()
    }

    func botTurnEnded() {
        lock.lock(); turnActive = false; suppressed = false; lock.unlock()
    }

    private func flushPlayer() {
        audio?.stopPlayback()
        lock.lock(); let had = scheduled > 0; scheduled = 0; lock.unlock()
        if had { notify(false) }
    }
}
