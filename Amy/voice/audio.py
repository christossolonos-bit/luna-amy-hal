"""PCM helpers. Discord plays 48 kHz stereo s16le; Whisper takes 16 kHz mono float32."""

import numpy as np

DISCORD_RATE = 48000


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.float32)
    duration = len(samples) / src_rate
    n_out = int(round(duration * dst_rate))
    src_t = np.arange(len(samples)) / src_rate
    dst_t = np.arange(n_out) / dst_rate
    return np.interp(dst_t, src_t, samples).astype(np.float32)


def to_discord_pcm(mono: np.ndarray, rate: int) -> bytes:
    """Mono float samples at any rate -> 48 kHz stereo s16le for the sidecar's player."""
    upsampled = resample(mono, rate, DISCORD_RATE)
    stereo = np.repeat(upsampled[:, None], 2, axis=1).reshape(-1)
    return float_to_pcm16(stereo)


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
