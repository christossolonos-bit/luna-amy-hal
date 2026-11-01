"""Text REPL. Runs with no Discord, no network, no language model."""

import argparse
import json
import os
import sys
from pathlib import Path

from app.config import load_config, load_dotenv
from app.conversation import Conversation
from db.repository import Repository
from dialogue.realizer import realize_assertion

HELP = """Talk to it in plain sentences; several at once is fine.
Commands:
  :as NAME     speak as someone else (e.g. another bot) from now on
  :teach FILE  feed a text file in, as the current speaker
  :why         show the proof trace for the last answer
  :debug       toggle the full pipeline trace after each reply
  :facts       list stored knowledge
  :open        list unresolved questions and sentences it couldn't understand
  :state       show its internal state variables
  :quit        exit"""


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # quotes in recalled text
    load_dotenv()
    config = load_config()
    ap = argparse.ArgumentParser(description="HalMini conversational reasoning engine")
    ap.add_argument("--db", default=config.db_path, help="SQLite database path")
    ap.add_argument("--speaker", default="user", help="who is talking")
    args = ap.parse_args(argv)

    repo = Repository(args.db)
    convo = Conversation(repo, speaker=args.speaker, self_name=os.environ.get("HALMINI_NAME", "Adam"), curious=True,
                         max_depth=config.max_depth,
                         budget=config.inference_budget)
    debug = False
    print(f"HalMini (db: {args.db}). Type :help for commands.")
    while True:
        try:
            line = input(f"{convo.state.speaker}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        command, _, rest = line.partition(" ")
        if command == ":quit":
            break
        if command == ":help":
            print(HELP)
            continue
        if command == ":debug":
            debug = not debug
            print(f"debug {'on' if debug else 'off'}")
            continue
        if command == ":as":
            convo.state.speaker = rest.strip() or "user"
            continue
        if command == ":why":
            trace = convo.state.last_trace or {}
            print(json.dumps(trace.get("proofs", "no proof for the last turn"), indent=2))
            continue
        if command == ":facts":
            for row in repo.all_assertions():
                text = realize_assertion(repo, row)
                if text:
                    conf = "" if row["confidence"] >= 1 else f"  (confidence {row['confidence']:.1f})"
                    print(f"  [{row['id']}] {text}{conf}")
            continue
        if command == ":state":
            print(json.dumps(convo.self_model.snapshot(), indent=2))
            continue
        if command == ":open":
            for row in repo.open_questions():
                print(f"  [{row['id']}] {row['frame_json']}")
            continue
        if command == ":teach":
            line = Path(rest.strip()).read_text(encoding="utf-8")
        reply = convo.handle(line)
        print(f"hal> {reply.text}")
        if debug:
            print(json.dumps(reply.trace, indent=2, default=str))
    repo.close()


if __name__ == "__main__":
    main()
