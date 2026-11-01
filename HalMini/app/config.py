import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    db_path: str
    max_depth: int
    inference_budget: int


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env reader (KEY=VALUE lines); real environment variables win."""
    file = Path(path)
    if not file.exists():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> Config:
    db_path = os.environ.get("HALMINI_DB", "data/halmini.db")
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return Config(
        db_path=db_path,
        max_depth=int(os.environ.get("HALMINI_MAX_DEPTH", "16")),
        inference_budget=int(os.environ.get("HALMINI_INFERENCE_BUDGET", "5000")),
    )
