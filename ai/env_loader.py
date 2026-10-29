"""Load the repo-root ``.env`` into ``os.environ``.

Several entry points (the scheduler, the live fixture fetcher, the odds sync
job) rely on API keys that live in ``.env``.  Without this, the keys are
missing and the code silently falls back to placeholder data.

Existing environment variables always win – real CI secrets override the
file.  No third-party dependency is used so this works in the slim
GitHub Actions runner image.
"""
from __future__ import annotations

import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"


def load_dotenv(path: str | os.PathLike[str] | None = None) -> bool:
    """Populate ``os.environ`` from a ``.env`` file. Returns True if loaded."""
    target = pathlib.Path(path) if path else ENV_PATH
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return False

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # `export FOO=bar` is valid in a shell rc; treat it as FOO.
        if key.startswith("export "):
            key = key[len("export "):]
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip a trailing comment ONLY when it is preceded by whitespace,
        # so `FOO=https://x/#frag` keeps its fragment but `FOO=bar  # note`
        # loses the note.
        if " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
    return True
