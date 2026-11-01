"""Run a real human dialogue dataset through Adam and sort what goes wrong into edge-case buckets.

Dataset: DailyDialog (roskoN/dailydialog on Hugging Face): everyday two-person conversations, each line
tagged with its act (1 inform, 2 question, 3 directive, 4 commissive) and emotion. Speaker A's lines are
said to Adam, in order, as one conversation; Adam takes speaker B's place. Every reply is checked:

  crash         an exception
  lost          he couldn't make sense of it ("couldn't pin down", "didn't catch", "who do you mean by")
  junk_fact     he stored a fact from a line that isn't a claim (a question, an order, small talk)
  wrong_kind    a question answered with "I've stored...", or a statement answered as a request
  routed        the request matcher re-read the line as something else (listed for review)
  repeat        the same reply twice in a row
  slow          over a second
The report groups the lines in each bucket by how they start, so the patterns that need rules show up
first. Usage:
  python -m tools.convo_eval.dataset_run --download 2000
  python -m tools.convo_eval.dataset_run --dialogues 300
"""

import argparse
import collections
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

DATA = os.path.join("data", "datasets", "dailydialog.jsonl")
API = "https://datasets-server.huggingface.co/rows?dataset=roskoN/dailydialog&config=full&split=train&offset={}&length=100"
ACTS = {1: "inform", 2: "question", 3: "directive", 4: "commissive"}
LOST = re.compile(r"couldn't pin down|didn't catch what|couldn't work out exactly|didn't follow|Who or what do you mean|"
                  r"I followed '|I'm not sure I followed|haven't understood|isn't clear to me", re.I)
STORED = re.compile(r"I've (?:added|recorded|stored|kept|noted|saved|made a note|taken that in)|"
                    r"This is what I've|Here's what I've|I learned \d", re.I)


def download(count: int) -> None:
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as out:
        for offset in range(0, count, 100):
            req = urllib.request.Request(API.format(offset), headers={"User-Agent": "HalMini-eval"})
            with urllib.request.urlopen(req, timeout=60) as r:
                rows = json.loads(r.read())["rows"]
            for row in rows:
                out.write(json.dumps(row["row"]) + "\n")
            print(f"downloaded {offset + len(rows)}", file=sys.stderr)


def clean(line: str) -> str:
    line = re.sub(r"\s+([,.!?;:'])", r"\1", line.strip())
    return re.sub(r"\s*’\s*", "'", line).replace(" n't", "n't")


def run(limit: int, report_path: str, only_failed: bool = False) -> None:
    os.environ["HALMINI_WEB"] = "0"
    sys.path.insert(0, os.getcwd())
    from db.repository import Repository
    from app.conversation import Conversation
    buckets: dict[str, list] = collections.defaultdict(list)
    turns = 0
    started = time.time()
    with open(DATA, encoding="utf-8") as f:
        dialogues = [json.loads(l) for _, l in zip(range(limit), f)]
    keep = None
    if only_failed and os.path.exists(report_path):  # just the conversations that had a problem last time
        old = json.load(open(report_path, encoding="utf-8"))["cases"]
        keep = {c["dialogue"] for cases in old.values() for c in cases if "dialogue" in c}
        print(f"re-running {len(keep)} conversations that had failures", file=sys.stderr)
    for n, d in enumerate(dialogues):
        if keep is not None and n not in keep:
            continue
        convo = Conversation(Repository(os.path.join(tempfile.mkdtemp(), "t.db")), speaker="Travis", self_name="Adam")
        convo.curiosity.rng.random = lambda: 1.0
        previous = None
        for i in range(0, len(d["utterances"]), 2):  # speaker A's lines; Adam is speaker B
            said = clean(d["utterances"][i])
            act = ACTS.get(d["acts"][i], "?")
            turns += 1
            t0 = time.perf_counter()
            try:
                reply = convo.handle(said, speaker="Travis")
            except Exception as exc:
                buckets["crash"].append({"said": said, "act": act, "error": repr(exc)[:200]})
                continue
            took = time.perf_counter() - t0
            text, trace = reply.text, reply.trace
            case = {"said": said, "act": act, "reply": text[:240], "dialogue": n}
            if LOST.search(text):
                buckets["lost"].append(case)
            if STORED.search(text) and (act in ("question", "directive") or said.rstrip().endswith("?")
                                        or re.match(r"^\W*(?:well|oh|yes|no|ok|okay|sure|thanks|hello|hi)\b", said, re.I)):
                buckets["junk_fact"].append(case)
            if said.rstrip().endswith("?") and STORED.search(text):
                buckets["wrong_kind"].append(case)
            if trace.get("routed"):
                buckets["routed"].append({**case, "as": trace["routed"]["as"]})
            if previous and text == previous:
                buckets["repeat"].append(case)
            if took > 1.0:
                buckets["slow"].append({**case, "seconds": round(took, 2)})
            previous = text
        if n % 50 == 0:
            print(f"{n}/{len(dialogues)} dialogues, {turns} turns, {time.time() - started:.0f}s", file=sys.stderr)
    summary = {"dialogues": len(dialogues), "turns": turns, "seconds": round(time.time() - started),
               "counts": {k: len(v) for k, v in sorted(buckets.items(), key=lambda kv: -len(kv[1]))}}
    patterns = {}
    for name, cases in buckets.items():
        starts = collections.Counter(" ".join(re.findall(r"[a-z']+", c["said"].lower())[:2]) for c in cases)
        patterns[name] = starts.most_common(25)
    with open(report_path, "w", encoding="utf-8") as out:
        json.dump({"summary": summary, "patterns": patterns, "cases": buckets}, out, indent=1)
    print(json.dumps(summary, indent=1))
    for name, common in patterns.items():
        print(f"\n{name}: most common openings: " + ", ".join(f"'{p}' x{c}" for p, c in common[:12]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", type=int)
    ap.add_argument("--dialogues", type=int, default=300)
    ap.add_argument("--failed", action="store_true", help="only re-run conversations that failed last time")
    ap.add_argument("--report", default=os.path.join("data", "datasets", "dailydialog_report.json"))
    args = ap.parse_args()
    if args.download:
        download(args.download)
    else:
        run(args.dialogues, args.report, args.failed)
