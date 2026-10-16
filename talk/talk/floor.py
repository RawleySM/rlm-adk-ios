"""Floor control: prayer by address, the amen rule, quiet/wake, and the output gate.

``FloorProcessor`` sits right after the STT service. It owns the conversation mode
(``available`` | ``prayer`` | ``quiet`` | ``bug``) and announces every change to the client as a transport
message ``{"type": "mode", "mode": ..., "reason": ...}`` so the phone can mark its own capture.

Prayer: an utterance that begins with an address phrase (``cfg.address_phrases``) enters prayer; the
client may also enter it earlier from its on-device partial transcript with
``{"type": "client_mode", "mode": "prayer"}``. While praying, no transcript reaches the LLM aggregator
(nothing is ever answered) and the ``OutputGate`` drops every bot audio and text frame. Prayer ends
when a final transcript ends with an amen phrase and ``cfg.amen_silence_s`` pass with no new speech;
new speech cancels the pending end. Silence alone never ends prayer, and nothing is spoken at the end
(MODE-04: save and wait).

Quiet: ``quiet please`` (or the client message) suppresses output until ``wake up``; capture and
backend work continue because they never depended on this process.

Bug (``talk.bug_report``): the phone's Bug chip (``client_bug`` start) silences output and stops
every turn reaching the LLM, like quiet, while transcription goes on: each final transcript (and any
typed text) is kept as a segment of the report and echoed to the phone (``bug_text``). The chip's
second tap (``client_bug`` stop) files the report with the skill service's intake issues log, returns
to the mode before and resumes a reading the press interrupted. The Flows node is left as it was.

Scripture pane (``talk.passage``): the floor also owns the passage open on the phone. A
``client_passage`` selection interrupts any reading, loads the passage, tells the client and
refreshes the LLM's system instruction so questions refer to it; ``client_read`` (the pane's Read
and Pause) and the spoken read phrases ("read it", "continue", "read this again") read the open
passage from where it stopped, verse by verse in the reading voice (``ScriptureSpeakFrame``).
"""

from __future__ import annotations

import asyncio
import re
import time

from loguru import logger
from pipecat.frames.frames import (
    StartFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputTransportMessageFrame,
    InterimTranscriptionFrame,
    LLMTextFrame,
    InterruptionFrame,
    OutputTransportMessageUrgentFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talk import bug_report, passage
from talk.bug_report import BugReport
from talk.config import TalkConfig
from talk.scripture_tts import ScriptureSpeakFrame


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z' ]+", " ", text.lower()).split())


class FloorProcessor(FrameProcessor):
    """Mode owner placed after STT; see the module docstring."""

    def __init__(self, cfg: TalkConfig, reading_state: dict | None = None, *, session_id: str = ""):
        super().__init__(name="floor")
        self._cfg = cfg
        self.session_id = session_id
        self.reading_state = reading_state if reading_state is not None else {}
        # Operator hold (``/admin/hold``): quiet until released; the wake phrase does not lift it.
        self.held = False
        self.mode = "available"
        self._task = None
        self._rtvi = None
        self._flow = None
        self._flow_started = False
        self._last_node: str | None = None
        self._vad_stopped_at: float | None = None
        self._user_speaking = False
        self._pending_amen: asyncio.Task | None = None
        self._address = sorted((_norm(p) for p in cfg.address_phrases), key=len, reverse=True)
        self._amen = sorted((_norm(p) for p in cfg.amen_phrases), key=len, reverse=True)
        self._quiet = [_norm(p) for p in cfg.quiet_phrases]
        self._wake = [_norm(p) for p in cfg.wake_phrases]
        self._backchannel = {_norm(w) for w in cfg.backchannel_words}
        self._pause = {_norm(w) for w in cfg.pause_phrases}
        self._continue = [_norm(w) for w in cfg.continue_phrases]
        self.bot_speaking = False
        # Set by the ReadingTracker (the last processor) when an interruption has crossed the whole pipeline.
        self.interruption_seen = asyncio.Event()
        # Wall time of the last BotStoppedSpeakingFrame or interruption; the VAD's double-talk rule holds
        # for a moment after it so the gaps between verses of a reading count as bot speech.
        self.bot_stopped_at = 0.0
        # True from the moment a transcript was forwarded to the LLM until the bot starts speaking.
        self.pending_turn = False
        self.events: list[dict] = []
        # The report in progress while the mode is ``bug`` (``start_bug`` / ``stop_bug``).
        self.bug: BugReport | None = None
        # The session's LLMContext (set by the bot) so a report carries the recent conversation.
        self.context = None
        self._last_final_at = 0.0
        self._bug_tasks: set[asyncio.Task] = set()

    def bind(self, task, rtvi=None, flow=None) -> None:
        self._task = task
        self._rtvi = rtvi
        self._flow = flow
        self._last_node: str | None = None

    @property
    def node(self) -> str | None:
        """Current Pipecat Flows node name (``talk.flows``), or None without a flow."""
        return getattr(self._flow, "current_node", None) if self._flow is not None else None

    async def set_node(self, name: str, reason: str) -> None:
        """Switch the Flows node (prayer / quiet / available / conversation) and tell the client."""
        if self._flow is None or self._flow.current_node == name:
            return
        from talk.flows import node as flow_node

        try:
            await self._flow.set_node_from_config(flow_node(name, reading_state=self.reading_state))
        except Exception as exc:  # noqa: BLE001 - a failed node switch must not take the floor down
            logger.warning(f"flows: set_node {name} failed: {exc}")
            return
        await self.announce_node(reason)

    async def announce_node(self, reason: str) -> None:
        current = self.node
        if current != self._last_node:
            self._last_node = current
            self.events.append({"t": time.time(), "node": current, "reason": reason})
            await self._tell_client({"type": "node", "node": current, "reason": reason, "t": time.time()})

    @property
    def assistant_name(self) -> str:
        """Who answers, as the app labels replies (config ``llm_display_name``, else ``llm_model``)."""
        return self._cfg.llm_display_name or self._cfg.llm_model

    async def _tell_client(self, message: dict) -> None:
        """Our bare JSON for the WebSocket client plus an RTVI server-message for the Pipecat SDK client."""
        await self.push_frame(OutputTransportMessageUrgentFrame(message=message))
        if self._rtvi is not None:
            try:
                await self._rtvi.send_server_message(message)
            except Exception as exc:  # noqa: BLE001 - the WebSocket path already has the message
                logger.warning(f"rtvi server message failed: {exc}")


    async def _swallow(self, frame) -> None:
        """Drop a transcript's words but still tell the aggregator a transcript came.

        Pipecat's turn-stop strategy waits for a finalized transcript after Smart Turn says the turn is
        complete; a transcript dropped here (prayer text, a pause word, a backchannel) left it waiting
        the full ``user_turn_stop_timeout`` (15 s) with the bot interrupted. An empty finalized
        TranscriptionFrame satisfies the wait; the aggregator ignores empty text, so no LLM turn runs.
        """
        if not isinstance(frame, TranscriptionFrame):
            return
        empty = TranscriptionFrame(text="", user_id=getattr(frame, "user_id", ""), timestamp=getattr(frame, "timestamp", ""))
        try:
            empty.finalized = True
        except Exception:  # noqa: BLE001 - older frame classes have no such attribute
            pass
        await self.push_frame(empty, FrameDirection.DOWNSTREAM)

    # ----- mode transitions -----
    async def set_mode(self, mode: str, reason: str, *, switch_node: bool = True) -> None:
        if mode == self.mode:
            return
        if self.mode == "bug" and not reason.startswith("bug:"):
            # The phone re-asserting prayer/quiet/available while a report is open: that is where the
            # floor returns when the report ends; the report itself only ends by client_bug.
            if self.bug is not None:
                self.bug.mode_before = mode
            logger.info(f"floor: mode {mode} ({reason}) kept for after the bug report")
            return
        previous = self.mode
        self.mode = mode
        stamp = time.time()
        self.events.append({"t": stamp, "mode": mode, "from": previous, "reason": reason})
        logger.info(f"floor: {previous} -> {mode} ({reason})")
        if mode in {"prayer", "quiet", "bug"}:
            # Silence whatever is playing or queued, now.
            await self.broadcast_interruption()
        await self._tell_client({"type": "mode", "mode": mode, "from": previous, "reason": reason, "t": stamp, "node": self.node})
        # Flows node follows the floor: prayer and quiet have their own nodes; leaving them returns to
        # available (the next request moves it on to conversation / devotional / project). Entering and
        # leaving a bug report keeps the node, so the conversation goes on where it was.
        if switch_node:
            await self.set_node(mode if mode in {"prayer", "quiet"} else "available", reason)

    def _starts_with_address(self, text: str) -> str | None:
        n = _norm(text)
        for phrase in self._address:
            if n == phrase or n.startswith(phrase + " "):
                return phrase
        return None

    def _ends_with_amen(self, text: str) -> bool:
        n = _norm(text)
        return any(n == p or n.endswith(" " + p) for p in self._amen)

    def _cancel_pending_amen(self) -> None:
        if self._pending_amen and not self._pending_amen.done():
            self._pending_amen.cancel()
        self._pending_amen = None

    async def _end_prayer_after_silence(self, heard_at: float) -> None:
        # The amen was heard in a final transcript that arrived after the VAD stop; the silence
        # window counts from that stop, not from the transcript's arrival.
        # VAD reports the stop ``vad_stop_secs`` after the last voiced sample; count silence from that sample.
        anchor = (self._vad_stopped_at - self._cfg.vad_stop_secs) if self._vad_stopped_at else heard_at
        remaining = self._cfg.amen_silence_s - (time.time() - anchor)
        if remaining > 0:
            await asyncio.sleep(remaining)
        if self._user_speaking:
            logger.info("floor: amen pending but speech resumed; prayer continues")
            return
        await self.set_mode("available", "amen")

    # ----- scripture pane -----
    async def _interrupt_and_settle(self) -> None:
        """Interrupt and wait until the interruption crossed the pipeline (or 1 s), so frames queued next survive it."""
        self.interruption_seen.clear()
        await self.broadcast_interruption()
        try:
            await asyncio.wait_for(self.interruption_seen.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            logger.warning("floor: interruption not seen by the reading tracker within 1 s")

    async def refresh_instruction(self, reason: str) -> None:
        """Re-send the current node's system instruction with the pane note (the open passage)."""
        if self._flow is None or not self._flow_started:
            return
        from pipecat.frames.frames import LLMUpdateSettingsFrame
        from pipecat.services.settings import LLMSettings

        from talk.flows import role_message

        await self.push_frame(LLMUpdateSettingsFrame(delta=LLMSettings(system_instruction=role_message(self.node or "available", self.reading_state))))
        logger.info(f"floor: system instruction refreshed ({reason}) for {self.reading_state.get('reference')}")

    async def select_passage(self, reference: str, reason: str) -> bool:
        """Open ``reference`` in the pane: pause any reading, load it, tell the client, refresh the LLM."""
        from talk import tools

        reference = (reference or "").strip()
        if not reference:
            return False
        if self.reading_state.get("reading") or self.bot_speaking:
            await self._interrupt_and_settle()
        try:
            result = await tools.fetch_scripture(reference)
        except Exception as exc:  # noqa: BLE001 - the skill service being down is an ordinary condition
            result = {"available": False, "spoken": str(exc)}
        loaded = passage.from_tool_result(result, reference)
        if loaded is None:
            logger.warning(f"floor: passage {reference!r} unavailable: {str(result.get('spoken') or result.get('reason'))[:160]}")
            await self._tell_client({"type": "passage_error", "reference": reference, "reason": result.get("spoken") or result.get("reason") or "unavailable"})
            return False
        if self.reading_state.get("reference") == loaded["reference"] and reason == "client:sync":
            # The phone re-announcing what this session already has (a reconnect): keep the position.
            await self._tell_client(passage.message(self.reading_state))
            return True
        passage.apply(self.reading_state, loaded, source="client")
        self.events.append({"t": time.time(), "passage": loaded["reference"], "reason": reason})
        from pipecat.frames.frames import LLMMessagesAppendFrame

        # The conversation history must show the switch too: a reading of another passage may be the
        # last thing in it, and "verse 2" must now mean this passage's verse 2.
        await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": f"(I opened {loaded['reference']} in the scripture pane.)"}], run_llm=False))
        logger.info(f"floor: passage {loaded['reference']} opened ({reason}), {len(loaded['verses'])} verses")
        await self._tell_client(passage.message(self.reading_state))
        if self.mode == "available" and self.node in {"available", "conversation", "project"}:
            await self.set_node("devotional", "passage")
        await self.refresh_instruction("passage")
        return True

    async def read_active(self, *, restart: bool = False, user_text: str | None = None, reason: str = "", interrupt: bool = True, switch_node: bool = True, indices: list[int] | None = None, announce: str | None = None) -> bool:
        """Read the open passage from where it stopped (or from the start), one verse per unit, in the reading voice.

        Args:
            restart: Read from the first verse (with the reference) even if a position is kept.
            user_text: The utterance that asked for it, appended to the context (no model turn runs).
            reason: For the log.
            interrupt: Stop whatever plays first. Tool handlers pass False: an interruption during a
                function call would cancel the call itself.
            switch_node: Move Flows to ``devotional``. Tool handlers pass False and return the node.
            indices: Read only these verses of the open passage (positions in its verse list).
            announce: Spoken first with ``indices`` (the requested reference, e.g. "Romans 12:2.").
        """
        from pipecat.frames.frames import LLMMessagesAppendFrame

        if not self.reading_state.get("verses"):
            await self._tell_client({"type": "reading", "state": "none", "reference": None, "spoken_count": 0})
            return False
        if self.mode in {"prayer", "quiet", "bug"}:
            await self._tell_client({"type": "reading", "state": "blocked", "reason": self.mode, "reference": self.reading_state.get("reference"), "spoken_count": int(self.reading_state.get("spoken_count") or 0)})
            return False
        if interrupt and (self.reading_state.get("reading") or self.bot_speaking):
            await self._interrupt_and_settle()
        units = passage.read_queue(self.reading_state, restart, indices, announce)
        if indices is None and units and units[0].get("index") is None:
            self.reading_state["spoken_count"] = 0
            self.reading_state["spoken_through"] = 0
        self.reading_state["queue"] = [dict(u) for u in units]
        self.reading_state["reading"] = True
        first = next((u for u in units if u.get("index") is not None), None)
        logger.info(f"floor: reading {self.reading_state.get('reference')} from verse {first and first['verse']} ({reason})")
        await self._tell_client({"type": "reading", "state": "started", "reference": self.reading_state.get("reference"), "from_index": first and first["index"], "from_verse": first and first["verse"], "spoken_count": int(self.reading_state.get("spoken_count") or 0)})
        if user_text:
            await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": user_text}], run_llm=False))
        for unit in units:
            await self.push_frame(ScriptureSpeakFrame(unit["text"]))
        if switch_node and self.node != "devotional":
            await self.set_node("devotional", "reading")
        return True

    async def pause_reading(self, reason: str) -> None:
        """Stop the reading now; the position (verses fully heard) is kept for the next read."""
        if self.reading_state.get("reading") or self.bot_speaking:
            await self._interrupt_and_settle()
        await self.refresh_instruction(f"pause:{reason}")

    async def reading_event(self, message: dict) -> None:
        """Forward a ReadingTracker progress message; a pause or the end refreshes the LLM's view of the pane."""
        await self._tell_client(message)
        if message.get("state") in {"paused", "finished"}:
            await self.refresh_instruction(message["state"])

    # ----- bug reports (the phone's Bug chip) -----
    def _bug_message(self, state: str, **fields) -> dict:
        return {"type": "bug_report", "state": state, "report_id": self.bug.report_id if self.bug else None, "t": time.time(), **fields}

    async def start_bug(self, *, source: dict | None = None, reason: str = "client") -> BugReport:
        """Open a report: silence output, keep transcribing into the report, tell the phone."""
        if self.bug is not None:
            await self._tell_client(self._bug_message("started", segments=len(self.bug.segments), again=True))
            return self.bug
        verses = self.reading_state.get("verses") or []
        report = BugReport(mode_before=self.mode, resume_reading=bool(self.reading_state.get("reading")))
        report.source = {"session_id": self.session_id, **{k: v for k, v in (source or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}}
        messages = []
        if self.context is not None:
            try:
                messages = self.context.get_messages()
            except Exception:  # noqa: BLE001 - context is optional evidence
                messages = []
        report.context = {
            "mode_before": self.mode,
            "node": self.node,
            "bot_speaking": self.bot_speaking,
            "pending_turn": self.pending_turn,
            "passage": {
                "reference": self.reading_state.get("reference"),
                "reading": bool(self.reading_state.get("reading")),
                "spoken_count": int(self.reading_state.get("spoken_count") or 0),
                "verse_count": len(verses),
            },
            "recent_turns": bug_report.recent_turns(messages),
            "recent_floor_events": self.events[-12:],
        }
        self.bug = report
        self.pending_turn = False
        self._cancel_pending_amen()
        self.events.append({"t": time.time(), "bug": "started", "report_id": report.report_id, "reason": reason})
        logger.info(f"floor: bug report {report.report_id} started ({reason}); reading {'interrupted' if report.resume_reading else 'not playing'}")
        await self.set_mode("bug", f"bug:{reason}", switch_node=False)
        await self._tell_client(self._bug_message("started", segments=0))
        return report

    async def bug_typed(self, text: str) -> None:
        """Typed text while reporting is part of the report, never a turn."""
        if self.bug is None or not text.strip():
            return
        segment = self.bug.add(text, "typed")
        await self._tell_client({"type": "bug_text", "text": segment["text"], "final": True, "source": "typed", "index": len(self.bug.segments) - 1})

    async def _bug_transcript(self, frame, direction) -> None:
        text = (frame.text or "").strip()
        if isinstance(frame, TranscriptionFrame):
            if text and self.bug is not None:
                segment = self.bug.add(text, "voice")
                logger.info(f"floor: bug report segment {len(self.bug.segments)}: {text!r}")
                await self._tell_client({"type": "bug_text", "text": segment["text"], "final": True, "source": "voice", "index": len(self.bug.segments) - 1})
            await self._swallow(frame)
            return
        if self.bug is not None:
            self.bug.interim = text
        await self._tell_client({"type": "bug_text", "text": text, "final": False, "source": "voice"})
        await self.push_frame(frame, direction)

    async def _transcription_settled(self, longest_s: float = 6.0) -> None:
        """Wait while the user is still speaking or an utterance that ended has no transcript yet."""
        deadline = time.time() + longest_s
        while time.time() < deadline:
            in_flight = self._vad_stopped_at is not None and self._vad_stopped_at > self._last_final_at and time.time() - self._vad_stopped_at < 4.0
            if not self._user_speaking and not in_flight:
                return
            await asyncio.sleep(0.1)

    async def _leave_bug(self, report: BugReport, reason: str) -> None:
        back = report.mode_before if report.mode_before in {"available", "quiet"} else "available"
        if self.held:
            back = "quiet"
        await self.set_mode(back, f"bug:{reason}", switch_node=report.mode_before == "prayer")

    async def stop_bug(self, *, device_segments: list | None = None, source: dict | None = None, reason: str = "client") -> dict | None:
        """File the open report, return to the mode before, resume an interrupted reading.

        Returns:
            The ``bug_report`` message sent to the phone (state ``filed`` or ``spooled``), or None
            when no report was open.
        """
        report = self.bug
        if report is None or report.finishing:
            return None
        report.finishing = True
        await self._tell_client(self._bug_message("finishing", segments=len(report.segments)))
        await self._transcription_settled()
        if device_segments:
            report.device_segments = [d if isinstance(d, dict) else {"text": str(d)} for d in device_segments][:200]
        if source:
            report.source.update({k: v for k, v in source.items() if isinstance(v, (str, int, float, bool)) or v is None})
        payload = report.payload(ended_by=reason, asr=self._cfg.stt_model)
        self.bug = None
        await self._leave_bug(report, reason)
        await self._tell_client({"type": "bug_report", "state": "filing", "report_id": report.report_id, "segments": len(report.segments), "t": time.time()})
        result = await bug_report.file_report(self._cfg, payload)
        message = {
            "type": "bug_report",
            "state": "filed" if result.get("filed") else "spooled",
            "report_id": report.report_id,
            "ticket_id": result.get("ticket_id"),
            "title": result.get("title"),
            "segments": len(report.segments),
            "chars": len(report.text),
            "error": result.get("error"),
            "t": time.time(),
        }
        self.events.append({"t": time.time(), "bug": message["state"], "report_id": report.report_id, "ticket_id": message["ticket_id"]})
        logger.info(f"floor: bug report {report.report_id} {message['state']} as {message['ticket_id']} ({len(report.segments)} segments)")
        await self._tell_client(message)
        if result.get("filed"):
            from pipecat.frames.frames import LLMMessagesAppendFrame

            await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": f"(I filed a bug report, {message['ticket_id']}, with the intake issues log.)"}], run_llm=False))
            self._spawn(self._report_triage(message["ticket_id"], report.report_id))
        if report.resume_reading and self.mode == "available" and self.reading_state.get("verses"):
            await self.read_active(reason="after bug report")
        return message

    async def cancel_bug(self, reason: str = "client") -> None:
        """Drop the open report unfiled and return to the mode before."""
        report = self.bug
        if report is None:
            return
        self.bug = None
        self.events.append({"t": time.time(), "bug": "cancelled", "report_id": report.report_id})
        logger.info(f"floor: bug report {report.report_id} cancelled ({len(report.segments)} segments dropped)")
        await self._leave_bug(report, reason)
        await self._tell_client({"type": "bug_report", "state": "cancelled", "report_id": report.report_id, "segments": len(report.segments), "t": time.time()})
        if report.resume_reading and self.mode == "available" and self.reading_state.get("verses"):
            await self.read_active(reason="after cancelled bug report")

    async def abandon_bug(self) -> dict | None:
        """The session ended with a report open: file what was heard (no phone to tell)."""
        report = self.bug
        if report is None:
            return None
        self.bug = None
        result = await bug_report.file_report(self._cfg, report.payload(ended_by="disconnect", asr=self._cfg.stt_model))
        logger.info(f"floor: bug report {report.report_id} filed at session end: {result.get('ticket_id') or result.get('spooled')}")
        return result

    async def client_bug(self, message: dict) -> None:
        """The phone's ``client_bug`` message: ``action`` start | stop | cancel."""
        action = message.get("action")
        # The app sends flat string fields (the WebRTC data channel carries a string map): app_build,
        # transport, audio_route, install_id, client; and on stop device_segments, one line each.
        source = dict(message.get("context")) if isinstance(message.get("context"), dict) else {}
        source.update({k: message[k] for k in ("app_build", "transport", "audio_route", "install_id", "client") if k in message})
        device = message.get("device_segments")
        if isinstance(device, str):
            device = [{"text": line} for line in device.split("\n") if line.strip()]
        if action == "start":
            await self.start_bug(source=source, reason="client")
        elif action == "stop":
            # Filing waits for the utterance in flight and for the skill service; it runs as its own
            # task so the processor that delivered the message keeps passing audio meanwhile.
            self._spawn(self._stop_from_client(device or None, source or None))
        elif action == "cancel":
            await self.cancel_bug("client")

    async def _stop_from_client(self, device_segments: list | None, source: dict | None) -> None:
        if await self.stop_bug(device_segments=device_segments, source=source, reason="client") is None:
            await self._tell_client({"type": "bug_report", "state": "none", "report_id": None, "t": time.time()})

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._bug_tasks.add(task)
        task.add_done_callback(self._bug_tasks.discard)

    async def _report_triage(self, ticket_id: str, report_id: str) -> None:
        """Tell the phone the triaged title, kind and priority once the skill service has them."""
        ticket = await bug_report.wait_for_triage(self._cfg, ticket_id)
        if not ticket:
            return
        triage = ticket.get("triage") or {}
        try:
            await self._tell_client({"type": "bug_report", "state": "triaged", "report_id": report_id, "ticket_id": ticket_id, **{k: triage.get(k) for k in ("title", "kind", "component", "severity", "priority", "disposition")}, "t": time.time()})
        except Exception as exc:  # noqa: BLE001 - the session may have ended; the log has the triage
            logger.info(f"floor: triage of {ticket_id} not sent to the phone: {exc}")

    # ----- frames -----
    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame) and self._flow is not None and not self._flow_started:
            # Flows queues its node frames at the pipeline source; before the task runs they are lost.
            self._flow_started = True
            from talk.flows import node as flow_node

            await self._flow.initialize(flow_node("available", reading_state=self.reading_state))
            await self.announce_node("start")

        if isinstance(frame, BotStartedSpeakingFrame):
            self.bot_speaking = True
            if self.node == "available":
                await self.set_node("conversation", "first reply")
            self.pending_turn = False
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            if self.bot_speaking:
                self.bot_stopped_at = time.time()
            self.bot_speaking = False
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            if self.mode == "prayer":
                self._cancel_pending_amen()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
            self._vad_stopped_at = time.time()

        if isinstance(frame, InputTransportMessageFrame):
            message = frame.message or {}
            kind = message.get("type")
            if kind == "client_mode":
                if message.get("reason") == "sync":
                    # The app syncs on every connect; tell it who answers so it can label replies.
                    await self._tell_client({"type": "assistant", "name": self.assistant_name})
                mode = message.get("mode")
                if mode in {"prayer", "available", "quiet"}:
                    await self.set_mode(mode, f"client:{message.get('reason', 'request')}")
                return
            if kind == "client_event":
                self.events.append({"t": time.time(), "client": message})
                return
            if kind == "client_bug":
                await self.client_bug(message)
                return
            if kind == "client_text":
                # Typed input from the app or the text harness: it becomes a user turn like a final transcript.
                text = str(message.get("text") or "").strip()
                if text:
                    from pipecat.utils.time import time_now_iso8601

                    await self.process_frame(TranscriptionFrame(text, "client", time_now_iso8601()), direction)
                return

        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            text = frame.text or ""
            final = isinstance(frame, TranscriptionFrame)
            if final:
                self._last_final_at = time.time()
            n = _norm(text)
            if self.mode == "bug":
                # Reporting a bug: every word goes into the report, none to the LLM; no phrase acts.
                await self._bug_transcript(frame, direction)
                return
            if self.mode == "prayer":
                if final and self._ends_with_amen(text):
                    logger.info(f"floor: amen heard in {text!r}")
                    self._cancel_pending_amen()
                    self._pending_amen = asyncio.create_task(self._end_prayer_after_silence(time.time()))
                # Prayer text never reaches the LLM: it is addressed to God, journaled by the app.
                await self._tell_client({"type": "prayer_text", "text": text, "final": final})
                await self._swallow(frame)
                return
            if final and any(n == p or n.endswith(" " + p) for p in self._quiet):
                await self.set_mode("quiet", "phrase")
                await self._swallow(frame)
                return
            if final and self.mode == "quiet" and any(n == p or n.startswith(p) for p in self._wake):
                if self.held:
                    logger.info("floor: wake phrase ignored while held")
                    await self._swallow(frame)
                    return
                await self.set_mode("available", "wake")
                await self._swallow(frame)
                return
            address = self._starts_with_address(text)
            if address and self.mode != "prayer":
                await self.set_mode("prayer", f"address:{address}")
                await self._tell_client({"type": "prayer_text", "text": text, "final": final})
                await self._swallow(frame)
                return
            if self.mode == "quiet" and final:
                # Heard, journaled by the app, not answered.
                await self._swallow(frame)
                return
            if final and n and (n in self._backchannel or (len(n.split()) <= 2 and all(w in self._backchannel for w in n.split()))):
                # "mm-hmm", "yeah", "okay": an acknowledgement, never a turn.
                logger.info(f"floor: backchannel transcript dropped: {text!r}")
                self.events.append({"t": time.time(), "backchannel_dropped": text})
                await self._swallow(frame)
                return
            lead = next((p for p in sorted(self._pause, key=len, reverse=True) if n == p or n.startswith(p + " ")), None) if final else None
            if lead is not None:
                # "hold on", "wait", "stop": stop speaking. Alone (or stacked: "Wait. Stop there."), it is
                # never a question for the LLM; followed by more words ("Hold on. What are the green
                # pastures about?") the rest is the turn.
                remainder = n[len(lead):].strip()
                while remainder:
                    more = next((p for p in sorted(self._pause, key=len, reverse=True) if remainder == p or remainder.startswith(p + " ")), None)
                    if more is None:
                        break
                    remainder = remainder[len(more):].strip()
                logger.info(f"floor: pause phrase {lead!r} in {text!r}; interrupting" + ("; waiting" if not remainder else f"; forwarding {remainder!r}"))
                self.events.append({"t": time.time(), "pause_phrase": lead, "remainder": remainder})
                if self.bot_speaking:
                    await self.broadcast_interruption()
                if not remainder or len(remainder.split()) < 2:
                    await self._swallow(frame)
                    return
                frame.text = text[len(text) - len(remainder) :] if text.lower().endswith(remainder) else remainder
                text = frame.text
                n = _norm(text)
            request = passage.read_request(passage.norm(text)) if final else None
            if request is None and final and any(p in n for p in self._continue) and len(n.split()) <= 10:
                request = "resume"
            if request is not None and self.reading_state.get("verses"):
                # "Read it", "continue", "read this again": read the open passage without a model turn.
                logger.info(f"floor: read request {request!r} in {text!r} for {self.reading_state.get('reference')}")
                self.events.append({"t": time.time(), "read_request": request, "text": text})
                await self.read_active(restart=request == "restart", user_text=text, reason="phrase")
                await self._swallow(frame)
                return
            if final and self.bot_speaking:
                # A short utterance that outlasted nothing but is not a backchannel is an interruption
                # decided by its words rather than its length.
                logger.info(f"floor: words interrupt the bot: {text!r}")
                self.events.append({"t": time.time(), "word_interrupt": text})
                await self.broadcast_interruption()
            if final:
                self.pending_turn = True
                self.reading_state["last_user_text"] = text
                await self.announce_node("turn")

        await self.push_frame(frame, direction)


class OutputGate(FrameProcessor):
    """Drops every spoken output while the floor is in prayer, quiet or bug; evaluated per frame."""

    def __init__(self, floor: FloorProcessor):
        super().__init__(name="output-gate")
        self._floor = floor

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and self._floor.mode in {"prayer", "quiet", "bug"}:
            if isinstance(frame, (TTSAudioRawFrame, TTSTextFrame, TTSSpeakFrame, LLMTextFrame, TTSStartedFrame, TTSStoppedFrame)):
                return
        await self.push_frame(frame, direction)
