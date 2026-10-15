import Foundation

/// One verse of the passage open in the scripture pane, exactly as the talk tier holds it.
struct ScriptureVerse: Codable, Equatable, Hashable {
    var verse: Int
    var text: String
    var osis: String?
}

/// The passage open in the scripture pane. The talk tier is its authority (`passage` events): the
/// pane shows what the bot reads, and a selection on the phone is sent as `client_passage` and shown
/// when the server answers with the loaded passage.
struct ScripturePassage: Codable, Equatable {
    var reference: String
    var title: String?
    var edition: String?
    /// USFM book code ("ROM") when the passage lies in one chapter.
    var book: String?
    var chapter: Int?
    var verses: [ScriptureVerse]
    /// Verses fully heard (a position in `verses`); the next reading resumes after them.
    var spokenCount: Int
    /// "tool" (opened by a reading), "client" (chosen on the phone) or "backend" (loaded while the talk tier was down).
    var source: String?

    /// Parses a talk-tier `passage` message.
    init?(message: [String: Any]) {
        guard let reference = message["reference"] as? String, let raw = message["verses"] as? [[String: Any]] else { return nil }
        let verses = raw.compactMap { v -> ScriptureVerse? in
            guard let text = v["text"] as? String else { return nil }
            return ScriptureVerse(verse: (v["verse"] as? Int) ?? Int((v["verse"] as? Double) ?? 0), text: text, osis: v["osis"] as? String)
        }
        guard !verses.isEmpty else { return nil }
        self.init(reference: reference, title: message["title"] as? String, edition: message["edition"] as? String, book: message["book"] as? String,
                  chapter: message["chapter"] as? Int, verses: verses, spokenCount: message["spoken_count"] as? Int ?? 0, source: message["source"] as? String)
    }

    init(reference: String, title: String?, edition: String?, book: String?, chapter: Int?, verses: [ScriptureVerse], spokenCount: Int, source: String?) {
        self.reference = reference
        self.title = title
        self.edition = edition
        self.book = book
        self.chapter = chapter
        self.verses = verses
        self.spokenCount = spokenCount
        self.source = source
    }

    /// The pill label, Logos style ("Rom 12").
    var shortReference: String {
        guard let code = book, let b = BibleBook.byCode[code], let chapter else { return reference }
        return "\(b.abbreviation) \(chapter)"
    }
}

/// A talk-tier `reading` message: progress of the reading of the open passage.
struct ReadingEvent: Equatable {
    /// started | verse | verse_done | paused | finished | blocked | none
    var state: String
    var reference: String?
    var verse: Int?
    /// Position of `verse` in the passage's verse list.
    var index: Int?
    var spokenCount: Int
    var durationS: Double?
    var fromIndex: Int?
    var reason: String?

    init?(message: [String: Any]) {
        guard let state = message["state"] as? String else { return nil }
        self.init(state: state, reference: message["reference"] as? String, verse: message["verse"] as? Int, index: message["index"] as? Int,
                  spokenCount: message["spoken_count"] as? Int ?? 0, durationS: (message["duration_s"] as? Double) ?? (message["duration_s"] as? Int).map(Double.init),
                  fromIndex: message["from_index"] as? Int, reason: message["reason"] as? String)
    }

    init(state: String, reference: String? = nil, verse: Int? = nil, index: Int? = nil, spokenCount: Int = 0, durationS: Double? = nil, fromIndex: Int? = nil, reason: String? = nil) {
        self.state = state
        self.reference = reference
        self.verse = verse
        self.index = index
        self.spokenCount = spokenCount
        self.durationS = durationS
        self.fromIndex = fromIndex
        self.reason = reason
    }
}

/// What the pane shades: verses heard, the verse playing now and how far into it the voice is.
///
/// The server reports when each verse starts and ends playing; between those two events the pane
/// shades word by word at the reading voice's speaking rate, which `apply` calibrates from every
/// finished verse (characters per second of its measured duration).
struct ReadingProgress: Equatable {
    var reference: String?
    var isReading = false
    /// Verses fully heard.
    var spokenCount = 0
    /// The verse playing now (position in the passage).
    var activeIndex: Int?
    var activeStartedAt: Date?
    /// Speaking rate of the reading voice; starts at am_michael's measured rate.
    var charactersPerSecond = 14.0

    mutating func reset(for passage: ScripturePassage?) {
        self = ReadingProgress(reference: passage?.reference, spokenCount: passage?.spokenCount ?? 0, charactersPerSecond: charactersPerSecond)
    }

    /// Applies one `reading` event; `verseText` gives the finished verse's text for calibration.
    mutating func apply(_ event: ReadingEvent, now: Date = Date(), verseText: (Int) -> String? = { _ in nil }) {
        if let ref = event.reference { reference = ref }
        switch event.state {
        case "started":
            isReading = true
            spokenCount = event.spokenCount
            activeIndex = nil
            activeStartedAt = nil
        case "verse":
            isReading = true
            activeIndex = event.index
            activeStartedAt = now
        case "verse_done":
            spokenCount = max(spokenCount, (event.index ?? -1) + 1)
            if let d = event.durationS, d > 0.5, let i = event.index, let text = verseText(i), !text.isEmpty {
                let measured = Double(text.count) / d
                if measured > 4, measured < 40 { charactersPerSecond = charactersPerSecond * 0.6 + measured * 0.4 }
            }
            if activeIndex == event.index {
                activeIndex = nil
                activeStartedAt = nil
            }
        case "paused", "finished", "blocked", "none":
            isReading = false
            spokenCount = event.spokenCount
            activeIndex = nil
            activeStartedAt = nil
        default:
            break
        }
    }

    /// Fraction of the active verse already spoken (0...0.97 until the server says it is done).
    func fraction(of text: String, at now: Date) -> Double {
        guard let started = activeStartedAt, !text.isEmpty else { return 0 }
        let expected = Double(text.count) / max(charactersPerSecond, 1)
        return min(0.97, max(0, now.timeIntervalSince(started) / max(expected, 0.3)))
    }
}

/// A book of the Protestant canon with its chapter count, for the passage navigator.
struct BibleBook: Identifiable, Hashable {
    var code: String
    var name: String
    var abbreviation: String
    var chapters: Int
    var newTestament: Bool
    var id: String { code }

    /// The reference sent to the talk tier for a chapter ("Psalm 23", "Romans 12").
    func reference(chapter: Int) -> String { "\(code == "PSA" ? "Psalm" : name) \(chapter)" }

    static let all: [BibleBook] = {
        let ot: [(String, String, String, Int)] = [
            ("GEN", "Genesis", "Gen", 50), ("EXO", "Exodus", "Exod", 40), ("LEV", "Leviticus", "Lev", 27), ("NUM", "Numbers", "Num", 36),
            ("DEU", "Deuteronomy", "Deut", 34), ("JOS", "Joshua", "Josh", 24), ("JDG", "Judges", "Judg", 21), ("RUT", "Ruth", "Ruth", 4),
            ("1SA", "1 Samuel", "1 Sam", 31), ("2SA", "2 Samuel", "2 Sam", 24), ("1KI", "1 Kings", "1 Kgs", 22), ("2KI", "2 Kings", "2 Kgs", 25),
            ("1CH", "1 Chronicles", "1 Chr", 29), ("2CH", "2 Chronicles", "2 Chr", 36), ("EZR", "Ezra", "Ezra", 10), ("NEH", "Nehemiah", "Neh", 13),
            ("EST", "Esther", "Esth", 10), ("JOB", "Job", "Job", 42), ("PSA", "Psalms", "Ps", 150), ("PRO", "Proverbs", "Prov", 31),
            ("ECC", "Ecclesiastes", "Eccl", 12), ("SNG", "Song of Songs", "Song", 8), ("ISA", "Isaiah", "Isa", 66), ("JER", "Jeremiah", "Jer", 52),
            ("LAM", "Lamentations", "Lam", 5), ("EZK", "Ezekiel", "Ezek", 48), ("DAN", "Daniel", "Dan", 12), ("HOS", "Hosea", "Hos", 14),
            ("JOL", "Joel", "Joel", 3), ("AMO", "Amos", "Amos", 9), ("OBA", "Obadiah", "Obad", 1), ("JON", "Jonah", "Jonah", 4),
            ("MIC", "Micah", "Mic", 7), ("NAM", "Nahum", "Nah", 3), ("HAB", "Habakkuk", "Hab", 3), ("ZEP", "Zephaniah", "Zeph", 3),
            ("HAG", "Haggai", "Hag", 2), ("ZEC", "Zechariah", "Zech", 14), ("MAL", "Malachi", "Mal", 4),
        ]
        let nt: [(String, String, String, Int)] = [
            ("MAT", "Matthew", "Matt", 28), ("MRK", "Mark", "Mark", 16), ("LUK", "Luke", "Luke", 24), ("JHN", "John", "John", 21),
            ("ACT", "Acts", "Acts", 28), ("ROM", "Romans", "Rom", 16), ("1CO", "1 Corinthians", "1 Cor", 16), ("2CO", "2 Corinthians", "2 Cor", 13),
            ("GAL", "Galatians", "Gal", 6), ("EPH", "Ephesians", "Eph", 6), ("PHP", "Philippians", "Phil", 4), ("COL", "Colossians", "Col", 4),
            ("1TH", "1 Thessalonians", "1 Thess", 5), ("2TH", "2 Thessalonians", "2 Thess", 3), ("1TI", "1 Timothy", "1 Tim", 6), ("2TI", "2 Timothy", "2 Tim", 4),
            ("TIT", "Titus", "Titus", 3), ("PHM", "Philemon", "Phlm", 1), ("HEB", "Hebrews", "Heb", 13), ("JAS", "James", "Jas", 5),
            ("1PE", "1 Peter", "1 Pet", 5), ("2PE", "2 Peter", "2 Pet", 3), ("1JN", "1 John", "1 John", 5), ("2JN", "2 John", "2 John", 1),
            ("3JN", "3 John", "3 John", 1), ("JUD", "Jude", "Jude", 1), ("REV", "Revelation", "Rev", 22),
        ]
        return ot.map { BibleBook(code: $0.0, name: $0.1, abbreviation: $0.2, chapters: $0.3, newTestament: false) }
            + nt.map { BibleBook(code: $0.0, name: $0.1, abbreviation: $0.2, chapters: $0.3, newTestament: true) }
    }()

    static let byCode: [String: BibleBook] = Dictionary(uniqueKeysWithValues: all.map { ($0.code, $0) })

    /// The chapter before or after `chapter` of `code`, crossing book boundaries; nil at either end of the canon.
    static func neighbour(of code: String, chapter: Int, step: Int) -> (BibleBook, Int)? {
        guard let i = all.firstIndex(where: { $0.code == code }) else { return nil }
        let next = chapter + step
        if next >= 1 && next <= all[i].chapters { return (all[i], next) }
        let j = i + (step > 0 ? 1 : -1)
        guard all.indices.contains(j) else { return nil }
        return (all[j], step > 0 ? 1 : all[j].chapters)
    }
}

/// Splits a passage into reading paragraphs (the retained edition carries no paragraph marks):
/// a paragraph closes after a verse that ends a sentence once it holds about `target` characters.
enum ScriptureParagraphs {
    static func split(_ verses: [ScriptureVerse], target: Int = 420) -> [[Int]] {
        var paragraphs: [[Int]] = []
        var current: [Int] = []
        var length = 0
        for (i, v) in verses.enumerated() {
            current.append(i)
            length += v.text.count
            let endsSentence = v.text.trimmingCharacters(in: .whitespaces).last.map { ".!?”’\"'".contains($0) } ?? false
            if length >= target && endsSentence {
                paragraphs.append(current)
                current = []
                length = 0
            }
        }
        if !current.isEmpty { paragraphs.append(current) }
        return paragraphs
    }
}
