"""Runs the Node sidecar as a child process and exchanges JSON lines with it."""

import json
import os
import queue
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

SIDECAR_DIR = Path(__file__).resolve().parent.parent / "sidecar"
_EOF = object()


class SidecarBridge:
    def __init__(self, node: str = "node", script: Path = SIDECAR_DIR / "index.js",
                 env: dict[str, str] | None = None) -> None:
        self.command = [node, str(script)]
        self.env = {**os.environ, **(env or {})}
        self.inbox: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if not (SIDECAR_DIR / "node_modules").exists():
            raise RuntimeError(f"Sidecar dependencies missing: run `npm install` in {SIDECAR_DIR}")
        self.proc = subprocess.Popen(
            self.command, cwd=SIDECAR_DIR, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=None, text=True, encoding="utf-8", bufsize=1,  # sidecar logs go straight to our stderr
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.inbox.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        self.inbox.put(_EOF)

    def send(self, message: dict) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.write(json.dumps(message) + "\n")
                self.proc.stdin.flush()

    def messages(self) -> Iterator[dict]:
        while (item := self.inbox.get()) is not _EOF:
            yield item

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()  # sidecar leaves voice channels and exits on stdin close
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
