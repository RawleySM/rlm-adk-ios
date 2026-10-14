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
import uuid
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
from talk.bridge import BridgeProcessor  # noqa: E402
from talk.client_messages import ClientMessages  # noqa: E402
from talk.cohere_stt import CohereSparkSTTService  # noqa: E402
from talk.config import TalkConfig, load_config  # noqa: E402
from talk.floor import FloorProcessor, OutputGate  # noqa: E402
from talk.pcm_serializer import PCMFrameSerializer  # noqa: E402
from talk.reading_tracker import ReadingTracker  # noqa: E402
from talk.session_log import SessionLog  # noqa: E402
from talk.tools import register_tools, tools_schema  # noqa: E402

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
interpretation and his own prayers clearly distinguished when you speak."""


def _transport_params(cfg: TalkConfig) -> TransportParams:
    return TransportParams(
        audio_in_enabled=True,
        audio_in_sample_rate=cfg.audio_in_sample_rate,
        audio_out_enabled=True,
        audio_out_sample_rate=cfg.audio_out_sample_rate,
    )


async def run_bot(transport, cfg: TalkConfig, *, session_label: str) -> None:
    """Assemble and run one session's pipeline on the given transport."""

    stt = CohereSparkSTTService(base_url=cfg.stt_base_url, model=cfg.stt_model, sample_rate=cfg.audio_in_sample_rate)
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
    register_tools(llm, cfg)
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
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())],
            ),
        ),
    )
    floor = FloorProcessor(cfg)
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
            ReadingTracker(),
            aggregators.assistant(),
        ]
    )
    session_id = f"{session_label}-{uuid.uuid4().hex[:10]}"
    session_log = SessionLog(cfg.root, session_id)
    logger.info(f"session {session_id}: log {session_log.path}")
    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[RTVIObserver(rtvi), session_log],
        idle_timeout_secs=None,
    )
    floor.bind(task)

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
        session_log.close()


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
