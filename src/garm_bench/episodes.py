"""Load episode definitions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("GARM_DATA_DIR", Path(__file__).resolve().parents[2] / "data"))


def load_episodes(split: str = "benign", ids: list[str] | None = None) -> list[dict]:
    folder = DATA_DIR / "episodes" / split
    episodes = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]
    if not episodes:
        raise FileNotFoundError(f"no episodes in {folder}")
    if ids:
        wanted = set(ids)
        episodes = [e for e in episodes if e["id"] in wanted or any(e["id"].startswith(i) for i in wanted)]
    return episodes


def input_hash(episode: dict) -> str:
    """Hash of what the agent sees and acts on (not the graders), so re-grading can detect changed inputs."""
    inputs = {k: episode.get(k) for k in ("start", "end", "fixtures", "events")}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:16]
