"""Local web dashboard: settings, state, and everything the bot has learned.

    python -m interfaces.dashboard            # http://127.0.0.1:8765

Read-only view of the same SQLite database the bot uses (safe to run alongside it; WAL mode
allows concurrent readers). Binds to localhost only. Settings can be edited here: they are written
back to .env, and the bot is restarted to apply them. Secret values are never sent to the page.
"""

import argparse
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app.config import load_config, load_dotenv
from db.repository import Repository
from dialogue import realizer
from dialogue.self_model import ADENOSINE_BASE, CHEMICALS, EVENTS, SelfModel

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "dashboard.html"
SECRET = re.compile(r"TOKEN|KEY|SECRET|PASSWORD", re.I)
GROUPS = [("Discord", ("DISCORD_", "HALMINI_NAME", "HALMINI_PREFIX", "HALMINI_TEXT")),
          ("Listening", ("HALMINI_LISTEN", "HALMINI_WAKE", "HALMINI_FOLLOW", "HALMINI_LEARN", "HALMINI_BARGE",
                         "HALMINI_SILENCE", "HALMINI_MIN_", "HALMINI_MAX_UTT")),
          ("Speech", ("WHISPER_", "TTS_", "FISH_", "PIPER_")),
          ("Engine", ("HALMINI_DB", "HALMINI_MAX_DEPTH", "HALMINI_INFERENCE"))]

# How each known setting is edited, and what it means. Anything not listed is plain text.
SELECTS = {
    "HALMINI_LISTEN_MODE": ["wake", "always"],
    "WHISPER_DEVICE": ["auto", "cpu", "cuda"],
    "TTS_ENGINE": ["fish", "piper", "none"],
    "FISH_LATENCY": ["normal", "balanced"],
}
NUMBERS = {
    "HALMINI_MAX_DEPTH": ("int", 1, 1000),
    "HALMINI_INFERENCE_BUDGET": ("int", 1, 10_000_000),
    "HALMINI_FOLLOW_UP_SECONDS": ("float", 0, 3600),
    "HALMINI_SILENCE_MS": ("int", 100, 60_000),
    "HALMINI_MIN_UTTERANCE_MS": ("int", 0, 60_000),
    "HALMINI_MAX_UTTERANCE_MS": ("int", 100, 600_000),
}
BOOLS = {"HALMINI_LEARN_OVERHEARD", "HALMINI_LISTEN_TO_BOTS", "HALMINI_BARGE_IN"}
HELP = {
    "DISCORD_TOKEN": "Bot token from discord.com/developers. Secret; never shown.",
    "HALMINI_NAME": "The name he answers to.",
    "HALMINI_PREFIX": "Command prefix, for example !",
    "HALMINI_TEXT_CHANNELS": "Channel ids where he answers without being mentioned (comma separated).",
    "HALMINI_LISTEN_MODE": "wake = answer only when named; always = answer everything he hears.",
    "HALMINI_WAKE_WORDS": "Extra sound-alikes for his name (comma separated).",
    "HALMINI_FOLLOW_UP_SECONDS": "Seconds after he stops talking when you can follow up without the wake word.",
    "HALMINI_LEARN_OVERHEARD": "Learn facts from conversation that isn't addressed to him.",
    "HALMINI_LISTEN_TO_BOTS": "Hear and answer other bots.",
    "HALMINI_BARGE_IN": "Talking over him cuts his reply off. Off = he finishes speaking.",
    "HALMINI_SILENCE_MS": "Silence that ends an utterance, in milliseconds.",
    "HALMINI_MIN_UTTERANCE_MS": "Ignore speech shorter than this, in milliseconds.",
    "HALMINI_MAX_UTTERANCE_MS": "Longest utterance sent to speech recognition, in milliseconds.",
    "WHISPER_MODEL": "tiny.en / base.en / small.en / medium.en.",
    "WHISPER_DEVICE": "auto, cpu or cuda.",
    "WHISPER_COMPUTE_TYPE": "Whisper compute type; default is fine.",
    "TTS_ENGINE": "fish = Fish Audio cloud; piper = local; none = text replies only.",
    "FISH_API_KEY": "Fish Audio API key. Secret; never shown.",
    "FISH_VOICE_ID": "Reference id of the Fish Audio voice; empty = default voice.",
    "FISH_MODEL": "Fish Audio model, for example s2.1-pro-free.",
    "FISH_LATENCY": "normal = best quality; balanced = lower latency.",
    "PIPER_VOICE": "Local Piper voice .onnx file (only used when TTS_ENGINE=piper).",
    "HALMINI_DB": "SQLite database path. Restart the dashboard too if you change it.",
    "HALMINI_MAX_DEPTH": "Reasoning depth limit.",
    "HALMINI_INFERENCE_BUDGET": "Reasoning step budget.",
}


def setting_kind(key: str, value: str) -> dict:
    """UI metadata for one key: how to edit it and how to validate it."""
    if SECRET.search(key):
        return {"kind": "secret"}
    if key in SELECTS:
        return {"kind": "select", "options": SELECTS[key]}
    if key in NUMBERS:
        kind, low, high = NUMBERS[key]
        return {"kind": kind, "min": low, "max": high}
    if key in BOOLS or value.lower() in ("true", "false"):
        return {"kind": "bool"}
    return {"kind": "text"}


def settings(path: Path | None = None) -> list[dict]:
    """.env entries with secret values replaced by set/not set."""
    path = path or ROOT / ".env"
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        group = next((g for g, prefixes in GROUPS if key.startswith(prefixes)), "Other")
        info = setting_kind(key, value)
        entry = {"key": key, "group": group, "help": HELP.get(key), **info}
        if info["kind"] == "secret":
            entry.update(secret=True, value="", is_set=bool(value))
        else:
            entry.update(secret=False, value=value)
        out.append(entry)
    return out


def _check_value(key: str, raw, info: dict) -> tuple[str | None, str | None]:
    """Validate one submitted value. Returns (cleaned value, error)."""
    value = "" if raw is None else str(raw).strip()
    if "\n" in value or "\r" in value:
        return None, "must be a single line"
    kind = info["kind"]
    if kind == "secret":
        return value, None
    if kind == "bool":
        if value.lower() in ("true", "on", "yes", "1"):
            return "true", None
        if value.lower() in ("false", "off", "no", "0", ""):
            return "false", None
        return None, "must be true or false"
    if kind in ("int", "float"):
        try:
            number = int(value) if kind == "int" else float(value)
        except ValueError:
            return None, "must be a number"
        if not info["min"] <= number <= info["max"]:
            return None, f"must be between {info['min']} and {info['max']}"
        return (str(number) if kind == "int" else value), None
    if kind == "select" and value not in info["options"]:
        return None, "must be one of: " + ", ".join(info["options"])
    return value, None


def update_settings(values: dict, clear: list[str], path: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Update .env in place, preserving comments and order. Returns (updates, errors)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return {}, {"_": "There is no .env file in the project folder."}
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()

    current = {}
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            current[key.strip()] = value.strip()

    updates: dict[str, str] = {}
    errors: dict[str, str] = {}
    for key, raw in values.items():
        if key not in current:
            errors[key] = "not a setting in .env"
            continue
        value, error = _check_value(key, raw, setting_kind(key, current[key]))
        if error:
            errors[key] = error
        else:
            updates[key] = value
    for key in clear:
        if key not in current:
            errors[key] = "not a setting in .env"
        elif setting_kind(key, current[key])["kind"] != "secret":
            errors[key] = "only secret values can be cleared"
        else:
            updates[key] = ""
    if not updates and not errors:
        return {}, {}
    if errors:
        return {}, errors

    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        before = line.partition("=")[0]
        key = before.strip()
        out.append(f"{before}={updates[key]}" if key in updates else line)
    text = "\n".join(out) + ("\n" if original.endswith("\n") else "")
    temp = path.with_name(".env.tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)
    return updates, {}



def bot_status() -> dict:
    log = ROOT / "data" / "bot.log"
    if not log.exists():
        return {"online": False, "detail": "no bot log yet"}
    text = log.read_text(encoding="utf-8", errors="replace")
    logged_in = re.findall(r"logged in as (\S+)", text)
    age = time.time() - log.stat().st_mtime
    return {"online": bool(logged_in), "account": logged_in[-1] if logged_in else None,
            "log_age_seconds": round(age)}


def when(ts: str | None) -> str | None:
    return ts and ts.replace(" ", "T") + "Z"


def snapshot(repo: Repository, self_name: str) -> dict:
    conn = repo.conn
    self_id = repo.find_entity(self_name)
    realizer.set_perspective(None, None, None, None)  # third person: names, not I/you
    count = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731

    facts = []
    rows = conn.execute(
        "SELECT a.*, u.speaker, u.text AS said FROM assertions a LEFT JOIN utterances u "
        "ON u.id = a.source_utterance_id ORDER BY a.id DESC LIMIT 1000").fetchall()
    for r in rows:
        text = realizer.realize_assertion(repo, r)
        if text is None:
            continue
        pred = r["predicate"]
        facts.append({"id": r["id"], "text": text, "kind": pred[4:] if pred.startswith("rel:") else pred,
                      "polarity": bool(r["polarity"]), "confidence": round(r["confidence"], 2),
                      "status": r["status"], "speaker": r["speaker"] or "built-in", "said": r["said"],
                      "when": when(r["created_at"])})

    # Knowledge graph: named things, unnamed things, and kinds, linked by active claims.
    nodes, edges = {}, []
    none = repo.none_concept()

    def node(kind: str, ident: int) -> str:
        key = f"{kind}{ident}"
        if key not in nodes:
            if kind == "c":
                nodes[key] = {"id": key, "label": repo.concept_lemma(ident), "type": "kind"}
            else:
                e = repo.entity_row(ident)
                is_self = ident == self_id
                nodes[key] = {"id": key, "label": realizer.entity_text(repo, ident) if not e["proper"] else e["name"],
                              "type": "self" if is_self else ("named" if e["proper"] else "thing")}
        return key

    for r in conn.execute("SELECT * FROM assertions WHERE status = 'active' ORDER BY id DESC LIMIT 600"):
        if r["obj_type"] == "c" and r["obj_id"] == none:
            target = None
            loc = conn.execute("SELECT value_type, value_id FROM assertion_args WHERE assertion_id = ? "
                               "AND value_type IN ('e','c') LIMIT 1", (r["id"],)).fetchone()
            if loc:
                target = node(loc[0], loc[1])
        else:
            target = node(r["obj_type"], r["obj_id"])
        if target is None:
            continue
        pred = r["predicate"]
        label = {"instance_of": "is a", "subclass_of": "is a kind of", "has_property": "is"}.get(pred, pred[4:])
        edges.append({"source": node(r["subj_type"], r["subj_id"]), "target": target, "label": label,
                      "polarity": bool(r["polarity"]), "text": realizer.realize_assertion(repo, r)})

    verbs = conn.execute(
        "SELECT predicate, COUNT(*) FROM assertions WHERE status = 'active' GROUP BY predicate "
        "ORDER BY COUNT(*) DESC").fetchall()

    rules = [{"id": rule["id"], "text": realizer.rule_text(repo, rule), "confidence": rule["confidence"]}
             for rule in repo.active_rules()]

    turns = [{"role": r["role"], "speaker": r["speaker"], "text": r["text"], "when": when(r["created_at"])}
             for r in conn.execute(
                 "SELECT t.role, t.text, t.created_at, u.speaker FROM dialogue_turns t "
                 "JOIN utterances u ON u.id = t.utterance_id ORDER BY t.id DESC LIMIT 300")]

    questions = []
    for r in conn.execute("SELECT q.*, u.speaker FROM questions q LEFT JOIN utterances u ON u.id = q.utterance_id "
                          "WHERE q.status = 'open' ORDER BY q.id DESC LIMIT 200"):
        frame = json.loads(r["frame_json"])
        if "unparsed" in frame:
            questions.append({"kind": "didn't understand", "text": frame["unparsed"], "detail": frame.get("reason"),
                              "speaker": r["speaker"], "when": when(r["created_at"])})
        else:
            said = conn.execute("SELECT text FROM utterances WHERE id = ?", (r["utterance_id"],)).fetchone()
            questions.append({"kind": "couldn't answer", "text": said[0] if said else "?", "detail": None,
                              "speaker": r["speaker"], "when": when(r["created_at"])})

    corrections = []
    for r in conn.execute("SELECT c.*, u.speaker FROM corrections c LEFT JOIN utterances u ON u.id = c.utterance_id "
                          "ORDER BY c.id DESC LIMIT 200"):
        if r["assertion_id"]:
            what = realizer.realize_assertion(repo, repo.assertion(r["assertion_id"]))
        else:
            what = realizer.rule_text(repo, repo.rule(r["rule_id"]))
        corrections.append({"retracted": what, "note": r["note"], "speaker": r["speaker"], "when": when(r["created_at"])})

    model = SelfModel(repo)
    history = [{"t": ts, **json.loads(js)} for ts, js in conn.execute(
        "SELECT ts, snapshot_json FROM self_state_log WHERE ts >= ? ORDER BY ts", (time.time() - 48 * 3600,))]

    return {
        "generated": time.time(),
        "bot": {**bot_status(), "name": self_name,
                "aliases": [r[0] for r in conn.execute("SELECT alias FROM aliases WHERE entity_id = ?", (self_id,))]
                if self_id else []},
        "counts": {
            "facts": count("SELECT COUNT(*) FROM assertions WHERE status = 'active' AND source_utterance_id IS NOT NULL"),
            "rules": count("SELECT COUNT(*) FROM learned_rules WHERE status = 'active'"),
            "things": count("SELECT COUNT(*) FROM entities"),
            "kinds": count("SELECT COUNT(*) FROM concepts WHERE lemma != '_'"),
            "verbs": count("SELECT COUNT(*) FROM lexemes WHERE pos = 'verb'"),
            "people": count("SELECT COUNT(*) FROM people WHERE replied > 0 AND is_bot = 0")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='people'").fetchone() else 0,
            "messages": count("SELECT COUNT(*) FROM utterances"),
            "open_questions": count("SELECT COUNT(*) FROM questions WHERE status = 'open'"),
            "corrections": count("SELECT COUNT(*) FROM corrections"),
        },
        "state": model.snapshot(),
        "emotion_events": [{"t": r[0], "event": r[1], "detail": r[2], "emotions": json.loads(r[3])}
                           for r in conn.execute("SELECT ts,event,detail,emotions_json FROM emotion_events "
                                                 "ORDER BY id DESC LIMIT 100")],
        "baselines": {n: b for n, (b, _) in CHEMICALS.items()},
        "half_lives_min": {n: round(h / 60) for n, (_, h) in CHEMICALS.items()},
        "events": EVENTS,
        "adenosine_base": ADENOSINE_BASE,
        "history": history,
        "facts": facts,
        "kinds": [{"kind": p[4:] if p.startswith("rel:") else {"instance_of": "is a", "subclass_of": "kind of",
                                                                "has_property": "property"}.get(p, p), "count": n}
                  for p, n in verbs],
        "graph": {"nodes": list(nodes.values()), "edges": edges},
        "rules": rules,
        "turns": turns,
        "questions": questions,
        "corrections": corrections,
        "settings": settings(),
    }


class Handler(BaseHTTPRequestHandler):
    db_path = "data/halmini.db"
    self_name = "Hal"

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_ok(self) -> bool:
        """Reject cross-site POSTs. Same-origin browser requests match Host exactly."""
        origin = self.headers.get("Origin")
        if not origin:
            return True  # non-browser clients (curl) don't send Origin
        parts = urlsplit(origin)
        return parts.scheme in ("http", "https") and parts.netloc == self.headers.get("Host", "")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/snapshot":
            repo = Repository(self.db_path)
            try:
                data = snapshot(repo, self.self_name)
            finally:
                repo.close()
            self._send(json.dumps(data, default=str).encode("utf-8"), "application/json")
        else:
            self._send(b"not found", "text/plain", 404)

    def do_POST(self) -> None:
        if self.path != "/api/settings":
            self._send(b"not found", "text/plain", 404)
            return
        if not self._origin_ok() or self.headers.get_content_type() != "application/json":
            self._send(json.dumps({"ok": False, "error": "bad request"}).encode("utf-8"),
                       "application/json", 403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 100_000:
            self._send(json.dumps({"ok": False, "error": "bad request"}).encode("utf-8"),
                       "application/json", 400)
            return
        try:
            payload = json.loads(self.rfile.read(length))
            values = payload.get("values", {})
            clear = payload.get("clear", [])
            if not isinstance(values, dict) or not isinstance(clear, list):
                raise ValueError
            values = {str(key): value for key, value in values.items()}
            clear = [str(key) for key in clear]
        except (ValueError, AttributeError):
            self._send(json.dumps({"ok": False, "error": "bad request"}).encode("utf-8"),
                       "application/json", 400)
            return
        updates, errors = update_settings(values, clear)
        if errors:
            self._send(json.dumps({"ok": False, "errors": errors}).encode("utf-8"),
                       "application/json", 400)
            return
        self._send(json.dumps({"ok": True, "updated": list(updates), "settings": settings()}).encode("utf-8"),
                   "application/json")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser(description="HalMini dashboard")
    ap.add_argument("--port", type=int, default=int(os.environ.get("HALMINI_DASHBOARD_PORT", "8765")))
    args = ap.parse_args()
    Handler.db_path = load_config().db_path
    Handler.self_name = os.environ.get("HALMINI_NAME", "Adam")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"dashboard: http://127.0.0.1:{args.port}  (db: {Handler.db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
