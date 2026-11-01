"""Backend seam for the memory subsystem — NVIDIA NIM (OpenAI-compatible).

  complete_json(messages, schema, model) -> a `schema` instance
  complete_text(messages, model)         -> str

Uses the same NIM endpoint / key as Luna chat. No Ollama.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Type, TypeVar

from pydantic import BaseModel

from server.config import (
    LOCAL_LLM_API_KEY,
    LOCAL_LLM_BASE_URL,
    LOCAL_LLM_THINK,
    MEMORY_LOCAL_BASE_URL,
)

T = TypeVar("T", bound=BaseModel)

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _api_root() -> str:
    base = (MEMORY_LOCAL_BASE_URL or LOCAL_LLM_BASE_URL or "").rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _api_key() -> str:
    key = (LOCAL_LLM_API_KEY or "").strip()
    if not key or key.lower() in ("ollama", "local", "env:nvidia_api_key"):
        key = (os.getenv("NVIDIA_API_KEY") or "").strip()
    return key


def _parse_json_payload(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("[mem-llm] empty NVIDIA reply")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


def _nvidia_chat(messages: list[dict], model: str, *, want_json: bool = False) -> str:
    """Non-streaming OpenAI-compatible chat.completions (NVIDIA NIM)."""
    key = _api_key()
    if not key:
        raise RuntimeError(
            "[mem-llm] NVIDIA_API_KEY missing — set it in .env (no Ollama fallback)"
        )
    url = f"{_api_root()}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.3,
        "max_tokens": 1024,
        "top_p": 0.95,
        "chat_template_kwargs": {"enable_thinking": bool(LOCAL_LLM_THINK)},
    }
    if want_json:
        # Soft hint — Nemotron may ignore; we still parse from content.
        payload["response_format"] = {"type": "json_object"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(
            f"[mem-llm] NVIDIA HTTP {e.code} at {url}: {detail}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"[mem-llm] NVIDIA unreachable at {url}: {e}"
        ) from e

    choices = body.get("choices") or []
    if not choices:
        raise ValueError(f"[mem-llm] NVIDIA returned no choices: {body!r}"[:300])
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if content:
        return content
    # Never use reasoning_content as the spoken/memory payload.
    raise ValueError("[mem-llm] NVIDIA returned empty content (thinking still on?)")


def complete_json(
    messages: list[dict],
    schema: Type[T],
    backend: str,
    model: str,
) -> T:
    """Return a validated `schema` instance from NVIDIA NIM."""
    if backend != "local":
        raise ValueError(
            f"[mem-llm] backend={backend!r} refused — use backend='local' (NVIDIA NIM)"
        )
    print(f"[mem-llm] complete_json backend='nvidia' model={model!r} schema={schema.__name__}")
    schema_hint = json.dumps(schema.model_json_schema())
    msgs = list(messages) + [{
        "role": "user",
        "content": (
            f"Reply with ONLY a JSON object matching this schema "
            f"(no markdown):\n{schema_hint}"
        ),
    }]
    raw = _nvidia_chat(msgs, model, want_json=True)
    return schema.model_validate(_parse_json_payload(raw))


def complete_text(messages: list[dict], backend: str, model: str) -> str:
    """Return a plain text completion from NVIDIA NIM."""
    if backend != "local":
        raise ValueError(
            f"[mem-llm] backend={backend!r} refused — use backend='local' (NVIDIA NIM)"
        )
    print(f"[mem-llm] complete_text backend='nvidia' model={model!r}")
    return _nvidia_chat(messages, model, want_json=False)


if __name__ == "__main__":
    from server.memory.schema import MemoryList

    assert "memories" in MemoryList.model_json_schema()["properties"]
    try:
        complete_json([], MemoryList, "openai", "x")
        raise SystemExit("[mem-llm.__main__] FAIL — openai label should be refused")
    except ValueError:
        pass
    print("[mem-llm.__main__] OK — schema serialises, wrong backend refused (no network)")
