import Foundation

/// Decides when an ordinary (non-prayer) utterance should be end-pointed from silence.
///
/// Silence is measured from the later of the last partial transcript and the moment the current
/// recognition request started: a freshly restarted request gets a full window instead of being
/// ended at once because the previous utterance's last partial is already old. Prayer never uses it.
struct EndpointClock: Equatable {
    var silenceSeconds: Double = 2.5
    private(set) var lastSpeechAt: Date
    private(set) var requestStartedAt: Date

    init(now: Date = Date(), silenceSeconds: Double = 2.5) {
        self.silenceSeconds = silenceSeconds
        lastSpeechAt = now
        requestStartedAt = now
    }

    mutating func speech(at now: Date = Date()) { lastSpeechAt = now }
    mutating func requestStarted(at now: Date = Date()) { requestStartedAt = now }

    func silentFor(now: Date = Date()) -> TimeInterval {
        now.timeIntervalSince(max(lastSpeechAt, requestStartedAt))
    }

    func shouldEndUtterance(now: Date = Date(), listening: Bool, praying: Bool) -> Bool {
        guard listening, !praying else { return false }
        return silentFor(now: now) > silenceSeconds
    }
}
