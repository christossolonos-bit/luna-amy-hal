"""Offline thinking subprocess. Reads live memory read-only; writes only its own queue DB."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time

from db.repository import Repository
from dialogue.self_model import SelfModel
from nlp.wordnet import WordNet
from reasoning.thoughts import ThinkingEngine, ThoughtStore
from reasoning.investigations import InvestigationController


def snapshot(path):
    repo=Repository(':memory:')
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=.2)) as source:
            source.backup(repo.conn,pages=128,sleep=.01)
        return repo
    except Exception:
        repo.close()
        raise


def work_once(memory_path,store,wordnet=None):
    repo=snapshot(memory_path)
    try:
        state=SelfModel(repo).current()
        # Low-energy/stress states reduce work, not the truth threshold.
        low=state['energy']<.35 or state['stress']>.7
        engine=ThinkingEngine(repo,store,wordnet=wordnet,max_steps=750 if low else 1500)
        chains=engine.cycle(batch=12 if low else 24,query_budget=3 if low else 8)
        InvestigationController(repo,store,wordnet=wordnet,max_queries=4 if low else 12).tick()
        return len(chains), 10.0 if low else 2.0
    finally:
        repo.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--memory',required=True,type=Path)
    parser.add_argument('--queue',required=True,type=Path)
    parser.add_argument('--wordnet',type=Path)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args(argv)
    if args.memory.resolve()==args.queue.resolve():
        parser.error('Thought queue must be separate from live fact memory')
    args.queue.parent.mkdir(parents=True,exist_ok=True)
    store=ThoughtStore(args.queue)
    stop=threading.Event()
    if not args.once:
        def watch_parent():
            sys.stdin.buffer.read()
            stop.set()
        threading.Thread(target=watch_parent,daemon=True).start()
    failed=False
    try:
        while not stop.is_set():
            started=time.monotonic()
            try:
                count,delay=work_once(args.memory,store,WordNet(args.wordnet) if args.wordnet else None)
                store.set('worker',{'alive':True,'last_cycle':time.time(),'connections':count,
                                    'cycle_seconds':round(time.monotonic()-started,3),'error':None})
            except Exception as exc:
                failed=True
                # A lock, schema update, or unavailable snapshot never terminates the bot.
                delay=5.0
                store.set('worker',{'alive':True,'last_cycle':time.time(),'error':type(exc).__name__})
            if args.once:break
            stop.wait(delay)
    finally:
        store.set('worker_stopped',time.time())
        store.close()
    return 1 if args.once and failed else 0


if __name__=='__main__':
    raise SystemExit(main())
