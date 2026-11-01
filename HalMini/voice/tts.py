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

FISH_TTS_URL = "https://api.fish.audio/v1/tts"


def speakable(text: str) -> str:
    """Strip things that read badly aloud: markdown, quote marks, bracketed debug notes."""
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"[*_`#>“”\"]", "", text)
    return re.sub(r"\s+", " ", text).strip()


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
                 latency: str = "normal", timeout: float = 30.0, url: str = FISH_TTS_URL) -> None:
        if not api_key:
            raise TTSError("FISH_API_KEY is not set")
        self.api_key, self.voice_id, self.model = api_key, voice_id, model
        self.latency, self.timeout, self.url = latency, timeout, url

    def _request(self, text: str) -> bytes:
        body = {"text": text, "format": "wav", "latency": self.latency, "normalize": True}
        if self.voice_id:
            body["reference_id"] = self.voice_id
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

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        return read_wav(self._request(speakable(text)))

    def discord_pcm(self, text: str) -> bytes:
        samples, rate = self.synthesize(text)
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
