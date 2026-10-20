"""Same .env loader as auth.load_env, without importing the server's web stack into the voice venv."""
import os
from pathlib import Path


def load_env(path: str | None) -> None:
    """Read KEY=VALUE lines from the .env file into os.environ (existing vars win)."""
    if not path or not Path(path).is_file():
        return
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
