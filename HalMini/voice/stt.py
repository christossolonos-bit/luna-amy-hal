"""Local speech-to-text with faster-whisper (CPU or GPU). No audio leaves the machine."""

from dataclasses import dataclass

from voice.audio import pcm16_to_float, rms

SILENCE_RMS = 0.004          # below this the clip is treated as silence and not transcribed
NO_SPEECH_DROP = 0.6         # Whisper's own "this wasn't speech" probability
LOGPROB_DROP = -1.0


@dataclass
class Transcript:
    text: str
    language: str | None = None
    confidence: float = 0.0  # mean segment log-probability (closer to 0 is better)


class Transcriber:
    def __init__(self, model: str = "small.en", device: str = "auto", compute_type: str = "default",
                 language: str | None = "en") -> None:
        from faster_whisper import WhisperModel  # imported lazily: optional dependency
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.language = language

    def transcribe(self, pcm16_16k_mono: bytes, vocabulary: list[str] | None = None) -> Transcript:
        audio = pcm16_to_float(pcm16_16k_mono)
        if rms(audio) < SILENCE_RMS:
            return Transcript("")
        # Names the engine already knows help Whisper spell them ("Hal", "Howard", ...).
        prompt = ", ".join(vocabulary[:60]) + "." if vocabulary else None
        segments, info = self.model.transcribe(
            audio, language=self.language, beam_size=5, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=prompt,
        )
        kept, logprobs = [], []
        for seg in segments:
            if seg.no_speech_prob > NO_SPEECH_DROP and seg.avg_logprob < LOGPROB_DROP:
                continue  # hallucinated text over noise
            kept.append(seg.text.strip())
            logprobs.append(seg.avg_logprob)
        text = " ".join(t for t in kept if t)
        return Transcript(text, info.language, sum(logprobs) / len(logprobs) if logprobs else 0.0)
