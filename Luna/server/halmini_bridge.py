"""Amy/HalMini fact+dialogue brain for Luna: try first; LLM teaches on miss.

Identity stays Luna (self_name). Skills + persona come from Amy when
HALMINI_ROOT points at D:\\Amy\\Amy and HALMINI_PERSONA=amy.
No Amy Discord bot — LunaBot is discord-bridge/ in this project.
See HALMINI.md.
"""

from __future__ import annotations

import inspect
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from server import config

_TAG_RE = re.compile(r"<[a-zA-Z][^<>]*>")
_LEAD_YN = re.compile(
    r"^\s*(?:yes|no|probably|i think|yeah|yep|nope)\s*[.!]?\s*",
    re.IGNORECASE,
)
_BECAUSE_TAIL = re.compile(
    r"\s+I (?:know this|think so) because\b.*$",
    re.IGNORECASE,
)
_UNKNOWN_MARKERS = (
    "i don't know",
    "i do not know",
    "i can't phrase that answer reliably",
    "i wont guess",
    "i won't guess",
    "not sure what you mean",
    "could you rephrase",
    "who or what do you mean",
)

# Without Amy persona: leave soft chat to Luna's LLM.
# With persona=amy: let warm social/curiosity through as Luna.
_SOFT_ALWAYS_LLM = frozenset({"news", "web-lookup"})
_SOFT_WITHOUT_PERSONA = frozenset({
    "social", "drive", "guide", "news", "web-lookup", "explain-self",
    "answer-to-my-question", "thread",
})

_lock = threading.Lock()
_convo: Any = None
_init_error: Optional[str] = None


@dataclass
class HalMiniResult:
    ok: bool
    text: str
    trace: dict
    reason: str = ""


def enabled() -> bool:
    return bool(getattr(config, "HALMINI_ENABLED", True))


def _persona() -> str:
    return str(getattr(config, "HALMINI_PERSONA", "amy") or "").casefold()


def _root() -> Path:
    return Path(config.HALMINI_ROOT).expanduser().resolve()


def _ensure_path() -> None:
    root = _root()
    if not root.is_dir():
        raise FileNotFoundError(f"HALMINI_ROOT not found: {root}")
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)


def _get_convo():
    """Lazy singleton Conversation: Luna name + Amy persona/skills when available."""
    global _convo, _init_error
    if _convo is not None:
        return _convo
    if _init_error is not None:
        raise RuntimeError(_init_error)
    with _lock:
        if _convo is not None:
            return _convo
        try:
            _ensure_path()
            from app.conversation import Conversation
            from db.repository import Repository

            db_path = Path(config.HALMINI_DB)
            if not db_path.is_absolute():
                db_path = Path(config.PROJECT_ROOT) / db_path
            db_path.parent.mkdir(parents=True, exist_ok=True)
            name = (config.HALMINI_SELF_NAME or "Luna").strip() or "Luna"
            persona = _persona()
            kwargs = dict(
                speaker="user",
                self_name=name,
                curious=True if persona == "amy" else False,
                elaborate=False,
            )
            # Amy Conversation accepts persona=; plain HalMini may not.
            if "persona" in inspect.signature(Conversation.__init__).parameters:
                kwargs["persona"] = persona or None
            _convo = Conversation(Repository(str(db_path)), **kwargs)
            print(
                f"[halmini] ready db={db_path} self={name!r} "
                f"persona={getattr(_convo, 'persona', None)!r} root={_root()}"
            )
            return _convo
        except Exception as exc:
            _init_error = f"{type(exc).__name__}: {exc}"
            print(f"[halmini] init failed: {_init_error}")
            raise


def _strip_assistant(text: str) -> str:
    out = _TAG_RE.sub("", text or "")
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _facts_for_teaching(assistant_text: str) -> str:
    """Turn an LLM reply into statement(s) the brain can try to learn."""
    out = _strip_assistant(assistant_text)
    out = _BECAUSE_TAIL.sub("", out).strip()
    out = _LEAD_YN.sub("", out).strip()
    out = re.sub(r"\s*(?:hope that helps|let me know|anything else\??)\s*$",
                 "", out, flags=re.I).strip()
    return out


def _statuses(trace: dict) -> list[str]:
    found: list[str] = []
    if trace.get("status"):
        found.append(str(trace["status"]))
    for item in trace.get("sentences") or []:
        for ans in item.get("answers") or []:
            st = ans.get("status")
            if st:
                found.append(str(st))
    return found


def _soft_intents() -> frozenset[str]:
    if _persona() == "amy":
        return _SOFT_ALWAYS_LLM
    return _SOFT_WITHOUT_PERSONA


def knew_answer(reply_text: str, trace: dict) -> tuple[bool, str]:
    """True when the brain produced something Luna should speak (skip LLM)."""
    text = (reply_text or "").strip()
    if not text:
        return False, "empty"

    intent = str(trace.get("intent") or "")
    if intent in _soft_intents():
        return False, f"soft-intent:{intent}"

    # Amy persona social/drive/thread — speak as Luna.
    if _persona() == "amy" and intent in (
        "social", "drive", "guide", "explain-self", "answer-to-my-question", "thread",
    ):
        return True, f"persona:{intent}"

    low = text.lower()
    if any(m in low for m in _UNKNOWN_MARKERS):
        return False, "unknown-phrasing"

    if intent == "clarify":
        return False, "clarify"

    statuses = _statuses(trace)
    if statuses and all(s == "unknown" for s in statuses):
        return False, "status-unknown"

    if any(s in ("yes", "no", "conflicting") for s in statuses):
        return True, "status-answer"

    if intent in ("recall", "retract", "forget", "learned", "answered"):
        return True, f"intent:{intent}"

    sentences = trace.get("sentences") or []
    stored_any = any(s.get("stored") for s in sentences)
    if stored_any:
        return True, "stored"
    if any(s.get("learned") for s in sentences):
        return True, "learned"
    if re.search(
        r"\b(i'?ve (?:learned|recorded|kept)|this is what i'?ve learned|"
        r"kept this information)\b",
        low,
    ):
        return True, "learn-ack"

    if intent in ("describe",) or "paragraph_plan" in trace or "lexical_plan" in trace:
        plan = trace.get("paragraph_plan") or trace.get("lexical_plan") or {}
        if plan.get("claims") or plan.get("status") in ("yes", "no"):
            return True, "exposition"
        if statuses and "unknown" not in statuses:
            return True, "exposition-status"

    if statuses and any(s != "unknown" for s in statuses):
        return True, "non-unknown-status"

    if not sentences and intent not in ("recall", "social", "drive"):
        if _persona() == "amy" and intent:
            return True, f"handler:{intent}"
        return False, "no-sentences"

    failed = any(s.get("errors") for s in sentences)
    answers = any(s.get("answers") for s in sentences)
    if failed and not answers and not stored_any:
        return False, "parse-failed"

    if answers or stored_any:
        return True, "parsed"

    if _persona() == "amy" and intent and intent not in _SOFT_ALWAYS_LLM:
        return True, f"persona-fallback:{intent}"

    return False, "default-fallback"


def try_answer(user_text: str, speaker: str | None = None) -> HalMiniResult:
    """Ask the brain. ok=True means speak this and skip the LLM.

    HalMini learns from addressed turns inside Conversation.handle each time.
    """
    if not enabled():
        return HalMiniResult(False, "", {}, "disabled")
    text = (user_text or "").strip()
    if not text:
        return HalMiniResult(False, "", {}, "empty-user")
    # Prefer spoken words over Discord VC labels for the fact parser.
    spoken = text
    who = speaker
    speaker_id = None
    try:
        from server.memory.discord_speaker import parse_discord_speaker, strip_discord_prefix
        sp = parse_discord_speaker(text)
        if sp:
            spoken = strip_discord_prefix(text) or text
            who = who or sp.display
            speaker_id = sp.subject_id
        spoken = re.sub(rf"^\s*{re.escape('[wake]')}\s*", "", spoken, flags=re.I).strip()
    except Exception:
        spoken = text
    if not spoken:
        return HalMiniResult(False, "", {}, "empty-spoken")
    try:
        convo = _get_convo()
        with _lock:
            kwargs = {"speaker": who or "user"}
            if speaker_id:
                kwargs["speaker_id"] = speaker_id
            reply = convo.handle(spoken, **kwargs)
        ok, reason = knew_answer(reply.text, reply.trace or {})
        print(f"[halmini] try ok={ok} reason={reason} reply={reply.text[:120]!r}")
        return HalMiniResult(ok, reply.text or "", dict(reply.trace or {}), reason)
    except Exception as exc:
        print(f"[halmini] try failed: {type(exc).__name__}: {exc}")
        return HalMiniResult(False, "", {}, f"error:{type(exc).__name__}")


def remember_utterance(
    text: str,
    *,
    speaker: str = "someone",
    speaker_id: str | None = None,
    learn: bool = True,
    is_bot: bool = False,
) -> None:
    """Store speech in HalMini and learn facts from it when learn=True.

    Called on every overhear / wake / local turn so Hal updates each time.
    No NVIDIA / Ollama.
    """
    if not enabled():
        return
    body = (text or "").strip()
    if not body:
        return
    try:
        convo = _get_convo()
        with _lock:
            convo.overhear(
                body,
                speaker=speaker or "someone",
                learn=bool(learn),
                speaker_id=speaker_id,
                is_bot=bool(is_bot),
            )
        print(
            f"[halmini] remember learn={learn} speaker={speaker!r} "
            f"text={body[:100]!r}"
        )
    except Exception as exc:
        print(f"[halmini] remember failed: {type(exc).__name__}: {exc}")


def teach(assistant_text: str, user_text: str = "", speaker: str = "TutorBot") -> str:
    """Feed the LLM's answer into the brain as learnable statements."""
    if not enabled():
        return ""
    facts = _facts_for_teaching(assistant_text)
    if len(facts) < 8:
        print(f"[halmini] teach skip (too short): {facts!r}")
        return ""
    if facts.endswith("?") and not re.search(r"\b(is|are|owns?|lives?|has)\b.+\.", facts):
        print(f"[halmini] teach skip (question-shaped): {facts!r}")
        return ""
    parts = re.split(r"(?<=[.!])\s+", facts)
    keep: list[str] = []
    for p in parts:
        p = p.strip()
        if not p or p.endswith("?"):
            continue
        keep.append(p)
        if sum(len(x) for x in keep) > 280:
            break
    payload = " ".join(keep) if keep else facts
    try:
        convo = _get_convo()
        with _lock:
            if not re.match(r"(?i)^remember\b", payload) and len(payload) < 400:
                payload = f"Remember this: {payload}"
            taught = convo.handle(payload, speaker=speaker, is_bot=True)
        print(f"[halmini] taught from LLM → {taught.text[:120]!r}")
        return taught.text or ""
    except Exception as exc:
        print(f"[halmini] teach failed: {type(exc).__name__}: {exc}")
        return ""
