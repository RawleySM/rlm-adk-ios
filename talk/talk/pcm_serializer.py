"""A small binary framing for our own WebSocket clients (the iPhone app's PCM path, the test probe).

Binary frames: one tag byte followed by the payload.
    0x01  int16 little-endian mono PCM. Client to server at the transport's input rate (16 kHz);
          server to client at the output rate (24 kHz for Kokoro).
Text frames: UTF-8 JSON objects. Server to client: ``{"type": ...}`` transport messages (RTVI
messages pass through unchanged, so the app sees bot transcripts, speaking state and our own
``mode`` messages); ``{"type": "interruption"}`` tells the client to flush its playback queue.
Client to server: ``{"type": "client_mode", "mode": "prayer"}`` and friends, delivered to the
pipeline as ``InputTransportMessageFrame``.
"""

from __future__ import annotations

import json

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

AUDIO_TAG = b"\x01"


class PCMFrameSerializer(FrameSerializer):
    """Tagged raw PCM plus JSON control messages."""

    def __init__(self, *, in_sample_rate: int = 16000, params: FrameSerializer.InputParams | None = None):
        super().__init__(params)
        self._params.ignore_rtvi_messages = False
        self._in_sample_rate = in_sample_rate

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return AUDIO_TAG + frame.audio
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message)
        if isinstance(frame, InterruptionFrame):
            return json.dumps({"type": "interruption"})
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, (bytes, bytearray)):
            if not data:
                return None
            if data[:1] == AUDIO_TAG:
                return InputAudioRawFrame(audio=bytes(data[1:]), sample_rate=self._in_sample_rate, num_channels=1)
            logger.warning(f"pcm serializer: unknown binary tag {data[:1]!r}")
            return None
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            logger.warning(f"pcm serializer: undecodable text frame {data[:80]!r}")
            return None
        return InputTransportMessageFrame(message=message)
