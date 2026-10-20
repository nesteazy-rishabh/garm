"""Headline figure: the laundered chain against each defence, with and without a known-good callback (D6).

Reads the analysis files written by `garm analyze` (runs/main/analysis-regraded.json and runs/d6/analysis.json) and
writes docs/figures/headline-{light,dark}.svg plus docs/figures/headline.csv (the table view).

    uv run --extra figures python scripts/make_figures.py
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"
MODEL = "claude-haiku-5-5"
DEFENCES = ["D0", "D2", "D4", "D5"]
LABELS = {"D0": "D0  no defence", "D2": "D2  approval gateway", "D4": "D4  plan-then-execute",
          "D5": "D5  provenance labels"}

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
              "axis": "#c3c2b7", "s1": "#2a78d6", "s2": "#eb6834"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
             "axis": "#383835", "s1": "#3987e5", "s2": "#d95926"},
}


def load() -> list[dict]:
    main = json.loads((ROOT / "runs/main/analysis-regraded.json").read_text())[MODEL]
    d6 = json.loads((ROOT / "runs/d6/analysis.json").read_text())[MODEL]
    rows = []
    for d in DEFENCES:
        for policy, cell in (("on file", main[d]), ("known good", d6[f"{d}+known_good"])):
            rows.append({
                "defence": d, "callback": policy,
                "laundered_success_rubber_stamp_93": cell["effective"]["rubber_stamp_93"]["attack_success_laundered"],
                "laundered_activation_approve_all": cell["cross_run_laundered"]["activation"]["rate"],
                "approvals_per_100_legitimate_updates": cell["approvals_per_100_by_split"]["legit"],
                "legitimate_update_pass": cell["legit_pass"]["rate"],
            })
    return rows


def draw(rows: list[dict], theme: str, preview: str | None = None) -> None:
    t = THEMES[theme]
    plt.rcParams.update({"font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"], "font.size": 10,
                         "svg.fonttype": "path"})
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.9), sharey=True, gridspec_kw={"wspace": 0.08})
    fig.patch.set_facecolor(t["surface"])
    lanes = {"on file": -0.16, "known good": 0.16}
    colours = {"on file": t["s1"], "known good": t["s2"]}
    panels = [("laundered_success_rubber_stamp_93", "Laundered attack success\nwith a 93% rubber-stamp approver",
               (0, 1.0), [0, 0.25, 0.5, 0.75, 1.0], lambda v: f"{v:.0%}"),
              ("approvals_per_100_legitimate_updates", "Approvals per 100\nlegitimate updates",
               (0, 360), [0, 100, 200, 300], lambda v: f"{v:.0f}")]
    for ax, (key, title, xlim, ticks, fmt) in zip(axes, panels, strict=True):
        ax.set_facecolor(t["surface"])
        for i, d in enumerate(DEFENCES):
            for policy, dy in lanes.items():
                r = next(r for r in rows if r["defence"] == d and r["callback"] == policy)
                ax.plot([0, r[key]], [i + dy, i + dy], color=t["grid"], lw=1, zorder=1, solid_capstyle="butt")
                ax.scatter([r[key]], [i + dy], s=64, color=colours[policy], edgecolors=t["surface"], linewidths=2,
                           zorder=3, clip_on=False)
        ax.set_xlim(xlim[0] - 0.02 * xlim[1], xlim[1])
        ax.set_xticks(ticks, [fmt(v) for v in ticks])
        ax.set_title(title, loc="left", color=t["ink"], fontsize=10.5, pad=10)
        ax.grid(axis="x", color=t["grid"], lw=1)
        ax.set_axisbelow(True)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(t["axis"])
        ax.tick_params(colors=t["muted"], length=0, labelsize=9)
    axes[0].set_yticks(range(len(DEFENCES)), [LABELS[d] for d in DEFENCES])
    axes[0].tick_params(axis="y", colors=t["ink2"], labelsize=9.5)
    axes[0].invert_yaxis()
    axes[0].annotate("0% under every defence with D6", xy=(0.035, lanes["known good"]), color=t["ink2"],
                     fontsize=9, va="center")
    handles = [plt.Line2D([], [], marker="o", ls="", markersize=8, markerfacecolor=colours[p],
                          markeredgecolor=t["surface"], markeredgewidth=1.5) for p in lanes]
    fig.legend(handles, ["Callback to the number on file (today's process)",
                         "Callback to a known-good number (D6)"],
               loc="upper left", bbox_to_anchor=(0.012, 1.02), ncol=2, frameon=False, fontsize=9,
               labelcolor=t["ink2"], handletextpad=0.3, columnspacing=1.6)
    fig.text(0.012, -0.04, "Claude Haiku 5.5, 8 laundered episodes and 16 legitimate updates, 3 attempts each. "
             "Garm, 2026-10-20.", color=t["muted"], fontsize=8)
    fig.subplots_adjust(left=0.2, right=0.98, top=0.78, bottom=0.1)
    fig.savefig(OUT / f"headline-{theme}.svg", facecolor=t["surface"], bbox_inches="tight", pad_inches=0.2)
    if preview:
        fig.savefig(Path(preview) / f"headline-{theme}.png", dpi=150, facecolor=t["surface"], bbox_inches="tight",
                    pad_inches=0.2)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load()
    with open(OUT / "headline.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for theme in THEMES:
        draw(rows, theme, os.environ.get("GARM_FIGURE_PREVIEW"))
    print(f"wrote {OUT}/headline-light.svg, headline-dark.svg, headline.csv")


if __name__ == "__main__":
    main()
