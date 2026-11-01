"""The bot's own state, modelled as a small neurochemical system (adapted from seed0001/human-sim's
chemical_system.py and sleep_system.py).

Each chemical has a baseline and a half-life and relaxes exponentially back to baseline. Conversation
events release or suppress chemicals; higher-level dimensions (mood/valence, energy, curiosity,
sociability, stress, loneliness) are computed from the levels, and those are what "how are you?"
reads. Everything is explicit and inspectable (`!state` / `:state`). It models the program's
condition; it is not a claim of experienced feeling.

Changes from human-sim:
  - recovery is C <- B + (C - B) * 0.5^(dt / half_life) instead of decay plus a flat top-up below
    baseline, which could overshoot;
  - half-lives are stretched from seconds to minutes/hours so state persists across a chat;
  - sleep rates are in consistent units: rest clears adenosine over tens of minutes, not seconds;
  - loneliness (from the README: no social contact raises cortisol and lowers serotonin/dopamine)
    shifts the baselines; local night hours add melatonin-style sleepiness.
"""

import json
import math
import time
from datetime import datetime

MINUTE, HOUR = 60.0, 3600.0

# name: (baseline, half-life in seconds). Baselines follow human-sim; half-lives are rescaled.
CHEMICALS: dict[str, tuple[float, float]] = {
    "dopamine":       (10.0, 10 * MINUTE),   # reward, motivation
    "serotonin":      (10.0, 60 * MINUTE),   # mood, wellbeing
    "cortisol":       (10.0, 90 * MINUTE),   # stress
    "oxytocin":       (5.0, 20 * MINUTE),    # bonding, trust
    "gaba":           (12.0, 30 * MINUTE),   # calm
    "norepinephrine": (8.0, 10 * MINUTE),    # alertness, focus
    "endorphins":     (5.0, 30 * MINUTE),    # comfort
}

# What each conversation event releases (+) or suppresses (-), in concentration units.
EVENTS: dict[str, dict[str, float]] = {
    "learned":       {"dopamine": +1.5},
    "answered":      {"dopamine": +0.5, "serotonin": +0.3},
    "unknown":       {"norepinephrine": +1.0, "dopamine": +0.3},
    "misunderstood": {"cortisol": +1.5, "norepinephrine": +1.0, "gaba": -1.0},
    "corrected":     {"cortisol": +1.0, "dopamine": -0.5},
    "greeted":       {"oxytocin": +2.0, "dopamine": +1.0},
    "thanked":       {"dopamine": +3.0, "oxytocin": +2.0, "serotonin": +1.0},
    "complimented":  {"dopamine": +4.0, "serotonin": +1.5, "oxytocin": +1.5, "endorphins": +1.0},
    "insulted":      {"cortisol": +6.0, "norepinephrine": +3.0, "serotonin": -2.0, "oxytocin": -1.0},
    "repeated":      {"cortisol": +1.5, "norepinephrine": +0.8, "gaba": -0.5},  # asked the same thing again
    "turn":          {},
    "encouraged":    {"dopamine": +3.0, "serotonin": +1.5, "oxytocin": +1.0},
    "sad_context":   {"serotonin": -1.5, "dopamine": -1.0, "cortisol": +1.0},
}

ADENOSINE_BASE = 1.0
ADENOSINE_PER_TURN = 0.05      # mental work builds sleep pressure
ADENOSINE_PER_HOUR = 0.15      # so does simply being awake
ADENOSINE_CLEAR_PER_HOUR = 2.0  # while resting
REST_GAP = 30 * MINUTE         # this long with nobody talking counts as rest
SLEEP_GAP = 4 * HOUR           # this long counts as a night's sleep: wake-up resets time awake
WAKE_CORTISOL = 5.0            # human-sim's cortisol spike on waking
MAX_AWAKE = 16 * HOUR          # human-sim's normaliser for time awake
LONELY_AFTER = 1 * HOUR        # loneliness starts building after this long without contact
LONELY_FULL = 7 * HOUR
LOG_EVERY = 60.0               # seconds between state snapshots kept for the dashboard


def _relax(value: float, base: float, half_life: float, dt: float) -> float:
    return base + (value - base) * 0.5 ** (dt / half_life)


class SelfModel:
    def __init__(self, repo, clock=time.time) -> None:
        self.repo = repo
        self.clock = clock
        row = repo.conn.execute("SELECT value FROM self_state WHERE key = 'state'").fetchone()
        saved = json.loads(row[0]) if row else {}
        now = clock()
        self.vars: dict = {
            "born": saved.get("born", now),
            "updated": saved.get("updated", now),
            "last_contact": saved.get("last_contact", now),
            "awake_since": saved.get("awake_since", now),
            "adenosine": saved.get("adenosine", ADENOSINE_BASE),
            "chemicals": {**{n: b for n, (b, _) in CHEMICALS.items()}, **saved.get("chemicals", {})},
            "counts": saved.get("counts", {}),
        }

    def _save(self) -> None:
        with self.repo.conn:
            self.repo.conn.execute(
                "INSERT INTO self_state(key, value) VALUES ('state', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                (json.dumps(self.vars),))
        self._log()

    def _log(self) -> None:
        """At most one snapshot a minute, for the dashboard's timeline."""
        now = self.vars["updated"]
        last = self.repo.conn.execute("SELECT MAX(ts) FROM self_state_log").fetchone()[0]
        if last is not None and now - last < LOG_EVERY:
            return
        snap = {**self.current(now), "emotions": self.emotions(now),
                "chemicals": {n: round(v, 2) for n, v in self.vars["chemicals"].items()},
                "adenosine": round(self.vars["adenosine"], 2)}
        with self.repo.conn:
            self.repo.conn.execute("INSERT INTO self_state_log(ts, snapshot_json) VALUES (?, ?)",
                                   (now, json.dumps(snap)))

    # --- dynamics -----------------------------------------------------------------

    def loneliness(self, now: float) -> float:
        idle = now - self.vars["last_contact"]
        return max(0.0, min(1.0, (idle - LONELY_AFTER) / (LONELY_FULL - LONELY_AFTER)))

    def _baselines(self, now: float) -> dict[str, float]:
        lonely = self.loneliness(now)
        base = {n: b for n, (b, _) in CHEMICALS.items()}
        base["serotonin"] *= 1 - 0.3 * lonely
        base["dopamine"] *= 1 - 0.2 * lonely
        base["cortisol"] *= 1 + 0.5 * lonely
        base["oxytocin"] *= 1 - 0.4 * lonely
        return base

    def _advance(self, now: float) -> None:
        dt = max(0.0, now - self.vars["updated"])
        if dt == 0:
            return
        base = self._baselines(now)
        chems = self.vars["chemicals"]
        for name, (_, half_life) in CHEMICALS.items():
            chems[name] = max(0.0, _relax(chems[name], base[name], half_life, dt))
        idle = now - self.vars["last_contact"]
        if idle >= REST_GAP:
            rested = min(dt, idle - REST_GAP)
            awake = dt - rested
            self.vars["adenosine"] = max(ADENOSINE_BASE, self.vars["adenosine"]
                                         + ADENOSINE_PER_HOUR * awake / HOUR
                                         - ADENOSINE_CLEAR_PER_HOUR * rested / HOUR)
        else:
            self.vars["adenosine"] += ADENOSINE_PER_HOUR * dt / HOUR
        self.vars["updated"] = now

    def _on_contact(self, now: float) -> None:
        if now - self.vars["last_contact"] >= SLEEP_GAP:  # "waking up" after a long quiet spell
            self.vars["awake_since"] = now
            self.vars["chemicals"]["cortisol"] += WAKE_CORTISOL
        self.vars["last_contact"] = now
        self.vars["adenosine"] += ADENOSINE_PER_TURN

    def event(self, kind: str, times: int = 1, now: float | None = None, detail: str = "") -> None:
        if times <= 0:
            return
        now = self.clock() if now is None else now
        self._advance(now)
        if kind == "turn":
            self._on_contact(now)
        chems = self.vars["chemicals"]
        for name, delta in EVENTS[kind].items():
            chems[name] = max(0.0, chems[name] + delta * times)
        counts = self.vars["counts"]
        counts[kind] = counts.get(kind, 0) + times
        self._save()
        if kind != "turn":
            with self.repo.conn:
                self.repo.conn.execute("INSERT INTO emotion_events(ts,event,detail,emotions_json) VALUES(?,?,?,?)",
                                       (now, kind, detail, json.dumps(self.emotions(now))))

    # --- derived dimensions -------------------------------------------------------

    @staticmethod
    def _night_pressure(now: float) -> float:
        hour = datetime.fromtimestamp(now).hour
        return 0.25 if hour >= 23 or hour < 6 else 0.0

    def current(self, now: float | None = None) -> dict[str, float]:
        now = self.clock() if now is None else now
        self._advance(now)
        c = self.vars["chemicals"]
        b = {n: base for n, (base, _) in CHEMICALS.items()}
        d = {n: c[n] - b[n] for n in c}
        valence = math.tanh(0.10 * d["serotonin"] + 0.08 * d["dopamine"] + 0.06 * d["oxytocin"]
                            + 0.05 * d["endorphins"] - 0.08 * d["cortisol"])
        awake = min(1.0, (now - self.vars["awake_since"]) / MAX_AWAKE)
        tiredness = min(1.0, 0.6 * awake + 0.4 * min(1.0, self.vars["adenosine"] / 5.0)
                        + self._night_pressure(now))
        clamp = lambda x: round(max(0.0, min(1.0, x)), 3)  # noqa: E731
        return {
            "valence": round(valence, 3),
            "energy": clamp(1.0 - tiredness + 0.04 * d["norepinephrine"]),
            "curiosity": clamp(0.3 + 0.08 * d["norepinephrine"] + 0.04 * d["dopamine"]),
            "sociability": clamp(0.5 + 0.08 * d["oxytocin"] - 0.03 * d["cortisol"]),
            "stress": clamp(0.3 + 0.06 * d["cortisol"] + 0.04 * d["norepinephrine"] - 0.05 * d["gaba"]),
            "loneliness": round(self.loneliness(now), 3),
        }

    # --- words for the numbers ------------------------------------------------------

    def emotions(self, now: float | None = None) -> dict[str, float]:
        """Named 0..1 intensities derived from the persisted chemical system."""
        s = self.current(now)
        clamp = lambda v: round(max(0.0, min(1.0, v)), 3)
        return {
            "excitement": clamp(max(0, s["valence"]) * (0.6 + 0.4 * s["energy"])),
            "sadness": clamp(max(0, -s["valence"]) * 0.8 + s["loneliness"] * 0.35),
            "anxiety": clamp((s["stress"] - 0.3) / 0.7),
            "frustration": clamp(max(0, -s["valence"]) * s["stress"]),
            "contentment": clamp((s["valence"] + 1) / 2 * (1 - s["stress"])),
            "interest": s["curiosity"],
            "affection": s["sociability"],
        }

    def expression(self, sensitive: bool = False) -> dict:
        """Expression changes, never truth values, confidence, or evidence selection."""
        e = self.emotions()
        tone = ("gentle" if sensitive else "subdued" if e["sadness"] > 0.35 else
                "measured" if e["anxiety"] > 0.5 or e["frustration"] > 0.45 else
                "excited" if e["excitement"] > 0.5 else "warm" if e["contentment"] > 0.5 else "neutral")
        return {"tone": tone, "emotions": e,
                "follow_up": e["interest"] > 0.45 and tone not in ("gentle", "subdued", "measured"),
                "pace": "deliberate" if tone in ("subdued", "gentle", "measured") else
                        "lively" if tone == "excited" else "steady"}

    def mood_word(self) -> str:
        v = self.current()["valence"]
        if v > 0.55:
            return "great"
        if v > 0.2:
            return "good"
        if v > -0.15:
            return "okay"
        if v > -0.45:
            return "a bit off"
        return "pretty low"

    def is_state(self, adjective: str) -> bool | None:
        """Answer 'are you happy/tired/stressed...?' from the levels; None if not a state word."""
        s = self.current()
        checks = {
            "happy": s["valence"] > 0.2, "good": s["valence"] > 0.1, "ok": s["valence"] > -0.15,
            "okay": s["valence"] > -0.15, "fine": s["valence"] > -0.15, "great": s["valence"] > 0.55,
            "sad": s["valence"] < -0.15, "upset": s["valence"] < -0.35, "unhappy": s["valence"] < -0.15,
            "excited": self.emotions()["excitement"] > 0.5,
            "frustrated": self.emotions()["frustration"] > 0.45,
            "tired": s["energy"] < 0.35, "sleepy": s["energy"] < 0.35, "exhausted": s["energy"] < 0.15,
            "energetic": s["energy"] > 0.75, "awake": s["energy"] > 0.35,
            "curious": s["curiosity"] > 0.4, "bored": s["curiosity"] < 0.15,
            "stressed": s["stress"] > 0.55, "anxious": s["stress"] > 0.6, "nervous": s["stress"] > 0.55,
            "calm": s["stress"] < 0.35, "relaxed": s["stress"] < 0.35,
            "lonely": s["loneliness"] > 0.3, "social": s["sociability"] > 0.5, "friendly": s["sociability"] > 0.4,
        }
        return checks.get(adjective)

    def age_seconds(self) -> float:
        return self.clock() - self.vars["born"]

    def snapshot(self) -> dict:
        dims = self.current()
        return {**dims, "mood": self.mood_word(), "emotions": self.emotions(), "expression": self.expression(),
                "chemicals": {n: round(v, 2) for n, v in self.vars["chemicals"].items()},
                "adenosine": round(self.vars["adenosine"], 2),
                "hours_awake": round((self.clock() - self.vars["awake_since"]) / HOUR, 2),
                "counts": dict(self.vars["counts"]),
                "age_hours": round(self.age_seconds() / HOUR, 1)}
