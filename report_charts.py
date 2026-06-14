"""report_charts.py — print-quality figures for the PDF report.

Each function takes the loaded dataframes and an output path, renders one figure
at print resolution using the shared visual identity, and returns the path. The
palette and styling mirror _style.py so the report and the notebooks read as one
body of work.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# --- visual identity (mirrors analysis/_style.py) ---
ACCENT     = "#C0392B"   # crimson — the finding
COUNTER    = "#2980B9"   # blue — the counterpoint
INK        = "#2B2B2B"
GREY_DARK  = "#7F8C8D"
GREY       = "#BDC3C7"
GREY_LIGHT = "#ECF0F1"

DPI = 200

def _base():
    plt.rcParams.update({
        "figure.dpi": DPI, "savefig.dpi": DPI, "savefig.bbox": "tight",
        "font.size": 11, "axes.titlesize": 13, "axes.titleweight": "bold",
        "axes.labelcolor": INK, "axes.edgecolor": GREY, "axes.linewidth": 0.8,
        "axes.grid": True, "axes.axisbelow": True,
        "grid.color": GREY_LIGHT, "grid.linewidth": 0.9,
        "text.color": INK, "xtick.color": INK, "ytick.color": INK,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False,
    })

def _finish(fig, ax, title, subtitle):
    ax.set_title(title + "\n", loc="left", fontsize=13, fontweight="bold")
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9.5,
            color=GREY_DARK, va="bottom")
    fig.tight_layout()


def fig_refund_gradient(reviews, path):
    _base()
    bins = [0, 30, 60, 120, 600, 3000, np.inf]
    labels = ["<30m", "30-60m", "1-2h", "2-10h", "10-50h", "50h+"]
    b = pd.cut(reviews["playtime_at_review"], bins=bins, labels=labels, right=False)
    rate = reviews.groupby(b, observed=True)["voted_up"].mean() * 100
    colors = [ACCENT, ACCENT, ACCENT, GREY, GREY, GREY]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar([str(l) for l in labels], [rate[l] for l in labels], color=colors, width=0.72)
    ax.bar_label(bars, fmt="%.0f%%", padding=3, fontsize=10, color=INK)
    ax.axvline(2.5, color=INK, linestyle=(0, (4, 3)), linewidth=1.1)
    ax.text(2.55, 8, "2-hour refund window", color=INK, fontsize=9, va="bottom")
    ax.set_ylim(0, 100); ax.set_ylabel("Recommend rate (%)")
    ax.set_xlabel("Playtime when the review was written")
    _finish(fig, ax, "Below the refund window, recommendation collapses",
            "Recommend rate by how long the reviewer had played")
    fig.savefig(path); plt.close(fig); return path


def fig_goodbye(reviews, path):
    _base()
    reviews = reviews.copy()
    reviews["after"] = (reviews["playtime_forever"] - reviews["playtime_at_review"]).clip(lower=0) / 60
    rec = reviews["voted_up"]
    med_rec, med_non = reviews.loc[rec, "after"].median(), reviews.loc[~rec, "after"].median()
    stop_rec = (reviews.loc[rec, "after"] < 1).mean()*100
    stop_non = (reviews.loc[~rec, "after"] < 1).mean()*100
    kept_rec = (reviews.loc[rec, "after"] > 10).mean()*100
    kept_non = (reviews.loc[~rec, "after"] > 10).mean()*100

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.4, 4.0),
                                   gridspec_kw={"width_ratios":[0.8,1.2]})
    ax1.bar(["Recommended","Not\nrecommended"], [med_rec, med_non],
            color=[COUNTER, ACCENT], width=0.6)
    ax1.bar_label(ax1.containers[0], fmt="%.1fh", padding=3, fontsize=10)
    ax1.set_ylabel("Median hours played after review")
    ax1.set_ylim(0, max(med_rec, med_non)*1.3 + 0.5)
    ax1.set_title("Recommenders keep playing", loc="left", fontsize=11)

    x = np.arange(2); w = 0.38
    ax2.bar(x-w/2, [stop_rec, kept_rec], w, label="Recommended", color=COUNTER)
    ax2.bar(x+w/2, [stop_non, kept_non], w, label="Not recommended", color=ACCENT)
    for c in ax2.containers: ax2.bar_label(c, fmt="%.0f%%", padding=2, fontsize=8)
    ax2.set_xticks(x); ax2.set_xticklabels(["Walked away\n(<1h after)","Stuck around\n(>10h after)"])
    ax2.set_ylabel("Share of reviews (%)")
    ax2.set_title("...and panners walk away", loc="left", fontsize=11)
    ax2.legend(fontsize=8)
    fig.suptitle("")
    fig.tight_layout()
    fig.savefig(path); plt.close(fig); return path


def fig_length(reviews, path):
    _base()
    reviews = reviews.copy()
    reviews["len"] = reviews["review"].str.len().fillna(0)
    rec = reviews["voted_up"]
    med_pos, med_neg = reviews.loc[rec,"len"].median(), reviews.loc[~rec,"len"].median()
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    bars = ax.bar(["Recommended","Not recommended"], [med_pos, med_neg],
                  color=[COUNTER, ACCENT], width=0.6)
    ax.bar_label(bars, fmt="%.0f chars", padding=3, fontsize=11)
    ax.set_ylabel("Median review length (characters)")
    ax.set_ylim(0, med_neg*1.3)
    _finish(fig, ax, "Negative reviews run more than twice as long",
            "Median characters per review")
    fig.savefig(path); plt.close(fig); return path


def fig_veteran(reviews, path):
    _base()
    public = reviews[reviews["num_games_owned"] >= 1]
    bins = [1, 10, 50, 200, np.inf]; labels = ["1-10","11-50","51-200","200+"]
    b = pd.cut(public["num_games_owned"], bins=bins, labels=labels, right=False)
    rate = public.groupby(b, observed=True)["voted_up"].mean()*100
    colors = [GREY, GREY, ACCENT, ACCENT]
    fig, ax = plt.subplots(figsize=(6.8, 4.2))
    bars = ax.bar([str(l) for l in labels], [rate[l] for l in labels], color=colors, width=0.68)
    ax.bar_label(bars, fmt="%.1f%%", padding=3, fontsize=10, color=INK)
    ax.set_ylim(0, 100); ax.set_ylabel("Recommend rate (%)")
    ax.set_xlabel("Games owned by the reviewer")
    _finish(fig, ax, "The bigger the library, the lower the recommend rate",
            "Public-profile reviewers only")
    fig.savefig(path); plt.close(fig); return path


def fig_reviewbomb(reviews, path, app_id=553850):
    _base()
    hd = reviews[reviews["app_id"] == app_id].copy()
    hd["date"] = pd.to_datetime(hd["timestamp_created"]).dt.date
    daily = hd.groupby("date").agg(n=("voted_up","size"), rate=("voted_up","mean"))
    daily = daily[daily["n"] >= 10]; daily["rate"] *= 100
    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    colors = [ACCENT if r < 60 else GREY_DARK for r in daily["rate"]]
    ax.scatter(daily.index, daily["rate"], c=colors,
               s=[min(n/3, 120) for n in daily["n"]], zorder=3, alpha=0.9)
    ax.plot(daily.index, daily["rate"], color=GREY, linewidth=1, zorder=1, alpha=0.6)
    ax.axhline(85, color=INK, linestyle=(0,(4,3)), linewidth=1)
    ax.text(daily.index[0], 86.5, "typical ~85%", fontsize=9, color=INK)
    ax.set_ylim(0, 100); ax.set_ylabel("Daily recommend rate (%)"); ax.set_xlabel("Date")
    _finish(fig, ax, "A real dip — but no baseline to call it a bomb",
            "Helldivers 2 daily recommend rate; dot size = reviews that day")
    fig.autofmt_xdate()
    fig.savefig(path); plt.close(fig); return path