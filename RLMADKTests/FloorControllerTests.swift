import XCTest
@testable import RLMADK

/// The phone's prayer floor through the real controller: prayer by address on a partial, the amen
/// rule with its silence window (clocked by the test), long pauses that never end a prayer, quiet
/// that blocks the voice and tells the server, and the server's floor messages.
@MainActor
final class FloorControllerTests: XCTestCase {
    private var roots: [URL] = []
    private var now = Date(timeIntervalSince1970: 1_800_000_000)

    override func tearDown() {
        for root in roots { try? FileManager.default.removeItem(at: root) }
        roots = []
        super.tearDown()
    }

    /// A model on its own store with a fake talk client already connected (ready).
    private func makeModel() -> (PortalModel, FakeTalkClient) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("floor-\(UUID().uuidString)")
        roots.append(root)
        let model = PortalModel(store: LocalStore(root: root))
        model.recognitionStarter = {}
        model.deliveryProbe = { true }
        model.floorClock = { [unowned self] in self.now }
        var made: FakeTalkClient?
        model.talkFactory = { kind in
            let fake = FakeTalkClient(kind: kind)
            made = fake
            return fake
        }
        model.connectTalk()
        return (model, made!)
    }

    private func advance(_ seconds: Double, tick model: PortalModel) {
        now = now.addingTimeInterval(seconds)
        model.floorTick()
    }

    func testConnectTellsTheServerThePhonesMode() {
        let (model, talk) = makeModel()
        XCTAssertEqual(talk.connectedTo?.absoluteString, model.talkURLString)
        XCTAssertEqual(talk.modes, [.available], "the persisted mode is sent on connect")
        XCTAssertTrue(talk.fakeVoice.gateOpen)
    }

    func testAddressOnAPartialEntersPrayerImmediately() throws {
        let (model, talk) = makeModel()
        model.partialTranscriptReceived("What does")
        XCTAssertFalse(model.mode.isPraying, "ordinary speech is not prayer")
        model.recognitionRestarted("final")
        model.partialTranscriptReceived("Lord")
        XCTAssertTrue(model.mode.isPraying, "the first partial that shows the address enters prayer, before the utterance ends")
        let captureId = try XCTUnwrap(model.mode.prayerCaptureId)
        XCTAssertEqual(model.captures.first(where: { $0.captureId == captureId })?.trigger, "address:lord")
        XCTAssertEqual(talk.modes.last, .prayer, "the server is told before its own detection could fire")
        XCTAssertGreaterThanOrEqual(talk.fakeVoice.stops, 1, "any bot audio stops on entry")
        XCTAssertFalse(talk.fakeVoice.gateOpen)
        XCTAssertEqual(model.recognizer.nextOwner, captureId)
        // The utterance that carried the address belongs to the prayer (no request was open to
        // adopt under XCTest, so its final arrives as a conversation final and is journaled here).
        model.finalTranscriptReceived("Lord thank you for this quiet evening", owner: nil)
        XCTAssertEqual(model.prayerASRJournal(captureId)?.lines.map(\.text), ["Lord thank you for this quiet evening"])
        XCTAssertTrue(model.mode.isPraying)
    }

    func testAmenThenSilenceEndsPrayer() throws {
        let (model, talk) = makeModel()
        model.partialTranscriptReceived("Lord")
        let captureId = try XCTUnwrap(model.mode.prayerCaptureId)
        advance(2, tick: model)
        model.partialTranscriptReceived("Lord keep my family safe tonight amen")
        advance(1.0, tick: model)
        XCTAssertTrue(model.mode.isPraying, "1.0 s after the amen is inside the window")
        advance(0.25, tick: model)
        XCTAssertFalse(model.mode.isPraying, "amen followed by 1.2 s with no new words ends the prayer")
        XCTAssertEqual(talk.modes.last, .available)
        XCTAssertEqual(model.captures.first(where: { $0.captureId == captureId })?.finalizeRequestedAt != nil, true, "the capture is saved")
        XCTAssertNil(model.recognizer.nextOwner)
    }

    func testAmenFollowedByMoreWordsDoesNotEnd() {
        let (model, _) = makeModel()
        model.partialTranscriptReceived("Dear Lord")
        XCTAssertTrue(model.mode.isPraying)
        model.partialTranscriptReceived("Dear Lord bless this home amen")
        advance(0.8, tick: model)
        model.partialTranscriptReceived("Dear Lord bless this home amen and bless my brother")
        advance(0.5, tick: model)
        advance(1.0, tick: model)
        XCTAssertTrue(model.mode.isPraying, "more words cancel the pending end")
        advance(30, tick: model)
        XCTAssertTrue(model.mode.isPraying, "and silence alone never ends it")
        // A final from the prayer's own request that ends in amen starts the window again.
        model.finalTranscriptReceived("and bless my brother amen", owner: model.mode.prayerCaptureId)
        advance(1.25, tick: model)
        XCTAssertFalse(model.mode.isPraying)
    }

    func testThreeAndFourAndAHalfSecondPausesNeverEndPrayer() {
        let (model, _) = makeModel()
        model.voiceProbe = { VoiceFacts(active: false, lastVoiceEnd: self.now) }
        model.partialTranscriptReceived("Heavenly Father")
        XCTAssertTrue(model.mode.isPraying)
        for _ in 0..<30 { advance(0.1, tick: model) }
        XCTAssertTrue(model.mode.isPraying, "3.0 s pause")
        model.partialTranscriptReceived("Heavenly Father I am still here")
        for _ in 0..<45 { advance(0.1, tick: model) }
        XCTAssertTrue(model.mode.isPraying, "4.5 s pause")
        model.finalTranscriptReceived("Heavenly Father I am still here and listening", owner: model.mode.prayerCaptureId)
        for _ in 0..<45 { advance(0.1, tick: model) }
        XCTAssertTrue(model.mode.isPraying, "a final without amen ends nothing")
    }

    func testQuietBlocksPlaybackAndTellsTheServer() {
        let (model, talk) = makeModel()
        talk.onEvent?(.botStartedSpeaking)
        XCTAssertEqual(model.mode.activity, .responding(turnId: "bot-1"))
        let stopsBefore = talk.fakeVoice.stops
        model.enterQuiet()
        XCTAssertFalse(talk.fakeVoice.gateOpen, "the gate closes on quiet")
        XCTAssertGreaterThan(talk.fakeVoice.stops, stopsBefore, "playback stops immediately")
        XCTAssertEqual(talk.modes.last, .quiet, "client_mode quiet is sent")
        XCTAssertEqual(model.mode.activity, .idle)
        // The server speaks anyway: stopped again, mode re-asserted, no speaking state.
        let stopsQuiet = talk.fakeVoice.stops
        talk.onEvent?(.botStartedSpeaking)
        XCTAssertEqual(talk.fakeVoice.stops, stopsQuiet + 1)
        XCTAssertEqual(talk.modes.last, .quiet)
        XCTAssertEqual(model.mode.activity, .idle)
        model.wake()
        XCTAssertTrue(talk.fakeVoice.gateOpen)
        XCTAssertEqual(talk.modes.last, .available)
    }

    func testEngineVoiceOutputDropsBotAudioWhileTheGateIsClosed() {
        let owner = AudioOwner()
        let voice = EngineVoiceOutput(audio: owner)
        let frame = Data(count: 960) // 20 ms at 24 kHz
        voice.gateOpen = false
        voice.enqueue(pcm24k: frame)
        XCTAssertEqual(voice.counters.droppedByGate, 1, "quiet/prayer: dropped before it reaches the engine")
        voice.gateOpen = true
        voice.enqueue(pcm24k: frame)
        XCTAssertEqual(voice.counters.droppedByGate, 1)
        XCTAssertEqual(voice.counters.droppedNoEngine, 1, "gate open: it goes to the engine (not running under XCTest)")
        voice.botTurnStarted()
        voice.stop()
        voice.enqueue(pcm24k: frame)
        XCTAssertEqual(voice.counters.droppedByGate, 2, "Stop silences the rest of the bot turn")
        voice.botTurnEnded()
        voice.enqueue(pcm24k: frame)
        XCTAssertEqual(voice.counters.droppedByGate, 2, "the next turn plays again")
        XCTAssertEqual(voice.received.frames, 4)
        // Regression (device, 2026-10-14): entering prayer with no bot turn in progress (gate closes,
        // then stop) must not suppress the first reply after the prayer.
        voice.gateOpen = false
        voice.stop()
        voice.gateOpen = true
        voice.enqueue(pcm24k: frame)
        XCTAssertEqual(voice.counters.droppedByGate, 2, "the first reply after a prayer plays")
        // A turn cut by the gate is over once the gate reopens.
        voice.botTurnStarted()
        voice.gateOpen = false
        voice.stop()
        voice.gateOpen = true
        voice.enqueue(pcm24k: frame)
        XCTAssertEqual(voice.counters.droppedByGate, 2)
    }

    func testServerFloorMessagesAreFollowedAndEchoesIgnored() {
        let (model, talk) = makeModel()
        talk.onEvent?(.floorMode(mode: "prayer", from: "available", reason: "address:lord"))
        XCTAssertTrue(model.mode.isPraying)
        XCTAssertTrue(model.captures.first?.trigger?.hasPrefix("server") ?? false)
        talk.onEvent?(.prayerText(text: "Lord, thank you for this quiet evening.", final: true))
        XCTAssertEqual(model.talkPrayerLines[model.mode.prayerCaptureId!], 1)
        talk.onEvent?(.floorMode(mode: "available", from: "prayer", reason: "client:sync"))
        XCTAssertTrue(model.mode.isPraying, "the echo of our own client_mode changes nothing")
        talk.onEvent?(.floorMode(mode: "available", from: "prayer", reason: "amen"))
        XCTAssertFalse(model.mode.isPraying, "the server's amen rule is followed")
        talk.onEvent?(.floorMode(mode: "quiet", from: "available", reason: "phrase"))
        XCTAssertTrue(model.mode.quiet)
        talk.onEvent?(.floorMode(mode: "available", from: "quiet", reason: "wake"))
        XCTAssertFalse(model.mode.quiet)
    }

    func testTypedInputGoesToTheTalkServerAndVoiceFinalsDoNot() {
        let (model, talk) = makeModel()
        model.typedInput = "What does the shepherd psalm say about rest?"
        model.submitTyped()
        XCTAssertEqual(talk.texts, ["What does the shepherd psalm say about rest?"])
        XCTAssertEqual(model.lastTranscript, "What does the shepherd psalm say about rest?")
        model.finalTranscriptReceived("and what about still waters", owner: nil)
        XCTAssertEqual(talk.texts.count, 1, "the server transcribes voice itself; the phone never re-sends it")
        model.typedInput = "quiet please"
        model.submitTyped()
        XCTAssertTrue(model.mode.quiet, "typed control phrases act locally")
        XCTAssertEqual(talk.texts.count, 1)
    }

    func testBotTranscriptsAndStop() {
        let (model, talk) = makeModel()
        talk.onEvent?(.userTranscript(text: "What does Psalm twenty three say about rest?", final: true))
        talk.onEvent?(.botStartedSpeaking)
        talk.onEvent?(.botTranscript(text: "He makes me lie down"))
        talk.onEvent?(.botTranscript(text: " in green pastures."))
        XCTAssertEqual(model.assistantText, "He makes me lie down in green pastures.")
        XCTAssertEqual(model.serverHeard, "What does Psalm twenty three say about rest?")
        XCTAssertTrue(model.botSpeaking)
        model.stopOutput()
        XCTAssertEqual(model.mode.activity, .idle)
        XCTAssertTrue(talk.sent.contains(.clientEvent(name: "stop", detail: [:])))
        talk.onEvent?(.botStoppedSpeaking)
        XCTAssertFalse(model.botSpeaking)
        XCTAssertEqual(talk.fakeVoice.turnEnds, 1)
    }

    func testDevotionalPositionFollowsSpokenTextAndInterruptionKeepsTheSegment() {
        let (model, talk) = makeModel()
        model.devotional = Devotional(available: true, reason: nil, passage: "PSA.23", title: "Psalm 23", revision_hash: "rev1", source_doc_id: nil, teaching_found: false, segments: [
            .init(kind: "scripture", osis: "Ps.23.1", verse: 1, text: "Yahweh is my shepherd: I shall lack nothing.", cite_id: "c1", title: nil, attribution: nil),
            .init(kind: "scripture", osis: "Ps.23.2", verse: 2, text: "He makes me lie down in green pastures. He leads me beside still waters.", cite_id: "c2", title: nil, attribution: nil),
            .init(kind: "scripture", osis: "Ps.23.3", verse: 3, text: "He restores my soul.", cite_id: "c3", title: nil, attribution: nil),
        ])
        model.playDevotional()
        XCTAssertTrue(model.devotionalPlaying)
        XCTAssertTrue(talk.texts.last?.contains("from Ps.23.1") ?? false)
        talk.onEvent?(.botSpokenText(text: "Here is Psalm 23."))
        XCTAssertEqual(model.progress?.resumeIndex, 0, "the bot's own words do not move the position")
        talk.onEvent?(.botSpokenText(text: "Yahweh is my shepherd: I shall lack nothing."))
        XCTAssertEqual(model.progress?.resumeIndex, 1, "verse 1 spoken to its end")
        talk.onEvent?(.botSpokenText(text: "He makes me lie down in green pastures."))
        XCTAssertEqual(model.progress?.resumeIndex, 1, "verse 2 started, not finished")
        talk.onEvent?(.interruption)
        XCTAssertFalse(model.devotionalPlaying)
        XCTAssertEqual(model.progress?.resumeIndex, 1, "continue repeats the interrupted verse in full")
        XCTAssertEqual(model.mode.devotionalPosition, 1)
        model.playDevotional()
        XCTAssertTrue(talk.texts.last?.contains("from Ps.23.2") ?? false)
    }

    func testPrayerPartialsThatRestartAfterPausesAreJournaled() throws {
        let (model, _) = makeModel()
        model.partialTranscriptReceived("Lord")
        let captureId = try XCTUnwrap(model.mode.prayerCaptureId)
        model.partialTranscriptReceived("Lord thank you for this quiet evening")
        // iOS 26 restarts the partial text after a pause, inside the same request.
        model.partialTranscriptReceived("I am still here")
        model.partialTranscriptReceived("I am still here and I am listening")
        model.partialTranscriptReceived("Thank you for your patience with me amen")
        advance(1.25, tick: model)
        XCTAssertFalse(model.mode.isPraying)
        XCTAssertEqual(model.prayerASRJournal(captureId)?.lines.map(\.text), [
            "Lord thank you for this quiet evening",
            "I am still here and I am listening",
            "Thank you for your patience with me amen",
        ])
        XCTAssertTrue(PortalModel.continues("Lord thank you for this", with: "Lord, thank you for this quiet"))
        XCTAssertFalse(PortalModel.continues("Lord thank you for this quiet evening", with: "I am still here"))
    }
}
