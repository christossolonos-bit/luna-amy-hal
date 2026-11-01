"""Read the bots' Markdown exports into conversations: [(conversation_id, [(speaker, text), ...])]."""

import re
from pathlib import Path

LINE = re.compile(r"^\[(?P<when>[^\]]+)\] \*\*(?P<speaker>[^*]+)\*\*: (?P<text>.*)$")


def load(path: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    conversations: list[tuple[str, list[tuple[str, str]]]] = []
    in_history = False
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("## "):
            in_history = raw.startswith("## Full chat history")
            continue
        if not in_history:
            continue
        if raw.startswith("### "):
            current = (f"{path.stem}/{raw[4:].strip()}", [])
            conversations.append(current)
            continue
        match = LINE.match(raw)
        if match and current is not None:
            current[1].append((match["speaker"].strip(), match["text"].strip()))
        elif current is not None and current[1] and raw.strip():
            speaker, text = current[1][-1]  # continuation of a multi-line message
            current[1][-1] = (speaker, text + "\n" + raw)
    return [c for c in conversations if c[1]]


def load_all(folder: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    out = []
    for path in sorted(folder.glob("*.md")):
        out += load(path)
    return out
