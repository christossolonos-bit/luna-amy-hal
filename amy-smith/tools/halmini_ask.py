"""Ask HalMini (Amy fact brain as Luna). Print JSON: {ok, text, reason}.

Used by amy-smith/src/halmini.js. Env:
  HALMINI_ROOT  — Amy/HalMini package (default: ../Amy under share)
  HALMINI_DB    — SQLite path
  HALMINI_SELF_NAME — Luna
  HALMINI_PERSONA — amy
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SHARE = Path(__file__).resolve().parents[2]  # HalMini-share
LUNA = SHARE / "Luna"
AMY = SHARE / "Amy"

# Prefer Luna venv imports for shared bridge, else put Amy on path.
sys.path.insert(0, str(LUNA))
sys.path.insert(0, str(AMY))

os.environ.setdefault("HALMINI_ENABLED", "1")
os.environ.setdefault("HALMINI_ROOT", str(AMY if (AMY / "app" / "conversation.py").is_file() else SHARE / "HalMini"))
os.environ.setdefault("HALMINI_DB", str(LUNA / "data" / "luna_amy.db"))
os.environ.setdefault("HALMINI_SELF_NAME", "Luna")
os.environ.setdefault("HALMINI_PERSONA", "amy")
os.environ.setdefault("RIKO_CHARACTER", "luna")


def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        text = sys.stdin.read().strip()
    speaker = os.environ.get("HALMINI_SPEAKER", "user")
    speaker_id = os.environ.get("HALMINI_SPEAKER_ID") or None
    try:
        from server.halmini_bridge import try_answer, teach

        if os.environ.get("HALMINI_MODE", "ask") == "teach":
            out = teach(text, speaker=speaker)
            print(json.dumps({"ok": bool(out), "text": out or "", "reason": "teach"}))
            return 0
        # try_answer accepts Discord-labeled text; pass speaker name when plain.
        hm = try_answer(text, speaker=speaker)
        print(json.dumps({"ok": bool(hm.ok), "text": hm.text or "", "reason": hm.reason or ""}))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "text": "", "reason": f"error:{type(e).__name__}:{e}"}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
