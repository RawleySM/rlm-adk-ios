import XCTest
@testable import RLMADK

/// Wire formats of the talk tier: RTVI and floor messages into events, client messages out, the
/// tagged-PCM framing, the WebSocket URL built from `/start`, and devotional alignment.
final class TalkProtocolTests: XCTestCase {
    func testRTVIMessagesBecomeEvents() {
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"user-transcription","data":{"text":"What does Psalm twenty three say about rest?","user_id":"","final":true}}"#),
                       [.userTranscript(text: "What does Psalm twenty three say about rest?", final: true)])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-started-speaking"}"#), [.botStartedSpeaking])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-stopped-speaking"}"#), [.botStoppedSpeaking])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-transcription","data":{"text":" what I know"}}"#), [], "not sent to RTVI 2 clients; bot-output is the one display source")
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-output","data":{"text":"Let me look.","aggregated_by":"sentence","spoken":false,"will_be_spoken":true,"spoken_status":"new"}}"#), [.botTranscript(text: "Let me look.")])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-output","data":{"text":"Let me look.","aggregated_by":"sentence","spoken":true,"will_be_spoken":true,"spoken_status":"completed"}}"#), [], "shown once, when queued")
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-output","data":{"text":"tool text","aggregated_by":"sentence","will_be_spoken":false,"spoken_status":"new"}}"#), [])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-tts-text","data":{"text":"Let me look."}}"#), [.botSpokenText(text: "Let me look.")])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"bot-interrupted"}"#), [.interruption])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"label":"rtvi-ai","type":"metrics","data":{"ttfb":[]}}"#), [])
        XCTAssertEqual(TalkMessageParser.events(fromText: "not json"), [])
    }

    func testFloorMessagesBothPlainAndAsServerMessages() {
        let plain = #"{"type":"mode","mode":"prayer","from":"available","reason":"address:lord","t":1791947096.59}"#
        XCTAssertEqual(TalkMessageParser.events(fromText: plain), [.floorMode(mode: "prayer", from: "available", reason: "address:lord")])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"prayer_text","text":"Lord, thank you.","final":true}"#), [.prayerText(text: "Lord, thank you.", final: true)])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"interruption"}"#), [.interruption])
        // Over WebRTC the same messages arrive wrapped as RTVI server messages.
        let wrapped = #"{"label":"rtvi-ai","type":"server-message","data":{"type":"mode","mode":"available","from":"prayer","reason":"amen"}}"#
        XCTAssertEqual(TalkMessageParser.events(fromText: wrapped), [.floorMode(mode: "available", from: "prayer", reason: "amen")])
    }

    func testAssistantNameLabelsReplies() {
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"assistant","name":"Qwen3.8"}"#), [.assistantName("Qwen3.8")])
        let wrapped = #"{"label":"rtvi-ai","type":"server-message","data":{"type":"assistant","name":"Qwen3.8"}}"#
        XCTAssertEqual(TalkMessageParser.events(fromText: wrapped), [.assistantName("Qwen3.8")])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"assistant","name":""}"#), [])
    }

    func testClientMessagesOnTheWire() throws {
        let mode = TalkClientMessage.clientMode(.prayer, reason: "address:lord").json
        XCTAssertEqual(mode["type"] as? String, "client_mode")
        XCTAssertEqual(mode["mode"] as? String, "prayer")
        XCTAssertEqual(mode["reason"] as? String, "address:lord")
        let text = TalkClientMessage.clientText("hello").json
        XCTAssertEqual(text as NSDictionary, ["type": "client_text", "text": "hello"] as NSDictionary)
        let event = TalkClientMessage.clientEvent(name: "stop", detail: ["k": "v"])
        XCTAssertEqual(event.type, "client_event")
        XCTAssertEqual(event.fields, ["event": "stop", "k": "v"])
    }

    func testTaggedPCMFraming() {
        XCTAssertEqual(PCMFraming.silenceFrame.count, 641)
        XCTAssertEqual(PCMFraming.silenceFrame.first, 0x01)
        var pending = Data(repeating: 7, count: 640 * 2 + 100)
        let frames = PCMFraming.takeFrames(from: &pending)
        XCTAssertEqual(frames.count, 2)
        XCTAssertTrue(frames.allSatisfy { $0.count == 641 && $0.first == 0x01 })
        XCTAssertEqual(pending.count, 100, "the remainder waits for the next buffer")
        // int16 little endian both ways.
        let bytes = PCMFraming.int16LE(fromFloats: [0, 1, -1, 0.5])
        XCTAssertEqual(Array(bytes), [0x00, 0x00, 0xFF, 0x7F, 0x01, 0x80, 0x00, 0x40])
        let back = PCMFraming.floats(fromInt16LE: bytes)
        XCTAssertEqual(back[1], 32767.0 / 32768.0, accuracy: 1e-6)
        XCTAssertEqual(back[2], -32767.0 / 32768.0, accuracy: 1e-6)
        XCTAssertEqual(PCMFraming.audioPayload(Data([0x01, 0x10, 0x20])), Data([0x10, 0x20]))
        XCTAssertNil(PCMFraming.audioPayload(Data([0x02, 0x10])))
        XCTAssertNil(PCMFraming.audioPayload(Data([0x01])))
    }

    func testSocketURLUsesOnlyThePathOfTheAdvertisedURL() throws {
        let base = URL(string: "http://100.121.221.42:7870")!
        // The runner advertises its bind host and a wrong scheme; only the path is used.
        XCTAssertEqual(try WebSocketTalkClient.socketURL(base: base, wsUrl: "wss://0.0.0.0:7870/ws-client", token: nil).absoluteString, "ws://100.121.221.42:7870/ws-client")
        XCTAssertEqual(try WebSocketTalkClient.socketURL(base: base, wsUrl: "wss://0.0.0.0:7870/ws-client", token: "abc").absoluteString, "ws://100.121.221.42:7870/ws-client/abc")
        XCTAssertEqual(try WebSocketTalkClient.socketURL(base: URL(string: "https://talk.example:443")!, wsUrl: "/ws-client/tok", token: "tok").absoluteString, "wss://talk.example:443/ws-client/tok")
        XCTAssertThrowsError(try WebSocketTalkClient.socketURL(base: base, wsUrl: nil, token: nil))
    }

    func testVoiceLevelTrackerIgnoresDigitalSilence() {
        var tracker = VoiceLevelTracker()
        var t: TimeInterval = 0
        func feed(_ db: Double, _ seconds: Double) { for _ in 0..<Int(seconds / 0.085) { tracker.observe(levelDb: db, duration: 0.085, at: t); t += 0.085 } }
        feed(-57, 2)
        feed(-120, 0.5) // an audio unit starting: zeros
        feed(-57, 1)
        XCTAssertFalse(tracker.active, "room noise after digital silence is not voice")
        feed(-22, 1.5)
        XCTAssertTrue(tracker.active)
        feed(-57, 0.3)
        XCTAssertFalse(tracker.active)
        XCTAssertNotNil(tracker.lastVoiceEndUptime, "the end of the speech is seen")
    }

    func testDevotionalTrackerAlignsRunsAndSkipsCommentary() {
        var tracker = DevotionalTracker(segments: ["Yahweh is my shepherd: I shall lack nothing.", "He makes me lie down in green pastures.", "He restores my soul."], startIndex: 0)
        XCTAssertEqual(tracker.spoken("Here is the passage you asked for."), [])
        XCTAssertEqual(tracker.spoken("Yahweh is my shepherd:"), [.started(0)])
        XCTAssertEqual(tracker.spoken("I shall lack nothing. He makes me lie down"), [.finished(0), .started(1)])
        XCTAssertEqual(tracker.segment, 1)
        // A skipped tail still finishes the segment once the reading is clearly in the next one.
        XCTAssertEqual(tracker.spoken("He restores my soul."), [.finished(1), .started(2), .finished(2)])
        XCTAssertTrue(tracker.isComplete)
        var resumed = DevotionalTracker(segments: ["a b c", "d e f"], startIndex: 1)
        XCTAssertEqual(resumed.spoken("a b c d e f"), [.started(1), .finished(1)], "words before the resume point are not progress")
    }
}
