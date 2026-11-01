"""Text-to-speech. Default: Fish Audio's cloud API (needs FISH_API_KEY). Optional: local Piper.
Every speaker exposes discord_pcm(text) -> 48 kHz stereo s16le for the sidecar's player.

Only the reply text is sent to Fish Audio; reasoning and speech recognition stay local."""

import io
import json
import re
import urllib.error
import urllib.request
import wave

import numpy as np

from voice.audio import pcm16_to_float, to_discord_pcm
from voice.text import speakable

FISH_TTS_URL = "https://api.fish.audio/v1/tts"


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = pcm16_to_float(wav.readframes(wav.getnframes()))
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


class TTSError(RuntimeError):
    pass


class FishSpeaker:
    """Fish Audio TTS. `voice_id` is a voice model's reference id from fish.audio (empty = default voice)."""

    def __init__(self, api_key: str, voice_id: str | None = None, model: str = "s2.1-pro-free",
                 latency: str = "normal", timeout: float = 30.0, url: str = FISH_TTS_URL,
                 expression_engine=None) -> None:
        if not api_key:
            raise TTSError("FISH_API_KEY is not set")
        self.api_key, self.voice_id, self.model = api_key, voice_id, model
        self.latency, self.timeout, self.url = latency, timeout, url
        self.expression_engine = expression_engine
        self.last_plan = None

    def _request(self, text: str, prosody: dict | None = None) -> bytes:
        body = {"text": text, "format": "wav", "latency": self.latency, "normalize": True}
        if self.voice_id:
            body["reference_id"] = self.voice_id
        if prosody is not None:
            body["prosody"] = prosody
        request = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                     "model": self.model},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise TTSError(f"Fish Audio returned {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TTSError(f"Could not reach Fish Audio: {exc.reason}") from exc

    def synthesize(self, text: str, *, state=None, context="", intent=None) -> tuple[np.ndarray, int]:
        if self.expression_engine is None:
            return read_wav(self._request(speakable(text)))
        plan = self.expression_engine.plan(text, state, context, intent)
        self.last_plan = plan
        directed = plan.render(self.model)
        return read_wav(self._request(directed, {"speed": plan.speed, "volume": plan.volume,
                                               "normalize_loudness": True}))

    def discord_pcm(self, text: str) -> bytes:
        samples, rate = self.synthesize(text)
        return to_discord_pcm(samples, rate)

    def discord_pcm_with_state(self, text: str, state: dict, context="", intent=None) -> bytes:
        samples, rate = self.synthesize(text, state=state, context=context, intent=intent)
        return to_discord_pcm(samples, rate)


class PiperSpeaker:
    """Local fallback (TTS_ENGINE=piper)."""

    def __init__(self, model_path: str) -> None:
        from piper import PiperVoice  # imported lazily: optional dependency
        self.voice = PiperVoice.load(model_path)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            if hasattr(self.voice, "synthesize_wav"):      # piper-tts >= 1.3
                self.voice.synthesize_wav(speakable(text), wav)
            else:                                           # older piper-tts
                self.voice.synthesize(speakable(text), wav)
        return read_wav(buffer.getvalue())

    def discord_pcm(self, text: str) -> bytes:
        samples, rate = self.synthesize(text)
        return to_discord_pcm(samples, rate)
