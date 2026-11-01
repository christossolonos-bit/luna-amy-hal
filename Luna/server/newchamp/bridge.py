"""Public New Champ API for Luna: recall / learn after each exchange."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Optional

from server import config

_JSON_RE = re.compile(r"\{[\s\S]*\}")
_chain_lock = threading.Lock()
_extraction_chain: Optional[threading.Thread] = None
_ready = False


def enabled() -> bool:
    return bool(getattr(config, "NEWCHAMP_ENABLED", True))


def _ensure() -> None:
    global _ready
    if _ready or not enabled():
        return
    from server.newchamp import store

    store.configure(config.NEWCHAMP_DB)
    src = getattr(config, "NEWCHAMP_IMPORT_DB", "") or ""
    if src:
        try:
            n = store.import_text_memories_from_companion(src)
            if n:
                print(f"[newchamp] imported {n} fact(s) from {src}")
        except Exception as e:
            print(f"[newchamp] import skipped: {type(e).__name__}: {e}")
    _ready = True


def recall(user_id: str, query: str) -> list[dict[str, Any]]:
    if not enabled() or not user_id:
        return []
    _ensure()
    from server.newchamp import store

    return store.recall(
        user_id,
        query,
        top_k=int(getattr(config, "NEWCHAMP_TOP_K", 6)),
        min_score=float(getattr(config, "NEWCHAMP_MIN_SCORE", 0.35)),
    )


def remember_facts(user_id: str, facts: list[str]) -> list[str]:
    if not enabled() or not user_id:
        return []
    _ensure()
    from server.newchamp import store

    return store.remember(
        user_id,
        facts,
        dedupe_score=float(getattr(config, "NEWCHAMP_DEDUPE_SCORE", 0.9)),
    )


def context_block(user_id: str, query: str, speaker_name: str = "them") -> str:
    """Prompt injection: things Luna remembers about this speaker."""
    hits = recall(user_id, query)
    if not hits:
        return ""
    lines = "\n".join(f"- {h['content']}" for h in hits)
    return (
        f"Things you remember about {speaker_name} "
        f"(use naturally, don't recite them):\n{lines}"
    )


def _nvidia_json(messages: list[dict]) -> dict | None:
    key = (getattr(config, "LOCAL_LLM_API_KEY", None) or "").strip()
    if not key:
        import os
        key = (os.getenv("NVIDIA_API_KEY") or "").strip()
    if not key:
        print("[newchamp] reflect skip — no NVIDIA_API_KEY")
        return None
    base = (getattr(config, "LOCAL_LLM_BASE_URL", "") or "https://integrate.api.nvidia.com/v1").rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    model = getattr(config, "MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
    payload = {
        "model": model,
        "messages": list(messages) + [{
            "role": "user",
            "content": "Respond with valid JSON only. No markdown.",
        }],
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 512,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[newchamp] reflect NVIDIA failed: {e}")
        return None
    content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = _JSON_RE.search(content)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _reflect(
    speaker_id: str,
    speaker_name: str,
    user_text: str,
    assistant_text: str,
    known: list[dict[str, Any]],
) -> None:
    name = speaker_name or "them"
    bot = getattr(config, "HALMINI_SELF_NAME", None) or "Luna"
    known_block = ""
    if known:
        known_block = (
            f"Already known about {name}:\n"
            + "\n".join(f"- {m['content']}" for m in known)
            + "\n\n"
        )
    system = (
        f"You maintain a companion named {bot}. Analyze the exchange and reply with JSON:\n"
        f'{{"facts": ["..."]}}\n\n'
        f'"facts": durable facts about {name} themselves (never traits they assign to {bot}): '
        f"preferences, plans, relationships, life details, things they asked to remember. "
        f"Each fact is a short standalone sentence starting with their name. "
        f"Skip small talk, one-off questions, and anything already known. Empty list if nothing new."
    )
    user = (
        f"{known_block}"
        f"Exchange:\n{name}: {user_text}\n{bot}: {assistant_text}"
    )
    result = _nvidia_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])
    facts = []
    if isinstance(result, dict) and isinstance(result.get("facts"), list):
        facts = [
            f.strip() for f in result["facts"]
            if isinstance(f, str) and f.strip()
        ][:5]
    saved = remember_facts(speaker_id, facts)
    if saved:
        print(f"[newchamp] learned {len(saved)} fact(s) about {name}: {saved}")


def learn_exchange(
    *,
    speaker_id: str,
    speaker_name: str,
    user_text: str,
    assistant_text: str,
    conversation: str = "luna",
    background: bool = True,
) -> None:
    """After every HalMini or NVIDIA reply — extract + store facts (each time)."""
    if not enabled():
        return
    uid = (speaker_id or "").strip()
    if not uid:
        uid = "local"
    ut = (user_text or "").strip()
    at = (assistant_text or "").strip()
    if not ut or not at:
        return
    _ensure()
    from server.newchamp import store

    # Short-term history (new champ style)
    try:
        store.add_message(
            conversation,
            role="user",
            content=ut,
            user_id=uid,
            name=speaker_name or uid,
        )
        store.add_message(conversation, role="assistant", content=at)
    except Exception as e:
        print(f"[newchamp] history save failed: {type(e).__name__}: {e}")

    known = recall(uid, ut)

    def _job() -> None:
        try:
            _reflect(uid, speaker_name or uid, ut, at, known)
        except Exception as e:
            print(f"[newchamp] learn failed: {type(e).__name__}: {e}")

    if not background:
        _job()
        return

    def _run() -> None:
        with _chain_lock:
            _job()

    threading.Thread(target=_run, name="newchamp-learn", daemon=True).start()
