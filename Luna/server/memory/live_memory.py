"""Live Discord/local memory — turn log + HalMini learning.

Every overheard VC / wake / local line is written to SQLite and taught into
HalMini immediately (learn=True each time). NVIDIA is not used for memory —
only for chat when HalMini cannot answer (then teach-back).
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from server.memory import live_store

WORKING_MAX = 2500
DURABLE_MAX = 5000
PROFILE_FIELD_MAX = 800
TURN_FEED_MAX = 60
RECENT_INJECT = 25

_consolidating = False
_pending = False
_bg_lock = threading.Lock()


def _teach_halmini_turn(
    *,
    body: str,
    source: str,
    user_id: Optional[str],
    display: Optional[str],
    speaker: Optional[str],
) -> None:
    """HalMini learns from this utterance right when it happens.

    - overhear: learn every time (HalMini.overhear(learn=True) → handle)
    - wake/local: skip here — orchestrator try_answer/handle already runs
    - reply: skip — NVIDIA teach-back (or HalMini already answered via handle)
    """
    text = (body or "").strip()
    if not text:
        return
    if source in ("reply", "wake", "local"):
        return
    who = (speaker or display or "someone").strip() or "someone"
    try:
        from server.halmini_bridge import remember_utterance
        remember_utterance(
            text,
            speaker=who,
            speaker_id=str(user_id) if user_id else None,
            learn=True,  # every overhear teaches Hal — not log-only
            is_bot=False,
        )
    except Exception as e:
        print(f"[live-mem] HalMini learn failed: {type(e).__name__}: {e}")


def record_turn(
    *,
    body: str,
    source: str = "overhear",
    user_id: Optional[str] = None,
    display: Optional[str] = None,
    role: Optional[str] = None,
    speaker: Optional[str] = None,
    trigger_consolidate: bool = True,
) -> int:
    """Append a permanent turn and teach HalMini immediately."""
    who = speaker or display or user_id or ("Luna" if source == "reply" else "someone")
    seq = live_store.append_turn(
        source=source,
        body=body,
        speaker=who,
        user_id=user_id,
        display=display,
        role=role,
    )
    if seq:
        _teach_halmini_turn(
            body=body,
            source=source,
            user_id=user_id,
            display=display,
            speaker=str(who) if who else None,
        )
    if seq and trigger_consolidate:
        schedule_consolidate()
    return seq


def schedule_consolidate() -> None:
    """Refresh simple working snippet from recent turns (no NVIDIA)."""
    global _consolidating, _pending
    with _bg_lock:
        if _consolidating:
            _pending = True
            return
        _consolidating = True

    def _run() -> None:
        global _consolidating, _pending
        try:
            while True:
                try:
                    consolidate_once()
                except Exception as e:
                    print(f"[live-mem] consolidate failed: {type(e).__name__}: {e}")
                with _bg_lock:
                    if _pending:
                        _pending = False
                        continue
                    _consolidating = False
                    return
        finally:
            with _bg_lock:
                _consolidating = False

    threading.Thread(target=_run, name="luna-live-mem", daemon=True).start()


def consolidate_once() -> bool:
    """Mark turns consolidated + refresh working from recent lines. Hal already learned live."""
    turns = live_store.unconsolidated_turns(limit=TURN_FEED_MAX)
    if not turns:
        return False

    new_working = "Recent call:\n" + "\n".join(
        f"- {t.get('speaker')}: {t.get('body')}" for t in turns[-12:]
    )
    live_store.set_doc("working", new_working[:WORKING_MAX])
    live_store.mark_consolidated(int(turns[-1]["seq"]))
    print(
        f"[live-mem] HalMini-fed {len(turns)} turn(s) "
        f"up_to=seq{turns[-1]['seq']} (no NVIDIA gist)"
    )
    return True


def _format_profile(user_id: Optional[str]) -> str:
    if not user_id:
        return ""
    card = live_store.get_profile(str(user_id))
    if not card:
        return ""
    lines = [f"Name: {card.get('name') or '?'}"]
    for label, key in (
        ("Goals", "goals"),
        ("Active projects", "active_projects"),
        ("Constraints", "constraints"),
        ("Vibe notes", "vibe_notes"),
        ("Notes", "notes"),
    ):
        if card.get(key):
            lines.append(f"{label}: {card[key]}")
    return "[SPEAKER PROFILE — who you're talking to right now]\n" + "\n".join(lines)


def get_context(
    user_id: Optional[str] = None,
    *,
    recent_limit: int = RECENT_INJECT,
    query: Optional[str] = None,
) -> str:
    """Prompt injection — profile + working/durable + turn log (+ optional search)."""
    uid = str(user_id).strip() if user_id else None
    parts: list[str] = [
        "MEMORY RULES (HARD):\n"
        "- CURRENT SPEAKER facts come only from their SPEAKER PROFILE and "
        "their own lines below.\n"
        "- Other people's lines are overheard context — never treat them as "
        "facts ABOUT the current speaker.\n"
        "- Do not invent hobbies, trips, instruments, or preferences for "
        "someone unless their own profile/lines say so.\n"
        "- If unsure who a fact belongs to, stay quiet about it.\n"
        "- Prefer the turn log and profile over any fuzzy guess.\n"
        "- If they ask their own name / who they are / what you remember "
        "about them: answer with the known fact in the first sentence "
        "(e.g. say Solonaras). Do not refuse, stall, or tease-withhold.\n"
        "- Direct questions (any topic): answer in the first sentence, "
        "then you may tease. Do not dodge, psychoanalyze their motive, "
        "or withhold the answer behind flirt."
    ]

    profile = _format_profile(uid)
    if profile:
        parts.append(profile)

    durable = live_store.get_doc("durable").strip()
    if durable:
        parts.append(
            "[DURABLE — shared group facts only, not one person's biography]\n"
            + durable
        )

    working = live_store.get_doc("working").strip()
    if working:
        parts.append(
            "[WORKING — live call topic; keep people distinct]\n" + working
        )

    # Exact keyword search when they ask what was said / remembered.
    q = (query or "").strip()
    if q and _looks_like_recall(q):
        # Search this speaker first, then whole log if thin.
        hit = recall(_recall_keywords(q), user_id=uid, limit=20)
        if hit == "(no matching turns)" and uid:
            hit = recall(_recall_keywords(q), user_id=None, limit=20)
        if hit != "(no matching turns)":
            parts.append(
                "[TURN LOG SEARCH — exact matches for this question; "
                "attribute each line to the named speaker only]\n" + hit
            )

    recent = live_store.recent_turns(limit=max(recent_limit * 2, 40))
    mine: list[str] = []
    others: list[str] = []
    for t in recent:
        src = t.get("source") or ""
        # Skip her own long replies — they often invent and then re-poison memory.
        if src == "reply":
            continue
        who = t.get("speaker") or t.get("display") or "?"
        body = (t.get("body") or "").strip()
        if not body:
            continue
        line = f"- [{src}] {who}: {body}"
        t_uid = str(t.get("user_id") or "")
        if uid and t_uid == uid:
            mine.append(line)
        else:
            others.append(line)

    if mine:
        parts.append(
            "THIS SPEAKER's own recent lines (trusted for their facts):\n"
            + "\n".join(mine[-recent_limit:])
        )
    if others:
        parts.append(
            "OTHER people in the call (overheard only — do NOT attribute "
            "these facts to the current speaker unless they ask about them):\n"
            + "\n".join(others[-recent_limit:])
        )

    return "\n\n".join(parts)


_RECALL_HINTS = (
    "remember", "remind", "what did", "what was", "what were", "earlier",
    "before", "talking about", "said about", "told you", "you know about",
    "recall", "last time", "we talked", "did i say", "did he say", "did she say",
)


def _looks_like_recall(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _RECALL_HINTS)


def _recall_keywords(text: str) -> str:
    """Strip wake fluff so LIKE search hits content words."""
    import re
    t = (text or "").strip()
    t = re.sub(
        r"\b(luna|hey luna|okay luna|ok luna|please|remind me|do you remember)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"[^\w\s]", " ", t)
    words = [w for w in t.split() if len(w) > 2]
    # Prefer content words; fall back to original if nothing left.
    return " ".join(words[:8]) if words else text.strip()[:80]


def recall(query: str = "", *, user_id: Optional[str] = None, limit: int = 40) -> str:
    rows = live_store.search_turns(query, user_id=user_id, limit=limit)
    if not rows:
        return "(no matching turns)"
    return "\n".join(
        f"[{t.get('source')}] {t.get('speaker')}: {t.get('body')}" for t in rows
    )


def stats() -> dict[str, Any]:
    return {
        "turns": live_store.turn_count(),
        "working_chars": len(live_store.get_doc("working")),
        "durable_chars": len(live_store.get_doc("durable")),
        "consolidating": _consolidating,
        "db": str(live_store._db_path()),
    }
