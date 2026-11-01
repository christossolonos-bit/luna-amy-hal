"""Offline, isolated paragraph-digestion benchmark. Never uses Adam's live memory.

    python -m tools.convo_eval.paragraphs --report fixtures/paragraphs/report.json

Missing digest() is a reported integration blocker, not a passing test or a silent
fallback to handle(). Test utilities may inject a small conversation double.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Callable

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "paragraphs" / "cases.json"
KINDS = {"wikipedia", "news", "wordnet", "conversation"}


@dataclass(frozen=True)
class Probe:
    question: str
    all_of: tuple[str, ...] = ()
    any_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()
    status: str = "answer"  # answer | yes | no | unknown


@dataclass(frozen=True)
class Paragraph:
    id: str
    category: str
    text: str
    source: str
    probes: tuple[Probe, ...]
    confidence: float = 0.8
    metadata: dict = field(default_factory=dict)


def load_cases(path: Path | str = CORPUS) -> list[Paragraph]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != 1:
        raise ValueError("Unsupported paragraph corpus schema")
    cases, ids = [], set()
    for row in raw["cases"]:
        ident = row["id"]
        if ident in ids or not ident:
            raise ValueError(f"Duplicate/empty paragraph ID: {ident}")
        ids.add(ident)
        if row["category"] not in KINDS or not row["text"].strip() or not row["source"].strip():
            raise ValueError(f"Incomplete paragraph: {ident}")
        confidence = float(row.get("confidence", 0.8))
        if not 0 <= confidence <= 1:
            raise ValueError(f"Invalid confidence: {ident}")
        probes = []
        for q in row["probes"]:
            status = q.get("status", "answer")
            if status not in ("answer", "yes", "no", "unknown") or not q["question"].strip():
                raise ValueError(f"Invalid probe: {ident}")
            if not q.get("all_of") and not q.get("any_of") and status == "answer":
                raise ValueError(f"Probe needs an expected answer: {ident}")
            probes.append(Probe(q["question"], *(tuple(q.get(k, ())) for k in
                                              ("all_of", "any_of", "none_of")), status))
        if not probes:
            raise ValueError(f"Paragraph has no probes: {ident}")
        cases.append(Paragraph(ident, row["category"], row["text"], row["source"], tuple(probes),
                               confidence, row.get("metadata", {})))
    return cases


def _normal(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold().replace("’", "'")))


def _contains(text, phrase):
    return f" {_normal(phrase)} " in f" {_normal(text)} "


def score(reply, probe: Probe) -> list[str]:
    text = reply.text
    trace = getattr(reply, "trace", {}) or {}
    status = trace.get("status")
    if status is None:
        status = ("yes" if re.match(r"^\s*yes\b", text, re.I) else
                  "no" if re.match(r"^\s*no\b", text, re.I) else None)
    unknown = status == "unknown" or trace.get("intent") == "clarify" or bool(re.search(
        r"\b(?:I (?:don't|do not) know|I (?:have no|don't have) (?:information|evidence)|"
        r"not sure|couldn't (?:understand|follow)|which meaning)\b", text, re.I))
    issues = []
    if probe.status == "unknown":
        if not unknown:
            issues.append("expected-unknown")
    elif probe.status in ("yes", "no"):
        if status != probe.status or unknown:
            issues.append("wrong-status")
    elif unknown or status == "conflicting":
        issues.append("not-an-answer")
    if not text.strip():
        issues.append("empty-answer")
    issues += ["missing:" + p for p in probe.all_of if not _contains(text, p)]
    if probe.any_of and not any(_contains(text, p) for p in probe.any_of):
        issues.append("missing-alternative")
    issues += ["forbidden:" + p for p in probe.none_of if _contains(text, p)]
    return issues


@contextmanager
def offline_environment():
    # These are the existing web/news switches. Restore callers' environment.
    overrides = {"HALMINI_WEB": "0", "HALMINI_NEWS": "0"}
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run(cases: list[Paragraph], factory: Callable | None = None) -> dict:
    from app.conversation import Conversation
    from db.repository import Repository

    factory = factory or (lambda repo: Conversation(repo, speaker="Evaluator", self_name="Adam", curious=False))
    results, totals = [], Counter()
    started = time.perf_counter()
    with offline_environment():
        for case in cases:
            result = {"id": case.id, "category": case.category, "source": case.source,
                      "facts": [], "skipped": [], "probes": []}
            with tempfile.TemporaryDirectory(prefix="adam-paragraph-") as folder:
                repo = Repository(str(Path(folder) / "memory.db"))
                try:
                    convo = factory(repo)
                    digest = getattr(convo, "digest", None)
                    if not callable(digest):
                        result["blocked"] = "Conversation.digest is not implemented"
                        totals["blocked_cases"] += 1
                    else:
                        read = digest(case.text, source=case.source, confidence=case.confidence)
                        result["facts"] = list(read.facts)
                        result["skipped"] = [list(pair) for pair in read.skipped]
                        # Read-only membership check: dictionary knowledge alone must not
                        # receive credit when digest did not store the target information.
                        for probe in case.probes:
                            reply = convo.handle(probe.question, speaker="Evaluator")
                            issues = score(reply, probe)
                            if not read.facts and probe.status != "unknown":
                                issues.append("no-digested-facts")
                            result["probes"].append({"question": probe.question, "reply": reply.text,
                                                      "issues": issues, "passed": not issues})
                            totals["probes"] += 1
                            totals["passed_probes"] += not issues
                        totals["stored_facts"] += len(read.facts)
                        totals["skipped_clauses"] += len(read.skipped)
                except Exception as exc:
                    result["error"] = f"{type(exc).__name__}: {exc}"
                    totals["errors"] += 1
                finally:
                    repo.close()
            result["passed"] = bool(result["probes"]) and all(p["passed"] for p in result["probes"])
            totals["passed_cases"] += result["passed"]
            results.append(result)
    return {"schema": 1, "summary": {"cases": len(cases), **dict(totals),
            "seconds": round(time.perf_counter() - started, 3)}, "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--category", choices=sorted(KINDS))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    cases = [c for c in load_cases(args.corpus) if not args.category or c.category == args.category]
    if args.limit:
        cases = cases[:args.limit]
    report = run(cases)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    if any(r.get("blocked") for r in report["results"]):
        return 2
    return 0 if all(r["passed"] for r in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
