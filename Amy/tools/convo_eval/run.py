"""Replay conversations through a fresh bot and report coherence and correctness problems.

    python -m tools.convo_eval.run                       # archive + 1,000 generated edge cases
    python -m tools.convo_eval.run --only archive --limit 200

Every conversation runs against its own throwaway in-memory database, so the real one is untouched.
Writes tools/convo_eval/report.json and prints a summary grouped by issue.
"""

import argparse
import json
import random
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path

from app.conversation import Conversation
from db.repository import Repository
from nlp.wordnet import WordNet
from tools.convo_eval import archive, edge_cases
from tools.convo_eval.checks import conversation_issues, reply_issues

HERE = Path(__file__).resolve().parent
ARCHIVE = Path("C:/Bots/export-2026-10-30")
SLOW_MS = 1500


def replay(name: str, turns: list, wordnet: WordNet, self_name: str = "Adam") -> dict:
    repo = Repository(":memory:")
    convo = Conversation(repo, self_name=self_name, wordnet=wordnet, curious=True)
    convo.social.rng.seed(7)
    results, replies = [], []
    try:
        for turn in turns:
            speaker, text = turn[0], turn[1]
            expect = turn[2] if len(turn) > 2 else None
            started = time.perf_counter()
            entry = {"speaker": speaker, "said": text}
            try:
                reply = convo.handle(text, speaker=speaker)
                entry.update(reply=reply.text, status=reply.trace.get("status"), intent=reply.trace.get("intent"))
                issues = reply_issues(text, reply.text, reply.trace)
            except Exception as exc:  # a crash is the worst incoherence
                entry.update(reply=None, error=f"{type(exc).__name__}: {exc}", tb=traceback.format_exc(limit=4))
                issues = ["crash"]
            entry["ms"] = round((time.perf_counter() - started) * 1000, 1)
            if entry["ms"] > SLOW_MS:
                issues.append("slow")
            if expect is not None and entry.get("reply") is not None:
                if not expect(entry["reply"], entry.get("status")):
                    issues.append("wrong-answer")
                    entry["expected"] = getattr(expect, "description", "")
            entry["issues"] = issues
            results.append(entry)
            replies.append(entry.get("reply") or "")
    finally:
        repo.close()
    return {"name": name, "turns": results, "issues": conversation_issues(replies)}


def main() -> None:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["archive", "edge", "all"], default="all")
    ap.add_argument("--limit", type=int, default=0, help="max turns per archived conversation (0 = all)")
    ap.add_argument("--edge", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    wordnet = WordNet()
    suites = []
    if args.only in ("archive", "all"):
        for name, turns in archive.load_all(ARCHIVE):
            suites.append((name, turns[: args.limit] if args.limit else turns))
    if args.only in ("edge", "all"):
        suites += edge_cases.generate(args.edge, random.Random(args.seed))

    started = time.perf_counter()
    reports = [replay(name, turns, wordnet) for name, turns in suites]
    elapsed = time.perf_counter() - started

    counts, examples = Counter(), defaultdict(list)
    total = 0
    for rep in reports:
        for issue in rep["issues"]:
            counts[issue] += 1
            examples[issue].append({"conversation": rep["name"]})
        for t in rep["turns"]:
            total += 1
            for issue in t["issues"]:
                counts[issue] += 1
                if len(examples[issue]) < 25:
                    examples[issue].append({"conversation": rep["name"], "said": t["said"][:200],
                                            "reply": (t.get("reply") or t.get("error") or "")[:400],
                                            **({"expected": t["expected"]} if "expected" in t else {})})
    bad_turns = sum(1 for rep in reports for t in rep["turns"] if t["issues"])
    summary = {"conversations": len(reports), "turns": total, "turns_with_issues": bad_turns,
               "clean_rate": round(1 - bad_turns / max(1, total), 4), "seconds": round(elapsed, 1),
               "issues": dict(counts.most_common())}
    (HERE / "report.json").write_text(json.dumps({"summary": summary, "examples": examples,
                                                  "reports": reports}, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    for issue, _ in counts.most_common():
        print(f"\n== {issue} ==")
        for ex in examples[issue][:4]:
            print(f"  [{ex['conversation']}] {ex.get('said', '')!r}\n     -> {ex.get('reply', '')!r}"
                  + (f"\n     expected: {ex['expected']}" if ex.get("expected") else ""))


if __name__ == "__main__":
    main()
