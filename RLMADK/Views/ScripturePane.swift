import SwiftUI
import UIKit

/// Colours of the scripture pane: a dark reading surface in the manner of Logos.
enum ScriptureTheme {
    static let background = Color(red: 0.055, green: 0.057, blue: 0.066)
    static let bar = Color(red: 0.12, green: 0.125, blue: 0.14)
    static let pill = Color(red: 0.19, green: 0.20, blue: 0.22)
    static let accent = Color(red: 0.96, green: 0.55, blue: 0.20)
    static let ink = UIColor(white: 0.93, alpha: 1)
    /// Text not yet reached while a reading plays.
    static let inkAhead = UIColor(white: 0.93, alpha: 0.38)
    /// Text after the position where a paused reading stopped.
    static let inkUnheard = UIColor(white: 0.93, alpha: 0.66)
    static let verseNumber = UIColor(red: 0.56, green: 0.70, blue: 1.0, alpha: 1)
    /// The word being spoken now.
    static let cursor = UIColor(red: 1.0, green: 0.76, blue: 0.38, alpha: 1)
    static let heading = UIColor(white: 0.97, alpha: 1)
    static let caption = UIColor(white: 0.6, alpha: 1)
}

/// The scripture pane: top bar, the passage text and the expandable passage navigator.
struct ScripturePaneView: View {
    @EnvironmentObject var model: PortalModel
    var readMode = false
    var onToggleReadMode: () -> Void
    @State private var showNavigator = ProcessInfo.processInfo.environment["RLMADK_CI_NAVIGATOR"] == "1"

    var body: some View {
        VStack(spacing: 0) {
            topBar
            ZStack(alignment: .top) {
                content
                if showNavigator {
                    ScriptureNavigator(isPresented: $showNavigator)
                        .transition(.move(edge: .top).combined(with: .opacity))
                        .zIndex(1)
                }
            }
        }
        .background(ScriptureTheme.background)
        .clipShape(RoundedRectangle(cornerRadius: readMode ? 0 : 16, style: .continuous))
        .environment(\.colorScheme, .dark)
        // A container keeps its children's own identifiers (passagePill, readButton, ...).
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("scripturePane")
    }

    private var topBar: some View {
        HStack(spacing: 10) {
            Button { model.stepChapter(-1) } label: { Image(systemName: "chevron.left") }
                .disabled(model.passage?.book == nil)
                .accessibilityIdentifier("previousChapter")
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { showNavigator.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "book.closed")
                    Text(model.passageRequested ?? model.passage?.shortReference ?? "Choose a passage").lineLimit(1)
                    Image(systemName: showNavigator ? "chevron.up" : "chevron.down").font(.caption2)
                }
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(Capsule().fill(ScriptureTheme.pill))
            }
            .accessibilityIdentifier("passagePill")
            Button { model.stepChapter(1) } label: { Image(systemName: "chevron.right") }
                .disabled(model.passage?.book == nil)
                .accessibilityIdentifier("nextChapter")
            Spacer(minLength: 4)
            Menu {
                Button { model.scriptureFontSize = max(15, model.scriptureFontSize - 2) } label: { Label("Smaller text", systemImage: "textformat.size.smaller") }
                Button { model.scriptureFontSize = min(34, model.scriptureFontSize + 2) } label: { Label("Larger text", systemImage: "textformat.size.larger") }
            } label: { Image(systemName: "textformat.size") }
                .accessibilityIdentifier("textSizeMenu")
            Button { model.toggleReading() } label: {
                Image(systemName: model.reading.isReading ? "pause.circle.fill" : "speaker.wave.2.fill")
                    .font(.title3)
                    .foregroundStyle(model.reading.isReading ? ScriptureTheme.accent : Color.white)
            }
            .disabled(model.passage == nil)
            .accessibilityLabel(model.reading.isReading ? "Pause reading" : "Read aloud")
            .accessibilityIdentifier("readButton")
            Button(action: onToggleReadMode) {
                Image(systemName: readMode ? "xmark" : "arrow.up.left.and.arrow.down.right")
            }
            .accessibilityLabel(readMode ? "Close read mode" : "Read mode")
            .accessibilityIdentifier("readModeButton")
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(ScriptureTheme.bar)
    }

    @ViewBuilder private var content: some View {
        if let passage = model.passage {
            TimelineView(.periodic(from: .now, by: model.reading.activeIndex == nil ? 3600 : 0.12)) { context in
                ScriptureTextView(passage: passage, progress: model.reading, now: context.date, fontSize: CGFloat(model.scriptureFontSize + (readMode ? 3 : 0)))
            }
            .overlay(alignment: .bottom) {
                if let requested = model.passageRequested {
                    Label("Opening \(requested)…", systemImage: "hourglass").font(.caption).padding(8)
                        .background(Capsule().fill(ScriptureTheme.pill)).padding(.bottom, 10)
                }
            }
        } else {
            VStack(spacing: 14) {
                Image(systemName: "book").font(.largeTitle).foregroundStyle(.secondary)
                Text(model.passageRequested.map { "Opening \($0)…" } ?? "Ask for a passage, or choose one.").foregroundStyle(.secondary)
                Button("Choose a passage") { withAnimation { showNavigator = true } }.buttonStyle(.borderedProminent).tint(ScriptureTheme.accent)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

/// The passage text: justified serif paragraphs with superscript verse numbers, shaded as it is read.
///
/// A `UITextView` gives what SwiftUI `Text` lacks here: justified and hyphenated paragraphs, per-word
/// colour updates without a relayout of the whole chapter, and scrolling to the verse being read.
struct ScriptureTextView: UIViewRepresentable {
    var passage: ScripturePassage
    var progress: ReadingProgress
    var now: Date
    var fontSize: CGFloat

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.isEditable = false
        view.isSelectable = true
        view.backgroundColor = .clear
        view.textContainerInset = UIEdgeInsets(top: 18, left: 20, bottom: 60, right: 20)
        view.indicatorStyle = .white
        view.alwaysBounceVertical = true
        view.delegate = context.coordinator
        view.accessibilityIdentifier = "scriptureText"
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        context.coordinator.update(view, passage: passage, progress: progress, now: now, fontSize: fontSize)
    }

    final class Coordinator: NSObject, UITextViewDelegate {
        private var key = ""
        private var verseRanges: [NSRange] = []
        private var wordRanges: [[NSRange]] = []
        private var lastShade: [Int] = []
        private var lastScrolled: Int?
        private var userScrolledAt: Date?

        func scrollViewWillBeginDragging(_ scrollView: UIScrollView) { userScrolledAt = Date() }

        func update(_ view: UITextView, passage: ScripturePassage, progress: ReadingProgress, now: Date, fontSize: CGFloat) {
            let newKey = "\(passage.reference)|\(passage.verses.count)|\(fontSize)"
            if newKey != key {
                key = newKey
                view.attributedText = build(passage, fontSize: fontSize)
                lastShade = []
                lastScrolled = nil
                view.setContentOffset(.zero, animated: false)
            }
            let active = progress.activeIndex.flatMap { verseRanges.indices.contains($0) ? $0 : nil }
            let spokenWords = active.map { i in Int(Double(wordRanges[i].count) * progress.fraction(of: passage.verses[i].text, at: now)) } ?? 0
            let shade = [progress.isReading ? 1 : 0, progress.spokenCount, active ?? -1, spokenWords]
            if shade != lastShade {
                lastShade = shade
                applyShading(view, progress: progress, active: active, spokenWords: spokenWords)
            }
            let target = active ?? (progress.isReading ? min(progress.spokenCount, verseRanges.count - 1) : nil)
            if let target, target != lastScrolled, userScrolledAt.map({ now.timeIntervalSince($0) > 6 }) ?? true {
                lastScrolled = target
                scroll(view, to: verseRanges[target])
            }
        }

        private func serif(_ size: CGFloat, weight: UIFont.Weight = .regular) -> UIFont {
            let base = UIFont.systemFont(ofSize: size, weight: weight)
            return base.fontDescriptor.withDesign(.serif).map { UIFont(descriptor: $0, size: size) } ?? base
        }

        private func build(_ passage: ScripturePassage, fontSize: CGFloat) -> NSAttributedString {
            let out = NSMutableAttributedString()
            let heading = NSMutableParagraphStyle()
            heading.paragraphSpacing = 2
            out.append(NSAttributedString(string: passage.reference + "\n", attributes: [.font: UIFont.systemFont(ofSize: fontSize * 1.25, weight: .bold), .foregroundColor: ScriptureTheme.heading, .paragraphStyle: heading]))
            let caption = NSMutableParagraphStyle()
            caption.paragraphSpacing = fontSize * 0.9
            out.append(NSAttributedString(string: (passage.edition ?? "World English Bible").uppercased() + "\n", attributes: [.font: UIFont.systemFont(ofSize: fontSize * 0.55, weight: .semibold), .foregroundColor: ScriptureTheme.caption, .kern: 1.2, .paragraphStyle: caption]))
            let body = NSMutableParagraphStyle()
            body.alignment = .justified
            body.lineHeightMultiple = 1.22
            body.paragraphSpacing = fontSize * 0.75
            body.hyphenationFactor = 0.85
            let textFont = serif(fontSize)
            let numberFont = UIFont.systemFont(ofSize: fontSize * 0.58, weight: .bold)
            verseRanges = Array(repeating: NSRange(location: 0, length: 0), count: passage.verses.count)
            wordRanges = Array(repeating: [], count: passage.verses.count)
            let chapters = Set(passage.verses.compactMap { $0.osis?.split(separator: ".").dropLast().joined(separator: ".") })
            for paragraph in ScriptureParagraphs.split(passage.verses) {
                for (position, i) in paragraph.enumerated() {
                    let verse = passage.verses[i]
                    // A passage spanning chapters labels each verse with its chapter ("8:28").
                    let label = chapters.count > 1 ? (verse.osis.map { $0.split(separator: ".").suffix(2).joined(separator: ":") } ?? "\(verse.verse)") : "\(verse.verse)"
                    out.append(NSAttributedString(string: (position == 0 ? "" : " ") + label + "\u{00a0}", attributes: [.font: numberFont, .baselineOffset: fontSize * 0.38, .foregroundColor: ScriptureTheme.verseNumber, .paragraphStyle: body]))
                    let start = out.length
                    out.append(NSAttributedString(string: verse.text, attributes: [.font: textFont, .foregroundColor: ScriptureTheme.ink, .paragraphStyle: body]))
                    verseRanges[i] = NSRange(location: start, length: out.length - start)
                    let ns = verse.text as NSString
                    var words: [NSRange] = []
                    ns.enumerateSubstrings(in: NSRange(location: 0, length: ns.length), options: .byWords) { _, range, _, _ in
                        words.append(NSRange(location: start + range.location, length: range.length))
                    }
                    wordRanges[i] = words
                }
                out.append(NSAttributedString(string: "\n", attributes: [.font: textFont, .paragraphStyle: body]))
            }
            return out
        }

        private func applyShading(_ view: UITextView, progress: ReadingProgress, active: Int?, spokenWords: Int) {
            let storage = view.textStorage
            let count = verseRanges.count
            storage.beginEditing()
            for i in 0..<count {
                let color: UIColor
                if progress.isReading {
                    color = i < progress.spokenCount ? ScriptureTheme.ink : ScriptureTheme.inkAhead
                } else if progress.spokenCount > 0 && progress.spokenCount < count {
                    color = i < progress.spokenCount ? ScriptureTheme.ink : ScriptureTheme.inkUnheard
                } else {
                    color = ScriptureTheme.ink
                }
                storage.addAttribute(.foregroundColor, value: color, range: verseRanges[i])
            }
            if let a = active {
                // The verse being spoken: words already said in full ink, the current word in the reading colour.
                for (w, range) in wordRanges[a].enumerated() {
                    let color = w < spokenWords ? ScriptureTheme.ink : (w == spokenWords ? ScriptureTheme.cursor : ScriptureTheme.inkAhead)
                    storage.addAttribute(.foregroundColor, value: color, range: range)
                }
            }
            storage.endEditing()
        }

        private func scroll(_ view: UITextView, to range: NSRange) {
            guard let start = view.position(from: view.beginningOfDocument, offset: range.location),
                  let end = view.position(from: start, offset: max(1, min(range.length, 40))),
                  let textRange = view.textRange(from: start, to: end) else { return }
            view.layoutIfNeeded()
            let rect = view.firstRect(for: textRange)
            guard !rect.isNull, !rect.isInfinite else { return }
            let visible = view.bounds.insetBy(dx: 0, dy: view.bounds.height * 0.18)
            if visible.contains(rect) { return }
            let maxY = max(0, view.contentSize.height - view.bounds.height + view.contentInset.bottom)
            let y = min(maxY, max(0, rect.minY - view.bounds.height * 0.28))
            view.setContentOffset(CGPoint(x: 0, y: y), animated: true)
        }
    }
}

/// Logos-style passage picker: testament tabs, a grid of books tinted by genre, then a chapter grid,
/// and a field for any reference ("John 3:16").
struct ScriptureNavigator: View {
    @EnvironmentObject var model: PortalModel
    @Binding var isPresented: Bool
    @State private var newTestament = false
    @State private var book: BibleBook?
    @State private var typed = ""
    @FocusState private var typing: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                if book != nil {
                    Button { withAnimation { book = nil } } label: { Label("Books", systemImage: "chevron.left") }
                        .accessibilityIdentifier("navigatorBack")
                }
                Text(book?.name ?? "Go to passage").font(.headline)
                Spacer()
                Button("Done") { typing = false; withAnimation { isPresented = false } }.accessibilityIdentifier("navigatorDone")
            }
            if let book {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 46), spacing: 8)], spacing: 8) {
                        ForEach(1...book.chapters, id: \.self) { chapter in
                            let current = model.passage?.book == book.code && model.passage?.chapter == chapter
                            Button { open(book, chapter) } label: {
                                Text("\(chapter)").font(.body.monospacedDigit().weight(current ? .bold : .regular))
                                    .frame(maxWidth: .infinity, minHeight: 40)
                                    .background(RoundedRectangle(cornerRadius: 8).fill(current ? ScriptureTheme.accent.opacity(0.85) : ScriptureTheme.pill))
                            }
                            .accessibilityIdentifier("chapter-\(chapter)")
                        }
                    }
                }
            } else {
                Picker("Testament", selection: $newTestament) {
                    Text("Old Testament").tag(false)
                    Text("New Testament").tag(true)
                }
                .pickerStyle(.segmented)
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 66), spacing: 8)], spacing: 8) {
                        ForEach(BibleBook.all.filter { $0.newTestament == newTestament }) { b in
                            let current = model.passage?.book == b.code
                            Button {
                                if b.chapters == 1 { open(b, 1) } else { withAnimation { book = b } }
                            } label: {
                                Text(b.abbreviation).font(.footnote.weight(current ? .bold : .medium)).lineLimit(1).minimumScaleFactor(0.7)
                                    .frame(maxWidth: .infinity, minHeight: 40)
                                    .background(RoundedRectangle(cornerRadius: 8).fill(Self.genreColor(b).opacity(current ? 0.9 : 0.42)))
                                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(current ? ScriptureTheme.accent : .clear, lineWidth: 2))
                            }
                            .accessibilityIdentifier("book-\(b.code)")
                        }
                    }
                }
            }
            HStack {
                TextField("Reference, e.g. John 3:16", text: $typed)
                    .textFieldStyle(.roundedBorder)
                    .focused($typing)
                    .submitLabel(.go)
                    .onSubmit { go() }
                    .accessibilityIdentifier("referenceField")
                Button("Go") { go() }.disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(ScriptureTheme.bar).shadow(color: .black.opacity(0.5), radius: 12, y: 6))
        .padding(8)
        .onAppear {
            if let code = model.passage?.book, let b = BibleBook.byCode[code] { newTestament = b.newTestament }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("passageNavigator")
    }

    private func open(_ b: BibleBook, _ chapter: Int) {
        model.openChapter(b, chapter)
        withAnimation { isPresented = false; book = nil }
    }

    private func go() {
        let ref = typed.trimmingCharacters(in: .whitespaces)
        guard !ref.isEmpty else { return }
        model.openPassage(ref)
        typed = ""
        typing = false
        withAnimation { isPresented = false }
    }

    /// Book groups, tinted as Logos tints its book grid.
    static func genreColor(_ b: BibleBook) -> Color {
        let order = BibleBook.all.firstIndex(of: b) ?? 0
        switch order {
        case 0...4: return Color(red: 0.75, green: 0.45, blue: 0.20)    // Law
        case 5...16: return Color(red: 0.50, green: 0.58, blue: 0.25)   // History
        case 17...21: return Color(red: 0.55, green: 0.38, blue: 0.70)  // Wisdom and poetry
        case 22...26: return Color(red: 0.72, green: 0.27, blue: 0.27)  // Major prophets
        case 27...38: return Color(red: 0.75, green: 0.38, blue: 0.52)  // Minor prophets
        case 39...42: return Color(red: 0.25, green: 0.47, blue: 0.80)  // Gospels
        case 43: return Color(red: 0.20, green: 0.60, blue: 0.62)       // Acts
        case 44...56: return Color(red: 0.28, green: 0.62, blue: 0.40)  // Paul's letters
        case 57...64: return Color(red: 0.25, green: 0.58, blue: 0.75)  // General letters
        default: return Color(red: 0.42, green: 0.36, blue: 0.75)       // Revelation
        }
    }
}
