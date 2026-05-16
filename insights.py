"""
insights.py — Auto-generated Insights & Recommendations layer for the dashboard.

Reads experiments.yaml, matches each experiment's keywords against the player-voice
parquet, and produces a copy-paste-ready section for QBR / WoW use.

Designed so the user can drag-select across the section and paste into PowerPoint
or Slack without losing structure. Streamlit renders the Markdown natively.

Matching is word-boundary aware (so "family" doesn't match "familiar") and pillar-gated
(records must be tagged with the experiment's pillar to count, except for Cross-pillar
experiments which match anywhere).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st
import yaml


EXPERIMENTS_PATH = Path("experiments.yaml")


# ---------- Data structures ----------

@dataclass
class ExperimentEvidence:
    """One experiment's data-backed scorecard."""
    experiment_id: str
    name: str
    pillar: str
    sentiment_focus: str
    evidence_summary: str
    priority_hint: int

    # Computed at runtime
    record_count: int
    upvote_sum: int
    avg_sentiment: float
    top_quotes: pd.DataFrame
    all_matches: pd.DataFrame


# ---------- Loading ----------

def load_experiments() -> list[dict]:
    """Read the experiments.yaml file into a list of dicts."""
    if not EXPERIMENTS_PATH.exists():
        return []
    with open(EXPERIMENTS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("experiments", [])


# ---------- Matching ----------

def _build_keyword_pattern(keywords: list[str]) -> Optional[re.Pattern]:
    """
    Build a single regex with word boundaries for all keywords.
    Multi-word phrases are matched as exact phrases with word boundaries
    at the outside; single words use word boundaries on both sides.
    """
    if not keywords:
        return None

    escaped_keywords = []
    for kw in keywords:
        kw_lower = kw.lower().strip()
        if not kw_lower:
            continue
        # Escape regex special chars, then wrap with word boundaries
        # Use \b at the boundaries but allow internal hyphens/spaces
        # to match as-written
        escaped = re.escape(kw_lower)
        # Replace escaped spaces with \s+ to allow flexible spacing
        escaped = escaped.replace(r"\ ", r"\s+")
        escaped_keywords.append(rf"\b{escaped}\b")

    if not escaped_keywords:
        return None

    pattern = "|".join(escaped_keywords)
    return re.compile(pattern, re.IGNORECASE)


def match_experiment(df: pd.DataFrame, experiment: dict) -> pd.DataFrame:
    """
    Find all records that match the experiment.

    Two filters apply in order:
    1. Pillar gate — record must be tagged with the experiment's pillar
       (except Cross-pillar experiments, which match anywhere)
    2. Keyword match — record's title+body must contain at least one keyword
       with word-boundary matching
    3. Sentiment focus — if specified, filter to that sentiment band
    """
    if df.empty:
        return df

    # Pillar gate
    pillar = experiment.get("pillar", "")
    if pillar and pillar != "Cross-pillar":
        pillar_mask = df["pillars_str"].fillna("").str.contains(pillar, na=False, regex=False)
        df_gated = df[pillar_mask]
    else:
        df_gated = df

    if df_gated.empty:
        return df_gated

    # Keyword match
    pattern = _build_keyword_pattern(experiment.get("keywords", []))
    if pattern is None:
        return df_gated.iloc[0:0]

    title = df_gated["title"].fillna("").astype(str)
    body = df_gated["body"].fillna("").astype(str)
    text = title + " " + body

    mask = text.str.contains(pattern, regex=True, na=False)

    # Sentiment narrowing
    sentiment_focus = experiment.get("sentiment_focus", "all")
    if sentiment_focus == "negative":
        mask &= (df_gated["sentiment_label"] == "negative")
    elif sentiment_focus == "positive":
        mask &= (df_gated["sentiment_label"] == "positive")

    return df_gated[mask].copy()


def compute_evidence(
    df: pd.DataFrame,
    experiment: dict,
    top_quote_count: int = 10,
) -> ExperimentEvidence:
    """Run the match and roll up the stats for one experiment."""
    matches = match_experiment(df, experiment)
    record_count = len(matches)
    upvote_sum = int(matches["score"].sum()) if record_count else 0
    avg_sentiment = float(matches["sentiment_compound"].mean()) if record_count else 0.0

    if record_count:
        top_quotes = matches.sort_values("score", ascending=False).head(top_quote_count)
    else:
        top_quotes = matches.iloc[0:0]

    return ExperimentEvidence(
        experiment_id=experiment["experiment_id"],
        name=experiment["name"],
        pillar=experiment["pillar"],
        sentiment_focus=experiment["sentiment_focus"],
        evidence_summary=experiment.get("evidence_summary", "").strip(),
        priority_hint=experiment.get("priority_hint", 5),
        record_count=record_count,
        upvote_sum=upvote_sum,
        avg_sentiment=avg_sentiment,
        top_quotes=top_quotes,
        all_matches=matches,
    )


def rank_experiments(evidence_list: list[ExperimentEvidence]) -> list[ExperimentEvidence]:
    """
    Rank experiments by current data signal combined with static priority.

    Score blends:
      - record count (40%)
      - upvote sum / 100 (40%)
      - priority hint bonus (20% - inverted so lower hint = higher bonus)
    """
    def signal(ev: ExperimentEvidence) -> float:
        rec_component = ev.record_count * 0.4
        upvote_component = (ev.upvote_sum / 100.0) * 0.4
        priority_component = (6 - ev.priority_hint) * 0.2 * 5
        return rec_component + upvote_component + priority_component

    return sorted(evidence_list, key=signal, reverse=True)


# ---------- Time windowing ----------

def filter_by_window(df: pd.DataFrame, window: str) -> pd.DataFrame:
    """Filter records by created_utc to a window: '7d', '30d', '90d', or 'all'."""
    if window == "all" or df.empty:
        return df

    days = {"7d": 7, "30d": 30, "90d": 90}.get(window, 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return df[df["created_utc"] >= cutoff].copy()


# ---------- Headline generation ----------

def generate_headline(
    df_window: pd.DataFrame,
    df_baseline: pd.DataFrame,
) -> str:
    """Generate a one-sentence headline summarizing the strongest signal."""
    if df_window.empty:
        return "Not enough data in the selected window to generate a headline."

    def pillar_stats(d: pd.DataFrame) -> dict:
        if d.empty:
            return {}
        out = {}
        for pillar in ["Marketplace", "Realms", "CreatorOnDemand"]:
            sub = d[d["pillars_str"].fillna("").str.contains(pillar, na=False, regex=False)]
            if len(sub) >= 5:
                out[pillar] = {
                    "count": len(sub),
                    "neg_share": (sub["sentiment_label"] == "negative").mean(),
                    "mean_compound": sub["sentiment_compound"].mean(),
                }
        return out

    window_stats = pillar_stats(df_window)
    baseline_stats = pillar_stats(df_baseline)

    biggest_shift_pillar = None
    biggest_shift_delta = 0.0
    biggest_shift_direction = ""

    for pillar, w in window_stats.items():
        b = baseline_stats.get(pillar)
        if not b:
            continue
        delta = w["neg_share"] - b["neg_share"]
        if abs(delta) > abs(biggest_shift_delta):
            biggest_shift_delta = delta
            biggest_shift_pillar = pillar
            biggest_shift_direction = "up" if delta > 0 else "down"

    if biggest_shift_pillar and abs(biggest_shift_delta) > 0.02:
        pct_change = abs(biggest_shift_delta) * 100
        return (
            f"**{biggest_shift_pillar}** negative sentiment is **{biggest_shift_direction} "
            f"{pct_change:.0f}%** in the current window vs. the trailing baseline. "
            f"Volume: {window_stats[biggest_shift_pillar]['count']} records."
        )

    total = len(df_window)
    neg = (df_window["sentiment_label"] == "negative").mean() * 100
    return (
        f"Current window: **{total:,} records**, **{neg:.0f}%** negative sentiment. "
        f"No significant pillar-level sentiment shifts vs. baseline."
    )


# ---------- Top verbatim quotes ----------

def top_verbatim_quotes(df_window: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """Pick the highest-upvote quotes in the current window."""
    if df_window.empty:
        return df_window
    return df_window.sort_values("score", ascending=False).head(n)


# ---------- What's quieter than usual ----------

def quieter_than_usual(
    df_window: pd.DataFrame,
    df_baseline: pd.DataFrame,
) -> Optional[str]:
    """Identify pillars where current-window volume is meaningfully lower."""
    if df_window.empty or df_baseline.empty:
        return None

    quieter = []
    for pillar in ["Marketplace", "Realms", "CreatorOnDemand"]:
        w_count = (df_window["pillars_str"].fillna("").str.contains(pillar, na=False, regex=False)).sum()
        b_count = (df_baseline["pillars_str"].fillna("").str.contains(pillar, na=False, regex=False)).sum()
        if b_count < 10:
            continue
        if w_count < b_count * 0.5 and b_count >= 20:
            drop_pct = (1 - w_count / max(b_count, 1)) * 100
            quieter.append((pillar, drop_pct, w_count, b_count))

    if not quieter:
        return None

    quieter.sort(key=lambda x: x[1], reverse=True)
    pillar, drop_pct, w_count, b_count = quieter[0]
    return (
        f"**{pillar}** complaint volume is down meaningfully in the current window "
        f"({w_count} records vs. {b_count} in baseline). "
        f"Possible signals: a recent fix landing, or attention diverted to "
        f"higher-priority frustrations elsewhere."
    )


# ---------- Streamlit rendering ----------

def render_insights_section(df: pd.DataFrame) -> None:
    """Render the full Insights & Recommendations section."""
    st.markdown("## 📌 Insights & Recommendations")
    st.caption(
        "Auto-generated from current data. Copy-paste-ready for QBRs and WoW reviews. "
        "Select any section and paste directly into a slide or Slack message."
    )

    window_label = st.radio(
        "Window",
        options=["Last 7 days", "Last 30 days", "Last 90 days", "All time"],
        index=3,
        horizontal=True,
        label_visibility="collapsed",
    )
    window_map = {
        "Last 7 days": "7d",
        "Last 30 days": "30d",
        "Last 90 days": "90d",
        "All time": "all",
    }
    window = window_map[window_label]
    df_window = filter_by_window(df, window)

    if window == "7d":
        baseline = filter_by_window(df, "30d")
    elif window == "30d":
        baseline = filter_by_window(df, "90d")
    elif window == "90d":
        baseline = df
    else:
        baseline = df

    st.markdown("---")

    # ─── HEADLINE ────────────────────────────────────────────────────────────
    st.markdown("### Headline")
    headline = generate_headline(df_window, baseline)
    st.markdown(headline)

    st.markdown("")

    # ─── TOP PLAYER VOICES ───────────────────────────────────────────────────
    st.markdown("### Top player voices")
    top_quotes = top_verbatim_quotes(df_window, n=3)
    if top_quotes.empty:
        st.markdown("_No high-engagement records in the selected window._")
    else:
        for _, row in top_quotes.iterrows():
            score = int(row.get("score", 0))
            subreddit = row.get("subreddit", "unknown")
            date_str = pd.to_datetime(row["created_utc"]).strftime("%Y-%m-%d")
            url = row.get("url", "")
            sentiment = row.get("sentiment_label", "")
            body = str(row.get("body") or row.get("title") or "").strip()
            if len(body) > 280:
                body = body[:277] + "..."

            st.markdown(
                f"<div style='color:rgba(183,228,244,0.7); font-size:0.9rem; "
                f"margin-bottom:0.3rem;'>"
                f"[<span style='color:#00f5ff; font-weight:600;'>{score:,}</span> upvotes "
                f"· <span style='color:#00f5ff; font-weight:600;'>{subreddit}</span> "
                f"· <span style='color:#00f5ff; font-weight:600;'>{date_str}</span> "
                f"· <span style='color:#00f5ff; font-weight:600;'>{sentiment}</span>]"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(f"_{body}_")
            st.markdown(
                f"<a href='{url}' target='_blank' "
                f"style='color:rgba(0,245,255,0.55); text-decoration:none; "
                f"font-size:0.85rem;'>"
                f"→ Open source</a>",
                unsafe_allow_html=True,
            )
            st.markdown("")

    # ─── RECOMMENDED EXPERIMENTS ─────────────────────────────────────────────
    # Pillar legend sits directly above the experiment list so readers can
    # decode the [PILLAR]-[LEVER]-[##] ID format at a glance while scrolling.
    # Wrapped in a native bordered container so it visually separates from the
    # Top player voices section above. Cyan accents match the DNA helix /
    # lab-briefing aesthetic of the rest of the site.
    CYAN = "#00f5ff"
    st.markdown(
        "<div style='margin-top:1.2rem;'></div>",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.markdown(
            "<div style='font-weight:600; font-size:0.95rem; "
            "color:rgba(255,255,255,0.92); margin-bottom:0.5rem; "
            "letter-spacing:0.02em;'>"
            "HOW TO READ THESE IDs"
            "</div>",
            unsafe_allow_html=True,
        )
        leg_cols = st.columns(4)
        with leg_cols[0]:
            st.markdown(
                f"<span style='color:{CYAN}; font-weight:700;'>MP</span> — Marketplace",
                unsafe_allow_html=True,
            )
            st.caption("Skin packs, world templates, mash-ups, the in-game store.")
        with leg_cols[1]:
            st.markdown(
                f"<span style='color:{CYAN}; font-weight:700;'>RM</span> — Realms",
                unsafe_allow_html=True,
            )
            st.caption("The player-hosted subscription server product.")
        with leg_cols[2]:
            st.markdown(
                f"<span style='color:{CYAN}; font-weight:700;'>CD</span> — Creator on Demand",
                unsafe_allow_html=True,
            )
            st.caption("Commissioned custom-build service.")
        with leg_cols[3]:
            st.markdown(
                f"<span style='color:{CYAN}; font-weight:700;'>XP</span> — Cross-pillar",
                unsafe_allow_html=True,
            )
            st.caption("Applies across the franchise.")
        st.markdown(
            f"<div style='color:rgba(183,228,244,0.7); font-size:0.85rem; "
            f"margin-top:0.4rem;'>"
            f"The second segment names the lever "
            f"(<span style='color:{CYAN};'>REFUND</span>, "
            f"<span style='color:{CYAN};'>TIER</span>, "
            f"<span style='color:{CYAN};'>MODERATION</span>). "
            f"The trailing number is a stable tracking ID."
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("### Recommended experiments")
    st.caption(
        "Ranked by current data signal × strategic priority. "
        "Pillar-gated: records must be tagged with the experiment's pillar to count "
        "(except Cross-pillar experiments)."
    )

    experiments = load_experiments()
    if not experiments:
        st.warning("No experiments.yaml file found. Add experiments to enable this section.")
        return

    evidence_list = [compute_evidence(df_window, exp) for exp in experiments]
    evidence_with_signal = [e for e in evidence_list if e.record_count > 0]

    if not evidence_with_signal:
        st.markdown(
            "_No experiments have evidence in the selected window. "
            "Try widening the window to 'All time'._"
        )
    else:
        ranked = rank_experiments(evidence_with_signal)[:8]  # top 8
        for i, ev in enumerate(ranked, start=1):
            st.markdown(
                f"**{i}. `{ev.experiment_id}` — {ev.name}**",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='color:rgba(183,228,244,0.7); font-size:0.9rem; "
                f"margin-top:-0.3rem; margin-bottom:0.5rem;'>"
                f"▸ <span style='color:#00f5ff; font-weight:600;'>{ev.record_count}</span> records "
                f"· <span style='color:#00f5ff; font-weight:600;'>{ev.upvote_sum:,}</span> total upvotes "
                f"· avg sentiment <span style='color:#00f5ff; font-weight:600;'>{ev.avg_sentiment:+.2f}</span> "
                f"· pillar: <span style='color:#00f5ff; font-weight:600;'>{ev.pillar}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.markdown(f"{ev.evidence_summary}")

            with st.expander(f"Show {min(ev.record_count, 10)} supporting quotes"):
                for _, row in ev.top_quotes.iterrows():
                    score = int(row.get("score", 0))
                    subreddit = row.get("subreddit", "unknown")
                    date_str = pd.to_datetime(row["created_utc"]).strftime("%Y-%m-%d")
                    url = row.get("url", "")
                    body = str(row.get("body") or row.get("title") or "").strip()
                    if len(body) > 320:
                        body = body[:317] + "..."
                    st.markdown(
                        f"- **[{score} upvotes · {subreddit} · {date_str}]** {body} "
                        f"[→]({url})"
                    )

            st.markdown("")

        # Show experiments below the top 8 in a collapsible expander.
        # A horizontal rule + cyan-tinted prelude line signals this is a
        # different KIND of disclosure than the per-experiment "supporting
        # quotes" expanders above, so readers don't skim past it.
        total_with_signal = len(evidence_with_signal)
        if total_with_signal > 8:
            remaining = rank_experiments(evidence_with_signal)[8:]
            st.markdown(
                "<hr style='border:none; border-top:1px solid rgba(0,245,255,0.18); "
                "margin:1.2rem 0 0.6rem 0;'>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div style='color:rgba(183,228,244,0.85); font-size:0.95rem; "
                f"margin-bottom:0.4rem;'>"
                f"<span style='color:#00f5ff; font-weight:700;'>{len(remaining)} more</span> "
                f"experiment(s) ranked below the cut — open to see what's next in the backlog."
                f"</div>",
                unsafe_allow_html=True,
            )
            with st.expander(
                f"📋 Show experiments 9–{8 + len(remaining)} (ranked below the top 8)"
            ):
                st.caption(
                    "These have evidence in the current window but didn't break "
                    "into the top 8. Useful for depth-of-bench discussions."
                )
                for i, ev in enumerate(remaining, start=9):
                    st.markdown(
                        f"**{i}. `{ev.experiment_id}` — {ev.name}**",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='color:rgba(183,228,244,0.7); font-size:0.9rem; "
                        f"margin-top:-0.3rem; margin-bottom:0.5rem;'>"
                        f"▸ <span style='color:#00f5ff; font-weight:600;'>{ev.record_count}</span> records "
                        f"· <span style='color:#00f5ff; font-weight:600;'>{ev.upvote_sum:,}</span> total upvotes "
                        f"· avg sentiment <span style='color:#00f5ff; font-weight:600;'>{ev.avg_sentiment:+.2f}</span> "
                        f"· pillar: <span style='color:#00f5ff; font-weight:600;'>{ev.pillar}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"{ev.evidence_summary}")
                    st.markdown("")

    # ─── WHAT'S QUIETER THAN USUAL ───────────────────────────────────────────
    st.markdown("### What's quieter than usual")
    quieter = quieter_than_usual(df_window, baseline)
    if quieter:
        st.markdown(quieter)
    else:
        st.markdown(
            "_No pillar shows a meaningful drop in current-window volume vs. baseline._"
        )

    st.markdown("---")
