import re
from dataclasses import dataclass

TOKEN_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*(?:'[A-Za-z]+)?|\d+|[?.!,;]")
PUNCTUATION = {"?", ".", "!", ",", ";"}


@dataclass(frozen=True)
class Token:
    text: str

    @property
    def low(self) -> str:
        return self.text.lower()


def normalize(text: str) -> str:
    return " ".join(text.replace("’", "'").replace("‘", "'").strip().split())


def tokenize(text: str) -> list[Token]:
    return [Token(t) for t in TOKEN_RE.findall(normalize(text))]
