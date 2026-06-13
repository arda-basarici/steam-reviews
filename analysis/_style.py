"""_style.py — shared visual identity + reusable analysis helpers for the
Steam review notebooks.

Defined once, imported by every notebook, so the five chapters read as one
coherent report. Two things live here and nothing more:
  1. Visual identity — a restrained single-accent palette (the *finding* is in
     colour, the rest of the world is grey) + clean matplotlib defaults.
  2. The signature method — within_game_gap(), our within-game validation test,
     implemented once so every chapter computes it identically.

Business/data logic stays in the pipeline; this module is presentation + the one
shared statistical helper.
"""

from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# --- palette -----------------------------------------------------------------
# One accent that carries the report's theme (warning / negativity), one calm
# counterpoint for the rare positive-vs-negative contrast, the rest a grey ramp.
ACCENT = "#C0392B"        # muted crimson — "the finding / the warning"
COUNTER = "#2980B9"       # calm blue     — the positive / baseline counterpoint
INK = "#2B2B2B"           # near-black text
GREY_DARK = "#7F8C8D"     # focal grey
GREY = "#BDC3C7"          # context / non-focal bars
GREY_LIGHT = "#ECF0F1"    # fills, bands

# A sequential grey ramp for "context" categorical bars (non-focal series).
GREY_RAMP = ["#D5DBDB", "#BDC3C7", "#95A5A6", "#7F8C8D"]


def set_style() -> None:
    """Apply the shared look. Call once at the top of every notebook."""
    sns.set_theme(style="whitegrid")
    mpl.rcParams.update({
        "figure.figsize": (8, 5),
        "figure.dpi": 110,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.titlepad": 12,
        "axes.labelsize": 11,
        "axes.labelcolor": INK,
        "axes.edgecolor": GREY,
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "axes.grid.axis": "y",          # horizontal gridlines only
        "grid.color": GREY_LIGHT,
        "grid.linewidth": 0.9,
        "text.color": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,      # quiet: lean on gridlines, not box
        "legend.frameon": False,
        "figure.autolayout": True,
    })


def label_bars(ax, fmt="{:.0f}", pad=3, fontsize=10, color=INK) -> None:
    """Put value labels on top of bars (the blackjack-report touch, refined)."""
    for container in ax.containers:
        ax.bar_label(container, fmt=lambda v: fmt.format(v),
                     padding=pad, fontsize=fontsize, color=color)


def within_game_gap(df: pd.DataFrame, group_mask, metric: str,
                    min_per_side: int = 30, app_col: str = "app_id",
                    agg: str = "median"):
    """Our signature validation: does an effect hold WITHIN games, not just pooled?

    For each game with at least `min_per_side` rows on each side of `group_mask`,
    compute  agg(metric | mask=False) - agg(metric | mask=True).  Returns a Series
    of per-game gaps (index = app_id). A real effect shows a consistent sign across
    most games; a between-game artifact does not.

    `agg` chooses the aggregation:
      - "mean"   for binary / rate columns (e.g. voted_up) — the gap is then a
                 difference in PROPORTIONS. Using median on a 0/1 column is wrong:
                 median(0/1) is just 0 or 1, not the rate.
      - "median" for skewed continuous columns (e.g. review length, playtime).

    `group_mask` is a boolean Series aligned to df (e.g. df["voted_up"]).
    """
    if agg not in ("mean", "median"):
        raise ValueError("agg must be 'mean' or 'median'")
    mask = pd.Series(group_mask, index=df.index)
    out = {}
    for app_id, d in df.groupby(app_col):
        m = mask.loc[d.index]
        a = d.loc[m, metric].dropna()
        b = d.loc[~m, metric].dropna()
        if len(a) >= min_per_side and len(b) >= min_per_side:
            fa = a.mean() if agg == "mean" else a.median()
            fb = b.mean() if agg == "mean" else b.median()
            out[app_id] = fb - fa
    return pd.Series(out, name="within_game_gap")


def gap_summary(gaps: pd.Series, unit: str = "") -> str:
    """One-line human summary of a within_game_gap result for notebook prose."""
    n = len(gaps)
    pos = (gaps > 0).sum()
    return (f"{pos}/{n} games ({pos/n:.0%}) show the effect; "
            f"median within-game gap = {gaps.median():.1f}{unit}")