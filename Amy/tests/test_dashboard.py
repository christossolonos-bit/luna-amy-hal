"""Dashboard settings editing: .env round-trip, validation, and secret handling."""

from pathlib import Path

import pytest

from interfaces.dashboard import settings, update_settings

ENV = """\
# Discord
DISCORD_TOKEN=old-token
HALMINI_NAME=Hal
HALMINI_PREFIX=!

# Listening
HALMINI_BARGE_IN=true
HALMINI_SILENCE_MS=900
HALMINI_LISTEN_MODE=wake

# Speech
TTS_ENGINE=fish
FISH_API_KEY=old-key
FISH_MODEL=s2.1-pro-free
"""


@pytest.fixture
def env(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(ENV, encoding="utf-8")
    return path


def entry(rows, key):
    return next(row for row in rows if row["key"] == key)


def test_settings_marks_kinds_groups_and_hides_secrets(env):
    rows = settings(env)

    token = entry(rows, "DISCORD_TOKEN")
    assert token["secret"] is True and token["value"] == "" and token["is_set"] is True
    assert token["group"] == "Discord"

    assert entry(rows, "HALMINI_NAME")["group"] == "Discord"
    assert entry(rows, "HALMINI_NAME")["kind"] == "text"

    barge = entry(rows, "HALMINI_BARGE_IN")
    assert (barge["kind"], barge["group"], barge["value"]) == ("bool", "Listening", "true")

    mode = entry(rows, "HALMINI_LISTEN_MODE")
    assert (mode["kind"], mode["options"]) == ("select", ["wake", "always"])

    silence = entry(rows, "HALMINI_SILENCE_MS")
    assert (silence["kind"], silence["min"], silence["max"]) == ("int", 100, 60_000)

    assert entry(rows, "TTS_ENGINE")["options"] == ["fish", "piper", "none"]
    assert entry(rows, "FISH_API_KEY")["secret"] is True


def test_update_preserves_comments_and_order(env):
    updates, errors = update_settings({"HALMINI_BARGE_IN": "false", "HALMINI_SILENCE_MS": "1200"}, [], env)

    assert errors == {} and updates == {"HALMINI_BARGE_IN": "false", "HALMINI_SILENCE_MS": "1200"}
    text = env.read_text(encoding="utf-8")
    assert text == ENV.replace("HALMINI_BARGE_IN=true", "HALMINI_BARGE_IN=false") \
                     .replace("HALMINI_SILENCE_MS=900", "HALMINI_SILENCE_MS=1200")


def test_update_can_replace_and_clear_secrets(env):
    updates, errors = update_settings({"FISH_API_KEY": "new-key"}, ["DISCORD_TOKEN"], env)

    assert errors == {}
    assert updates == {"FISH_API_KEY": "new-key", "DISCORD_TOKEN": ""}
    rows = settings(env)
    assert entry(rows, "FISH_API_KEY")["is_set"] is True
    assert entry(rows, "DISCORD_TOKEN")["is_set"] is False


def test_settings_http_response_does_not_echo_secrets(env, monkeypatch):
    import json
    import threading
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer
    from interfaces import dashboard

    monkeypatch.setattr(dashboard, "ROOT", env.parent)
    server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = HTTPConnection(*server.server_address)
    try:
        client.request("POST", "/api/settings", json.dumps({"values": {
            "FISH_API_KEY": "replacement-key", "DISCORD_TOKEN": "replacement-token"}}),
            {"Content-Type": "application/json"})
        response = client.getresponse()
        body = response.read().decode()
        assert response.status == 200
        assert all(secret not in body for secret in (
            "replacement-key", "replacement-token", "old-key", "old-token"))
        payload = json.loads(body)
        assert set(payload["updated"]) == {"FISH_API_KEY", "DISCORD_TOKEN"}
        assert entry(payload["settings"], "FISH_API_KEY")["is_set"]
        assert "FISH_API_KEY=replacement-key" in env.read_text(encoding="utf-8")
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("values, clear, key, match", [
    ({"HALMINI_BARGE_IN": "maybe"}, [], "HALMINI_BARGE_IN", "true or false"),
    ({"HALMINI_SILENCE_MS": "5"}, [], "HALMINI_SILENCE_MS", "between"),
    ({"HALMINI_SILENCE_MS": "soon"}, [], "HALMINI_SILENCE_MS", "number"),
    ({"HALMINI_LISTEN_MODE": "sometimes"}, [], "HALMINI_LISTEN_MODE", "one of"),
    ({"NOT_A_SETTING": "x"}, [], "NOT_A_SETTING", "not a setting"),
    ({}, ["HALMINI_NAME"], "HALMINI_NAME", "only secret"),
])
def test_update_rejects_bad_values_without_touching_the_file(env, values, clear, key, match):
    before = env.read_text(encoding="utf-8")
    updates, errors = update_settings(values, clear, env)
    assert updates == {}
    assert key in errors and match in errors[key]
    assert env.read_text(encoding="utf-8") == before
