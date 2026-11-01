import json
from types import SimpleNamespace

import pytest

from tools.convo_eval.paragraphs import CORPUS, Paragraph, Probe, load_cases, main, run, score


def reply(text, status=None, intent=None):
    return SimpleNamespace(text=text, trace={"status": status, "intent": intent})


def test_corpus_has_real_sources_and_independent_gold():
    cases = load_cases()
    assert len(cases) >= 60
    assert len({case.id for case in cases}) == len(cases)
    assert len({case.text for case in cases}) == len(cases)
    assert {case.category for case in cases} == {"wikipedia", "news", "wordnet", "conversation"}
    assert sum(case.category != "conversation" for case in cases) >= 60
    assert all(case.metadata.get("url") for case in cases if case.category != "conversation")
    assert all(case.metadata.get("license") for case in cases)
    assert sum(len(case.probes) for case in cases) >= 100


@pytest.mark.parametrize("text,status,probe,expected", [
    ("Howard lives in Ohio.", "yes", Probe("Where?", ("Ohio",)), []),
    ("I don't know whether Howard lives in Ohio.", "unknown", Probe("Where?", ("Ohio",)), ["not-an-answer"]),
    ("No, I don't know.", "unknown", Probe("Does he?", status="no"), ["wrong-status"]),
    ("I know it.", None, Probe("Does he?", status="no"), ["wrong-status"]),
    ("Ohio.", None, Probe("Where?", ("Oh",)), ["missing:Oh"]),
    ("Unknown.", "unknown", Probe("Does he?", status="unknown"), []),
    ("He lives in Boston, not Denver.", "yes", Probe("Where?", ("Boston",), none_of=("Denver",)), ["forbidden:Denver"]),
])
def test_scoring_rejects_echoes_substrings_and_wrong_status(text, status, probe, expected):
    assert score(reply(text, status), probe) == expected


def test_runner_fresh_memory_sources_skips_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("HALMINI_WEB", "original")
    memories, calls = [], []
    class Reader:
        def __init__(self, repo):
            self.repo = repo
            memories.append(repo)
            assert repo.conn.execute("SELECT COUNT(*) FROM assertions").fetchone()[0] == 0
        def digest(self, text, source, confidence):
            calls.append((text, source, confidence))
            return SimpleNamespace(facts=[123], skipped=[("hard clause", "unsupported")])
        def handle(self, text, speaker):
            return reply("Dayton.", "yes")
    case = Paragraph("example", "conversation", "Jen lives in Dayton.", "said:Evaluator", (Probe("Where?", ("Dayton",)),))
    report = run([case, case], Reader)
    assert report["summary"]["passed_cases"] == 2
    assert report["summary"]["skipped_clauses"] == 2
    assert calls == [(case.text, case.source, 0.8)] * 2
    assert memories[0] is not memories[1]
    import os
    assert os.environ["HALMINI_WEB"] == "original"
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        memories[0].conn.execute("SELECT 1")


def test_missing_digest_is_reported_not_silently_substituted():
    class NotReady:
        def __init__(self, repo): pass
        def handle(self, *args, **kwargs):
            raise AssertionError("must not replace digest with handle")
    report = run(load_cases()[:1], NotReady)
    assert report["summary"]["blocked_cases"] == 1
    assert not report["results"][0]["passed"]
    assert not report["results"][0]["probes"]


def test_prior_dictionary_answers_do_not_count_without_digestion():
    class EmptyReader:
        def __init__(self, repo): pass
        def digest(self, *args, **kwargs):
            return SimpleNamespace(facts=[], skipped=[])
        def handle(self, *args, **kwargs):
            return reply("Dayton.", "yes")
    case = Paragraph("empty", "conversation", "Jen lives in Dayton.", "said:Evaluator", (Probe("Where?", ("Dayton",)),))
    report = run([case], EmptyReader)
    assert "no-digested-facts" in report["results"][0]["probes"][0]["issues"]


def test_corpus_schema_rejects_duplicate_ids(tmp_path):
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    data["cases"] = data["cases"][:1] * 2
    file = tmp_path / "bad.json"
    file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_cases(file)


def test_cli_validates_limit():
    with pytest.raises(SystemExit):
        main(["--limit", "0"])
