"""Package the reported runs as a release archive: configs, graded results, card decisions and analyses.

Transcripts and final databases (about 700 MB) are left out. Adaptive-attacker runs are left out, because they hold
attacker rewrites (see docs/dataset-card.md). Every file is checked for API keys before it is added.

    uv run python scripts/package_runs.py            # writes dist/garm-runs-<date>.tar.gz
"""

from __future__ import annotations

import io
import re
import tarfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTS = ["runs/main/dirs.txt", "runs/sonnet/dirs.txt", "runs/d6/dirs.txt"]
RUN_FILES = ["config.json", "results.jsonl", "results.regraded.jsonl", "summary.json", "summary.regraded.json"]
EXTRA = ["runs/main/analysis-regraded.json", "runs/main/analysis-readers.json", "runs/main/cards-strict.json",
         "runs/main/cards-base_rate.json", "runs/sonnet/analysis-regraded.json", "runs/sonnet/analysis-readers.json",
         "runs/sonnet/cards-strict.json", "runs/sonnet/cards-base_rate.json", "runs/d6/analysis.json",
         "runs/d6/analysis-sonnet.json", "runs/d6/cards-strict.json", "runs/d6/cards-base_rate.json",
         "runs/d6/cards-sonnet-strict.json", "runs/d6/cards-sonnet-base_rate.json", "runs/d6/haiku-dirs.txt",
         "runs/d6/sonnet-dirs.txt", "runs/cards-sonnet-reader-strict.json", "runs/cards-sonnet-reader-base_rate.json",
         *[f"runs/resample/{m}-{p}.json" for m in ("claude-haiku-5-5", "claude-sonnet-5-5")
           for p in ("strict", "base_rate")],
         "runs/resample/report.json", "runs/resample/per-card.json", *LISTS]
SECRET = re.compile(rb"sk-ant-[A-Za-z0-9_-]{8,}")
README = """Garm run data ({day})

Configs, graded results (results.regraded.jsonl is what the docs report), card-reader decisions and analysis files
for every run in runs/main, runs/sonnet and runs/d6. Unpack at the repository root, then reproduce the tables with
the commands in docs/results-main.md and docs/results-d6.md. Transcripts and final databases are not included.
"""


def main() -> None:
    dirs = sorted({line.strip() for name in LISTS for line in (ROOT / name).read_text().splitlines() if line.strip()})
    files = [f"{d}/{f}" for d in dirs for f in RUN_FILES if (ROOT / d / f).exists()] + EXTRA
    out = ROOT / "dist" / f"garm-runs-{date.today().isoformat()}.tar.gz"
    out.parent.mkdir(exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for rel in files:
            data = (ROOT / rel).read_bytes()
            if SECRET.search(data):
                raise SystemExit(f"refusing to package {rel}: it contains an API key")
            info = tarfile.TarInfo(rel)
            info.size, info.mtime = len(data), 0
            tar.addfile(info, io.BytesIO(data))
        note = README.format(day=date.today().isoformat()).encode()
        info = tarfile.TarInfo("runs/README.txt")
        info.size = len(note)
        tar.addfile(info, io.BytesIO(note))
    print(f"wrote {out} ({len(dirs)} runs, {len(files)} files, {out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
