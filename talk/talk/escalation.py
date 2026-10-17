"""Automatic escalation: a request the assistant could not meet goes to Intake by itself.

Rawley's direction (2026-10-17): the voice assistant exists to answer quickly; when he asks for
something it cannot do, the request must go to the Intake resident automatically and be tracked as
a fix, not end in "I can't". Two layers make that happen:

* The model is told to call ``send_to_intake`` itself whenever a request needs something its tools
  cannot do (``talk.bot.SYSTEM_PROMPT``, the tool's description). That is the normal path.
* ``EscalationWatch`` (this module) is the deterministic safety net behind the model. It sits
  between the LLM and the TTS, keeps the text of each assistant response, and when a response ends
  whose words say the request cannot be met (``CANNOT_PATTERNS``) with no tool call in that user
  turn, it files Rawley's verbatim last words through the same path ``send_to_intake`` uses
  (``bug_report.file_report`` to ``POST /tools/work``, origin ``auto_escalation``), speaks one short
  line (``ESCALATED_SPOKEN``) and appends a note to the context so "what did you send?" can be
  answered. Filing is fire-and-forget: the conversation is never held for it, and a request the
  desktop cannot take is spooled and retried like a bug report.

What is never escalated: a response in prayer, quiet or bug mode (nothing is answered there), a
response inside a tool turn (the model relaying a tool result, such as a verse that was not found,
or a ``send_to_intake`` the model already made), an interrupted response, text inside quotation
marks (Scripture quoted aloud), and a second response of the same user turn once one was filed.
``TalkConfig.auto_escalate`` switches the safety net off.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from talk.config import TalkConfig

# Spoken once per escalated turn, after the model's own reply, through the floor's output gate.
ESCALATED_SPOKEN = "I've passed that to Intake; it will reply on your phone."

# Conservative: each pattern is the assistant saying, in the first person, that it cannot meet the
# request. Apostrophes are normalised before matching, quoted spans removed (``cannot_meet``).
CANNOT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bI can't\b",
        r"\bI cannot\b",
        r"\bI(?:'m| am) unable\b",
        r"\bI(?:'m| am) not able to\b",
        r"\bI won't be able to\b",
        r"\bI don't have (?:access|a way|any way|the ability|that ability|a tool|any tool|the tools|that capability|such a capability|that feature|that option)\b",
        r"\bI do not have (?:access|a way|any way|the ability|that ability|a tool|any tool|the tools|that capability|such a capability|that feature|that option)\b",
        r"\bI have no way to\b",
        r"\bisn't something I can\b",
        r"\bis not something I can\b",
        r"\bnot (?:something|anything) I(?:'m| am) able to\b",
        r"\bbeyond what I can\b",
        r"\boutside (?:of )?what I can\b",
        r"\bI(?:'m| am) not (?:set up|equipped|built|designed) to\b",
    )
)

_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|‘[^’]*’")
_QUESTION_LEAD = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|is there|are there|do you know|can you tell me|could you tell me)\b", re.I)


def _normalise(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'").replace("`", "'")


def cannot_meet(text: str) -> str | None:
    """The first ``CANNOT_PATTERNS`` phrase in ``text`` (quoted spans ignored), or None.

    Args:
        text: An assistant response, as spoken.

    Returns:
        The matched phrase (for the log and the ticket), or None when the response does not say the
        request cannot be met.
    """
    plain = _QUOTED.sub(" ", _normalise(text))
    for pattern in CANNOT_PATTERNS:
        match = pattern.search(plain)
        if match:
            return match.group(0)
    return None


def is_question(words: str) -> bool:
    """True when Rawley's words are a question (end with ``?`` or open with a question word).

    "Can you add ..." is a request, not a question, so auxiliaries alone do not count.
    """
    text = _normalise(words).strip()
    return text.endswith("?") or bool(_QUESTION_LEAD.match(text))


def kind_hint_for(words: str) -> str:
    """The ticket kind hint for an auto-escalated request: ``message`` for a question, else
    ``feature_request`` (a missing capability or a change); triage decides the final kind."""
    return "message" if is_question(words) else "feature_request"


class EscalationWatch(FrameProcessor):
    """The safety net: files a request the assistant said it could not meet (see the module docstring).

    Placed after the LLM (and the bridge) and before the TTS, so it sees the model's text and
    function calls as they stream and its spoken line and context note travel the normal path
    through the output gate and the assistant aggregator.
    """

    def __init__(self, cfg: TalkConfig, floor):
        super().__init__(name="escalation-watch")
        self._cfg = cfg
        self._floor = floor
        self._text: list[str] = []
        self._in_response = False
        # The floor's turn serial a tool call, an interruption or a filing was last seen in.
        self._tool_serial: int | None = None
        self._tool_names: set[str] = set()
        self._interrupted_serial: int | None = None
        self._filed_serial: int | None = None
        self._tasks: set[asyncio.Task] = set()
        # What was escalated (tests, the session log): ``{t, serial, phrase, words, kind, ticket_id|spooled}``.
        self.escalations: list[dict[str, Any]] = []

    @property
    def _serial(self) -> int:
        return int(getattr(self._floor, "turn_serial", 0) or 0)

    def _skip_reason(self, text: str) -> str | None:
        """Why this response is not escalated, or None when it is."""
        if not self._cfg.auto_escalate:
            return "off"
        serial = self._serial
        if self._floor.mode in {"prayer", "quiet", "bug"} or getattr(self._floor, "held", False):
            return f"mode:{self._floor.mode}"
        if self._filed_serial == serial:
            return "already_filed"
        if self._interrupted_serial == serial:
            return "interrupted"
        if self._tool_serial == serial:
            return "sent_to_intake" if "send_to_intake" in self._tool_names else f"tool_turn:{','.join(sorted(self._tool_names)) or '?'}"
        if cannot_meet(text) is None:
            return "met"
        return None

    def _words(self) -> str:
        from talk import tools

        messages: list[dict[str, Any]] = []
        context = getattr(self._floor, "context", None)
        if context is not None:
            try:
                messages = context.get_messages()
            except Exception:  # noqa: BLE001 - the floor's last forwarded utterance is the fallback
                messages = []
        fallback = str((getattr(self._floor, "reading_state", None) or {}).get("last_user_text") or "")
        return tools.intake_words(messages, fallback=fallback)

    async def _response_ended(self) -> None:
        text = "".join(self._text).strip()
        self._text = []
        self._in_response = False
        if not text:
            return
        reason = self._skip_reason(text)
        if reason is not None:
            if reason != "met":
                logger.info(f"escalation: not filed ({reason}): {text[:80]!r}")
            return
        # NOTE [Context]: the words are read now, before the assistant aggregator (downstream of
        # this processor) records the reply: ``intake_words`` collects the user messages since the
        # assistant last spoke, which at this moment are exactly this turn's.
        words = self._words()
        if not words:
            logger.info("escalation: nothing to file (no user words in the context)")
            return
        serial = self._serial
        self._filed_serial = serial
        phrase = cannot_meet(text) or ""
        kind = kind_hint_for(words)
        logger.info(f"escalation: {phrase!r} in reply; filing {kind} for turn {serial}: {words[:120]!r}")
        await self.push_frame(TTSSpeakFrame(ESCALATED_SPOKEN))
        task = asyncio.get_running_loop().create_task(self._file(words, kind, text, phrase, serial))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _file(self, words: str, kind: str, reply: str, phrase: str, serial: int) -> None:
        from talk import bug_report, tools

        record: dict[str, Any] = {"t": time.time(), "serial": serial, "phrase": phrase, "words": words, "kind": kind}
        try:
            payload = tools.intake_report(words, kind, self._floor, origin="auto_escalation", assistant_reply=reply, ended_by="auto_escalation")
            result = await bug_report.file_report(self._cfg, payload, route="/tools/work")
        except Exception as exc:  # noqa: BLE001 - the conversation goes on; the failure is logged
            logger.warning(f"escalation: filing failed: {exc}")
            record["error"] = str(exc)
            self.escalations.append(record)
            return
        ticket_id = result.get("ticket_id")
        record.update({"ticket_id": ticket_id, "spooled": result.get("spooled"), "filed": bool(result.get("filed"))})
        self.escalations.append(record)
        if ticket_id:
            note = f"(The host passed that request to Intake as ticket {ticket_id}; Intake will reply on the phone.)"
        else:
            note = "(The host passed that request to Intake; the desktop is not answering, so it goes when it is back, and Intake will reply on the phone.)"
        logger.info(f"escalation: {'filed ' + str(ticket_id) if ticket_id else 'spooled'} for turn {serial}")
        try:
            await self.push_frame(LLMMessagesAppendFrame(messages=[{"role": "user", "content": note}], run_llm=False))
        except Exception as exc:  # noqa: BLE001 - the session may have ended; the ticket is filed
            logger.info(f"escalation: context note not added: {exc}")

    def _note_tool(self, frame: Frame) -> None:
        """Remember that this user turn ran a tool (and which): its responses are never escalated."""
        serial = self._serial
        if self._tool_serial != serial:
            self._tool_names = set()
        self._tool_serial = serial
        names = [getattr(frame, "function_name", None)]
        if isinstance(frame, FunctionCallsStartedFrame):
            names = [getattr(call, "function_name", None) for call in (getattr(frame, "function_calls", None) or [])]
        self._tool_names.update(str(n) for n in names if n)

    def note_interruption(self) -> None:
        """An interruption crossed the pipeline: the response in flight is dropped and nothing of this
        user turn is escalated (what was said was cut off, not refused)."""
        self._interrupted_serial = self._serial
        self._text = []
        self._in_response = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, (FunctionCallsStartedFrame, FunctionCallInProgressFrame, FunctionCallResultFrame)):
            # A tool turn: whatever the model says next is about the tool's result, never a gap.
            self._note_tool(frame)
        elif isinstance(frame, InterruptionFrame):
            self.note_interruption()
        elif direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, LLMFullResponseStartFrame):
                self._text = []
                self._in_response = True
            elif isinstance(frame, LLMTextFrame) and self._in_response:
                self._text.append(frame.text or "")
            elif isinstance(frame, LLMFullResponseEndFrame):
                # The end frame goes first so the TTS flushes the reply's last sentence before the line.
                await self.push_frame(frame, direction)
                await self._response_ended()
                return
        await self.push_frame(frame, direction)
