import Foundation

/// Turns streamed text deltas into complete phrases for incremental speech. Deltas for any turn
/// other than the current one are dropped, so a cancelled turn cannot re-enter playback.
struct PhraseQueue: Equatable {
    private(set) var currentTurnId: String?
    private(set) var buffer: String = ""
    private(set) var cancelledTurnIds: Set<String> = []

    mutating func beginTurn(_ turnId: String) {
        currentTurnId = turnId
        buffer = ""
    }

    /// Returns the complete phrases ready to speak after applying `delta`.
    mutating func accept(delta: String, turnId: String) -> [String] {
        guard turnId == currentTurnId, !cancelledTurnIds.contains(turnId) else { return [] }
        buffer += delta
        var phrases: [String] = []
        while let range = PhraseQueue.phraseEnd(in: buffer) {
            let phrase = String(buffer[buffer.startIndex..<range.upperBound]).trimmingCharacters(in: .whitespacesAndNewlines)
            buffer = String(buffer[range.upperBound...])
            if !phrase.isEmpty { phrases.append(phrase) }
        }
        return phrases
    }

    /// Flushes any trailing text when the turn is done.
    mutating func finish(turnId: String) -> [String] {
        guard turnId == currentTurnId, !cancelledTurnIds.contains(turnId) else { return [] }
        let rest = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
        buffer = ""
        currentTurnId = nil
        return rest.isEmpty ? [] : [rest]
    }

    /// Barge-in / stop / quiet: forget everything queued and refuse the turn's later deltas.
    mutating func cancelAll() {
        if let id = currentTurnId { cancelledTurnIds.insert(id) }
        currentTurnId = nil
        buffer = ""
    }

    static func phraseEnd(in text: String) -> Range<String.Index>? {
        var index = text.startIndex
        while index < text.endIndex {
            let ch = text[index]
            let next = text.index(after: index)
            if ch == "\n" { return index..<next }
            if ".!?;:".contains(ch) {
                if next == text.endIndex { return nil } // wait for following whitespace or more text
                if text[next].isWhitespace { return index..<next }
            }
            index = next
        }
        return nil
    }
}
