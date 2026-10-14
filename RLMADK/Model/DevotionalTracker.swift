import Foundation

/// Follows the talk tier's actual speech through the devotional segments being read.
///
/// The bot's spoken text (`bot-tts-text`, emitted after each sentence was synthesised and sent) is
/// aligned word by word against the segment texts. Progress moves only when words were spoken,
/// never when the reading was requested, so an interruption keeps the segment that was being read
/// and "continue" repeats it in full. Words the bot adds (an introduction, a bridge phrase such as
/// "Let me look.") do not match the passage and are skipped.
struct DevotionalTracker: Equatable {
    enum Update: Equatable {
        case started(Int)
        case finished(Int)
    }

    /// Normalised words of each segment.
    let segmentWords: [[String]]
    /// Segment the next spoken passage word is expected in.
    private(set) var segment: Int
    /// Index of the next expected word within `segment`.
    private(set) var wordOffset = 0
    private(set) var startedSegments: Set<Int> = []
    /// How far ahead (in passage words) a spoken word may match, to skip words the TTS or the
    /// recognizer of the reference text spelled differently.
    static let lookahead = 12

    /// Args:
    ///   segments: The text of every devotional segment, in reading order.
    ///   startIndex: The segment the reading starts at (the saved resume point).
    init(segments: [String], startIndex: Int) {
        segmentWords = segments.map { FloorText.words($0) }
        segment = min(max(0, startIndex), segments.count)
    }

    /// True when every segment from the start index was spoken to its last word.
    var isComplete: Bool { segment >= segmentWords.count }

    /// Spoken text arrived; returns the progress updates it implies (in order).
    ///
    /// A spoken word is accepted only as part of a run: it must directly continue the previous
    /// accepted word, or the spoken word after it must follow it in the passage too. A stray common
    /// word in the bot's own introduction ("Here is the passage") therefore cannot move the
    /// position.
    mutating func spoken(_ text: String) -> [Update] {
        var updates: [Update] = []
        let words = FloorText.words(text)
        var inRun = false
        for (i, word) in words.enumerated() {
            let next = i + 1 < words.count ? words[i + 1] : nil
            guard let (s, o) = locate(word, continuing: inRun, followedBy: next) else {
                inRun = false
                continue
            }
            inRun = true
            if s > segment, startedSegments.contains(segment) {
                // The reading moved on from a segment whose last words were not heard as spoken.
                updates.append(.finished(segment))
            }
            if !startedSegments.contains(s) {
                startedSegments.insert(s)
                updates.append(.started(s))
            }
            segment = s
            wordOffset = o + 1
            if wordOffset >= segmentWords[s].count {
                updates.append(.finished(s))
                segment = s + 1
                wordOffset = 0
            }
        }
        return updates
    }

    /// The passage position after (s, o), crossing into the next non-empty segment.
    private func position(after s: Int, _ o: Int) -> (Int, Int)? {
        var s = s
        var o = o + 1
        while s < segmentWords.count {
            if o < segmentWords[s].count { return (s, o) }
            s += 1
            o = 0
        }
        return nil
    }

    /// Finds `word` at or after the current position within the lookahead, crossing segments.
    /// Directly continuing a run accepts the very next passage word; otherwise the following spoken
    /// word must follow the candidate in the passage as well.
    private func locate(_ word: String, continuing: Bool, followedBy next: String?) -> (Int, Int)? {
        var s = segment
        var o = wordOffset
        var budget = DevotionalTracker.lookahead
        var first = true
        while s < segmentWords.count, budget > 0 {
            if o >= segmentWords[s].count {
                s += 1
                o = 0
                continue
            }
            if segmentWords[s][o] == word {
                if continuing, first { return (s, o) }
                if let next, let (ns, no) = position(after: s, o), segmentWords[ns][no] == next { return (s, o) }
            }
            first = false
            o += 1
            budget -= 1
        }
        return nil
    }
}
