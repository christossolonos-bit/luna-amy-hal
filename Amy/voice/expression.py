"""State -> delivery plan -> model-specific tags. No reply/fact rewriting.

S2/Drama use square brackets, S1 parentheses. Unknown models get plain speech.
Supported control vocabulary follows Fish Audio's emotion and fine-control docs.
The broad catalog is available for previews; automatic selection is restrained.
"""

from dataclasses import asdict, dataclass
import math
import random
import re

from voice.text import speakable

EMOTIONS = (
    "happy", "sad", "angry", "excited", "calm", "nervous", "confident", "surprised", "satisfied",
    "delighted", "scared", "worried", "upset", "frustrated", "depressed", "empathetic", "embarrassed",
    "disgusted", "moved", "proud", "relaxed", "grateful", "curious", "sarcastic", "disdainful", "unhappy",
    "anxious", "hysterical", "indifferent", "uncertain", "doubtful", "confused", "disappointed", "regretful",
    "guilty", "ashamed", "jealous", "envious", "hopeful", "optimistic", "pessimistic", "nostalgic", "lonely",
    "bored", "contemptuous", "sympathetic", "compassionate", "determined", "resigned",
)
DELIVERY = ("in a hurry tone", "shouting", "screaming", "whispering", "soft tone", "emphasis")
SOUNDS = ("laughing", "chuckling", "sobbing", "crying loudly", "sighing", "groaning", "panting", "gasping",
          "yawning", "snoring", "clear throat", "breath")
PAUSES = ("break", "long-break")
BACKGROUND = ("audience laughing", "background laughter", "crowd laughing")
CATALOG = {"emotions": EMOTIONS, "delivery": DELIVERY, "sounds": SOUNDS,
           "pauses": PAUSES, "background": BACKGROUND}
ALLOWED = set(EMOTIONS + DELIVERY + SOUNDS + PAUSES + BACKGROUND)
SENSITIVE = re.compile(r"\b(?:died|death|funeral|grief|grieving|mourning|bereaved|depressed|"
                       r"suicide|heartbroken|devastated|hurt|injured|hospitalized)\b", re.I)
PLAYFUL = re.compile(r"\b(?:haha|ha ha|hehe|funny|hilarious|joke|kidding|teasing|charmer|flattery|"
                     r"way with words|cheeky|laugh)\b", re.I)
S1_SOUNDS = {"laughing": "laugh", "chuckling": "laugh", "sighing": "sigh", "breath": "breath"}


def number(data, key, default=0.0, low=0.0, high=1.0):
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value)) if math.isfinite(value) else default


@dataclass(frozen=True)
class Segment:
    text: str
    emotion: str
    delivery: str | None = None
    effect: str | None = None
    pause_before: bool = False


@dataclass(frozen=True)
class VoicePlan:
    segments: tuple[Segment, ...]
    speed: float
    volume: float
    sensitive: bool
    reasons: tuple[str, ...]

    def render(self, model):
        modern = model.startswith(("s2-pro", "s2.1-pro")) or model == "drama-3"
        legacy = model == "s1"
        if not modern and not legacy:
            return " ".join(s.text for s in self.segments)
        def tag(value):
            if value not in ALLOWED:
                raise ValueError(f"Unknown voice control: {value}")
            value = S1_SOUNDS.get(value, value) if legacy else value
            return f"({value})" if legacy else f"[{value}]"
        chunks = []
        for part in self.segments:
            prefix = [tag("break")] if part.pause_before else []
            prefix.append(tag(part.emotion))
            if part.delivery:
                prefix.append(tag(part.delivery))
            if part.effect:
                prefix.append(tag(part.effect))
            chunks.append("".join(prefix) + " " + part.text)
        return " ".join(chunks)

    def summary(self):
        # Delivery diagnostics only: no reply text, credentials, or reference IDs.
        return {"emotions": [s.emotion for s in self.segments],
                "effects": [s.effect for s in self.segments if s.effect],
                "speed": self.speed, "volume": self.volume, "sensitive": self.sensitive,
                "reasons": list(self.reasons)}


class VoiceExpressionEngine:
    def __init__(self, strength=1.0, effects=True, rng=None):
        strength = float(strength)
        if not math.isfinite(strength):
            raise ValueError("Expression strength must be finite")
        self.strength = max(0.0, min(1.5, strength))
        self.effects = effects
        self.rng = rng if rng is not None else random.Random()
        self.previous_speed = 1.0
        self.previous_emotion = "calm"
        self.turn = 0
        self.last_effect = -10

    def plan(self, text, state=None, context="", intent=None, preview_effect=None):
        state = state if isinstance(state, dict) else {}
        self.turn += 1
        emotions = state.get("emotions", {})
        emotions = emotions if isinstance(emotions, dict) else {}
        chemicals = state.get("chemicals", {})
        chemicals = chemicals if isinstance(chemicals, dict) else {}
        energy = number(state, "energy", 0.65)
        stress = number(state, "stress", 0.3)
        valence = number(state, "valence", 0, -1, 1)
        dopamine = number(chemicals, "dopamine", 10, 0, 100)
        cortisol = number(chemicals, "cortisol", 10, 0, 100)
        oxytocin = number(chemicals, "oxytocin", 5, 0, 100)
        norepinephrine = number(chemicals, "norepinephrine", 8, 0, 100)
        gaba = number(chemicals, "gaba", 12, 0, 100)
        excitement = number(emotions, "excitement", max(0, valence, (dopamine - 10) / 25))
        sadness = number(emotions, "sadness", max(0, -valence))
        anxiety = number(emotions, "anxiety", max(0, stress - 0.3))
        curiosity = number(state, "curiosity", number(emotions, "interest", 0.3))
        affectionate = number(emotions, "affection", number(state, "sociability", 0.5))
        # Chemical deviations add nuance; derived emotions remain the main signal.
        excitement = min(1, excitement * 0.8 + max(0, dopamine - 10) / 50)
        anxiety = min(1, anxiety * 0.8 + max(0, cortisol - 10) / 80)
        warmth = min(1, affectionate * 0.8 + max(0, oxytocin - 5) / 40)
        clean = speakable(text)
        sensitive = bool(SENSITIVE.search(context + " " + clean))
        reasons = []
        if sensitive:
            primary, delivery = "empathetic", "soft tone"
            reasons.append("sensitive-context")
        elif sadness >= 0.35:
            primary, delivery = "sad", "soft tone"
            reasons.append("sadness")
        elif energy < 0.3:
            primary, delivery = "relaxed", "soft tone"
            reasons.append("low-energy")
        elif anxiety >= 0.55 or stress > 0.7:
            primary, delivery = "nervous", None
            reasons.append("stress-anxiety")
        elif excitement >= 0.5 and energy > 0.5:
            primary, delivery = ("delighted" if excitement > 0.85 else "excited"), None
            reasons.append("dopamine-excitement")
        elif warmth > 0.6 or valence > 0.25:
            primary, delivery = "happy", None
            reasons.append("warm-positive-state")
        elif curiosity >= 0.6:
            primary, delivery = "curious", None
            reasons.append("curiosity")
        else:
            primary, delivery = "calm", None
            reasons.append("neutral")
        if not sensitive:
            if self.previous_emotion in ("excited", "delighted") and primary in ("happy", "calm") and excitement > 0.42:
                primary = self.previous_emotion
            elif self.previous_emotion == "sad" and primary == "calm" and sadness > 0.25:
                primary, delivery = "sad", "soft tone"
        target_speed = 1 + self.strength * (0.15 * (energy - 0.5) + 0.1 * excitement - 0.1 * sadness - 0.08 * anxiety)
        target_speed += self.strength * max(-0.04, min(0.04, (norepinephrine - 8) * 0.003 - (gaba - 12) * 0.002))
        if sensitive:
            target_speed = 0.92
        target_speed = max(0.82, min(1.18, target_speed))
        # No abrupt rate swing between replies. Sensitive context still wins on tags immediately.
        speed = round(self.previous_speed + 0.55 * (target_speed - self.previous_speed), 3)
        volume = round(max(-5, min(2, self.strength * (excitement * 1.5 - sadness * 2 - anxiety))), 2)
        if sensitive or delivery == "soft tone":
            volume = min(volume, -1.5)
        self.previous_speed, self.previous_emotion = speed, primary
        paragraphs = [p for p in re.split(r"\n\s*\n", text) if speakable(p)]
        effect = None
        playful = bool(PLAYFUL.search(context + " " + clean))
        if preview_effect is not None:
            if preview_effect not in SOUNDS:
                raise ValueError("Preview effect must be a catalog sound")
            effect = preview_effect if not sensitive else None
        elif self.effects and not sensitive and self.turn - self.last_effect >= 4:
            if playful and valence >= 0 and sadness < 0.25 and anxiety < 0.4:
                if self.rng.random() < 0.55:
                    effect = "laughing" if excitement > 0.75 and re.search(r"\b(?:haha|hilarious|laugh)\b", context + " " + clean, re.I) else "chuckling"
            elif energy < 0.2 and sadness < 0.3 and self.rng.random() < 0.18:
                effect = "yawning"
            elif anxiety > 0.65 and self.rng.random() < 0.15:
                effect = "sighing"
            elif len(clean.split()) > 65 and self.rng.random() < 0.25:
                effect = "breath"
        if effect:
            self.last_effect = self.turn
        effect_index = 1 if effect == "breath" and len(paragraphs) > 1 else 0
        parts = []
        for index, paragraph in enumerate(paragraphs):
            chunk = speakable(paragraph)
            emotion = "curious" if chunk.endswith("?") and curiosity > 0.55 and not sensitive \
                      and primary not in ("sad", "nervous") else primary
            parts.append(Segment(chunk, emotion, delivery, effect if index == effect_index else None, index > 0))
        if self.strength == 0:
            parts = [Segment(s.text, "calm", pause_before=s.pause_before) for s in parts]
            speed, volume = 1.0, 0.0
        return VoicePlan(tuple(parts), speed, volume, sensitive, tuple(reasons))
