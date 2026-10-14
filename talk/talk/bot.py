"""The talk-tier pipeline: transport → VAD/turns → Cohere STT → Qwen → Kokoro → transport.

Run with the Pipecat development runner, which serves ``POST /start``,
``/sessions/{id}/api/offer`` (SmallWebRTC, the iPhone's Pipecat client) and ``/ws-client`` (our
tagged-PCM WebSocket, the iPhone's fallback path and the benchmark probe)::

    .venv/bin/python -m talk.bot --host 0.0.0.0 --port 7860            # all transports
    .venv/bin/python -m talk.bot -t websocket --host 0.0.0.0 --port 7870

Tool calls reach the skill service over HTTP (``talk.tools``); the pipeline never waits on them in
the audio path because Pipecat runs function calls asynchronously and keeps streaming.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams  # noqa: E402
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3  # noqa: E402
from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: E402
from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: E402
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
from pipecat.pipeline.task import PipelineParams, PipelineTask  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi.observer import RTVIObserver  # noqa: E402
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor  # noqa: E402
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments, WebSocketRunnerArguments  # noqa: E402
from pipecat.services.kokoro.tts import KokoroTTSService  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402
from pipecat.transports.base_transport import TransportParams  # noqa: E402
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter  # noqa: E402
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy  # noqa: E402
from pipecat.turns.user_turn_strategies import UserTurnStrategies  # noqa: E402

from talk.backchannel import BackchannelAwareStartStrategy, DuckProcessor  # noqa: E402
from talk.bridge import BridgeProcessor, ContextScrubber  # noqa: E402
from talk.client_messages import ClientMessages  # noqa: E402
from talk.cohere_stt import CohereSparkSTTService  # noqa: E402
from talk.config import TalkConfig, load_config  # noqa: E402
from talk.floor import FloorProcessor, OutputGate  # noqa: E402
from talk.pcm_serializer import PCMFrameSerializer  # noqa: E402
from talk.reading_tracker import ReadingTracker  # noqa: E402
from talk.session_log import SessionLog  # noqa: E402
from talk.tools import new_reading_state, register_tools, tools_schema  # noqa: E402

SYSTEM_PROMPT = """You are the voice of Rawley's personal Scripture and prayer portal. You speak out loud through
a text-to-speech voice, so answer in short plain spoken sentences: no lists, no markdown, no asterisks,
no headings, no bracketed citations, no emoji. Two to four sentences unless he asks you to read Scripture, in which case read the
retained text exactly as the tool returns it. Use the tools for anything about his prayers, projects,
Scripture text, teaching or work requests; never invent a verse, a date, a project or a result. When a
tool reports that something is accepted but not done, say exactly that. The tools read_scripture,
continue_reading, word_from_prayer and request_work speak their result themselves; after calling one of
them say nothing more unless Rawley asks a new question. Use read_scripture only when he asks to hear a
passage read ("read me", "read Psalm 23"); for a question about what a passage says on a topic ("what does
Romans 8 say about suffering") call search_sources with family scripture (or read_scripture on the specific
verses, such as "Romans 8:18" or "Romans 8:35-39"), then answer briefly in your own words, quoting a verse or two. When a tool result carries a "spoken" field, say
that text as given, then add at most one sentence of your own. A one-word acknowledgement from Rawley such as "mm-hmm", "yeah" or
"okay" is not a request: do not answer it and do not restart anything. Keep Scripture, a teacher's
interpretation and his own prayers clearly distinguished when you speak.
Never reply with only a filler such as "One moment" or "Let me look"; the host speaks those while you work. Every reply of yours either calls the tool the request needs or gives the answer.
"""


def _transport_params(cfg: TalkConfig) -> TransportParams:
    return TransportParams(
        audio_in_enabled=True,
        audio_in_sample_rate=cfg.audio_in_sample_rate,
        audio_out_enabled=True,
        audio_out_sample_rate=cfg.audio_out_sample_rate,
    )


async def run_bot(transport, cfg: TalkConfig, *, session_label: str) -> None:
    """Assemble and run one session's pipeline on the given transport."""

    stt = CohereSparkSTTService(base_url=cfg.stt_base_url, model=cfg.stt_model, sample_rate=cfg.audio_in_sample_rate, min_rms=cfg.stt_min_rms)
    llm = OpenAILLMService(
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        system_instruction=SYSTEM_PROMPT,
        settings=OpenAILLMService.Settings(
            model=cfg.llm_model,
            temperature=cfg.llm_temperature,
            max_completion_tokens=cfg.llm_max_tokens,
            # The OpenAI SDK only forwards unknown fields through extra_body; the owner proxies them.
            extra={"extra_body": dict(cfg.llm_extra_body)},
        ),
    )
    tts = KokoroTTSService(
        model_path=cfg.kokoro_model_path,
        voices_path=cfg.kokoro_voices_path,
        settings=KokoroTTSService.Settings(voice=cfg.tts_voice, speed=cfg.tts_speed),
        sample_rate=cfg.audio_out_sample_rate,
        text_filters=[MarkdownTextFilter()],
    )
    reading_state = new_reading_state()
    register_tools(llm, cfg, reading_state)
    context = LLMContext(messages=[], tools=tools_schema())
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=cfg.vad_stop_secs)),
            # Cohere answers per utterance, about a second warm; a cold owner takes far longer and an
            # empty user turn must never reach the LLM (the owner rejects it and Pipecat retires the service).
            user_turn_stop_timeout=15.0,
            empty_user_turn=None,
            user_turn_strategies=UserTurnStrategies(
                start=[BackchannelAwareStartStrategy(cfg)],
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=cfg.smart_turn_stop_secs)))],
            ),
        ),
    )
    session_id = f"{session_label}-{uuid.uuid4().hex[:10]}"
    floor = FloorProcessor(cfg, reading_state, session_id=session_id)
    gate = OutputGate(floor)
    bridge = BridgeProcessor(cfg, floor)
    duck = DuckProcessor(cfg)
    rtvi = RTVIProcessor()
    pipeline = Pipeline(
        [
            transport.input(),
            ClientMessages(floor),
            rtvi,
            stt,
            floor,
            aggregators.user(),
            llm,
            bridge,
            tts,
            duck,
            gate,
            transport.output(),
            # After the output transport, TTSTextFrame arrives as the audio is played out, not as
            # it is synthesised, so the tracker counts verses the listener actually heard.
            ReadingTracker(reading_state),
            ContextScrubber(cfg),
            aggregators.assistant(),
        ]
    )
    session_log = SessionLog(cfg.root, session_id)
    logger.info(f"session {session_id}: log {session_log.path}")
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[RTVIObserver(rtvi), session_log],
        idle_timeout_secs=None,
    )
    floor.bind(task, rtvi)
    LIVE_FLOORS[session_id] = floor
    if HOLD["active"] and HOLD.get("hold_new"):
        floor.held = True
        floor.mode = "quiet"

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"[{session_label}] client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"[{session_label}] client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    finally:
        LIVE_FLOORS.pop(session_id, None)
        session_log.close()



# ----- operator admin: hold every live session quiet (room benchmarks share the air with the phone) -----
# NOTE [Context]: the Pipecat runner's FastAPI app is a module global; routes added here are served
# alongside /start and /ws-client. ``LIVE_FLOORS`` maps session id -> FloorProcessor for the sessions
# running in this process. A hold sets each to quiet with ``held`` so the wake phrase cannot lift it;
# ``hold_new`` also holds sessions that connect later (a phone reconnecting mid-run), so a benchmark
# client that must stay awake connects before calling hold, or passes ``hold_new=false``.
LIVE_FLOORS: dict[str, FloorProcessor] = {}
HOLD: dict[str, Any] = {"active": False, "reason": "", "hold_new": False, "since": None}

try:
    from pipecat.runner.run import app as _runner_app
except Exception:  # noqa: BLE001 - admin routes are optional outside the runner
    _runner_app = None

if _runner_app is not None:

    @_runner_app.get("/admin/sessions")
    async def admin_sessions() -> dict[str, Any]:
        return {
            "hold": HOLD,
            "sessions": [{"session_id": sid, "mode": fl.mode, "held": fl.held, "bot_speaking": fl.bot_speaking} for sid, fl in LIVE_FLOORS.items()],
        }

    @_runner_app.post("/admin/hold")
    async def admin_hold(body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        only = set(body.get("sessions") or [])
        except_ids = set(body.get("except") or [])
        HOLD.update({"active": True, "reason": str(body.get("reason") or "operator"), "hold_new": bool(body.get("hold_new", False)), "since": time.time()})
        held = []
        for sid, fl in list(LIVE_FLOORS.items()):
            if (only and sid not in only) or sid in except_ids:
                continue
            fl.held = True
            await fl.set_mode("quiet", f"hold:{HOLD['reason']}")
            held.append(sid)
        return {"held": held, "hold": HOLD}

    @_runner_app.post("/admin/release")
    async def admin_release(body: dict[str, Any] | None = None) -> dict[str, Any]:
        HOLD.update({"active": False, "reason": "", "hold_new": False, "since": None})
        released = []
        for sid, fl in list(LIVE_FLOORS.items()):
            if fl.held:
                fl.held = False
                if fl.mode == "quiet":
                    await fl.set_mode("available", "release")
                released.append(sid)
        return {"released": released}

    @_runner_app.post("/admin/sessions/{session_id}/mode")
    async def admin_session_mode(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        fl = LIVE_FLOORS.get(session_id)
        if fl is None:
            return {"error": "no such session", "sessions": list(LIVE_FLOORS)}
        mode = str(body.get("mode") or "available")
        fl.held = bool(body.get("held", fl.held)) if mode == "quiet" else False
        await fl.set_mode(mode, f"admin:{body.get('reason') or 'operator'}")
        return {"session_id": session_id, "mode": fl.mode, "held": fl.held}


async def bot(runner_args: RunnerArguments) -> None:
    """Runner entry point: one call per session."""

    cfg = load_config()
    if isinstance(runner_args, SmallWebRTCRunnerArguments):
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

        transport = SmallWebRTCTransport(params=_transport_params(cfg), webrtc_connection=runner_args.webrtc_connection)
        await run_bot(transport, cfg, session_label="webrtc")
    elif isinstance(runner_args, WebSocketRunnerArguments):
        from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

        params = FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_in_sample_rate=cfg.audio_in_sample_rate,
            audio_out_enabled=True,
            audio_out_sample_rate=cfg.audio_out_sample_rate,
            serializer=PCMFrameSerializer(in_sample_rate=cfg.audio_in_sample_rate),
            session_timeout=None,
        )
        transport = FastAPIWebsocketTransport(websocket=runner_args.websocket, params=params)
        await run_bot(transport, cfg, session_label="websocket")
    else:
        logger.error(f"unsupported runner arguments: {type(runner_args).__name__}")


if __name__ == "__main__":
    from pipecat.runner.run import main

    os.environ.setdefault("PIPECAT_LOG_LEVEL", "INFO")
    main()
