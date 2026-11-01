"""Inventory literal spoken responses and build their variation catalog offline."""

import ast
import json
from pathlib import Path

from dialogue.variation import expand

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = {'persona.py', 'phrasing.py', 'variation.py', 'realizer.py'}


def inventory():
    found = set()
    for path in [ROOT/'app'/'conversation.py', *(ROOT/'dialogue').glob('*.py')]:
        if path.name in EXCLUDE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if not isinstance(node, ast.Return):
                continue
            pending = [node.value]
            while pending:
                value = pending.pop()
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    text = value.value.strip()
                    if any(c.isalpha() for c in text) and text.endswith(('.', '?', '!')):
                        found.add(text)
                elif isinstance(value, ast.IfExp):
                    pending.extend((value.body, value.orelse))
    return found


def main():
    data = {text:list(expand((text,))) for text in sorted(inventory())}
    (ROOT/'dialogue'/'scripted_replies.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'{len(data)} canned response families; minimum {min(map(len,data.values()))} variants')


if __name__ == '__main__':
    main()
