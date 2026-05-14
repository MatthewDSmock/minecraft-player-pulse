"""
Minecraft Player Pulse — Streamlit dashboard.

Run with:
    streamlit run dashboard.py

Reads from the master parquet produced by minecraft_scraper.py.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

MASTER_PATH = Path("data/processed/minecraft_sentiment.parquet")
EVENTS_PATH = Path("events.csv")

st.set_page_config(
    page_title="Minecraft Player Pulse",
    page_icon="🎮",
    layout="wide",
)


# ---------- Data loading ----------

@st.cache_data(ttl=600)
def load_data() -> pd.DataFrame:
    if not MASTER_PATH.exists():
        return pd.DataFrame()
    df = pd.read_parquet(MASTER_PATH)
    df["created_utc"] = pd.to_datetime(df["created_utc"], utc=True)
    df["created_date"] = df["created_utc"].dt.date
    return df


@st.cache_data(ttl=600)
def load_events() -> pd.DataFrame:
    """Optional events.csv: date,event,pillar — overlaid on the timeline."""
    if not EVENTS_PATH.exists():
        return pd.DataFrame(columns=["date", "event", "pillar"])
    e = pd.read_csv(EVENTS_PATH)
    e["date"] = pd.to_datetime(e["date"]).dt.date
    return e


df = load_data()
events = load_events()


# ---------- Header ----------

st.title("Minecraft Player Pulse")
st.caption(
    "Public community sentiment across Marketplace, Realms, and Creator on Demand. "
    "Built from Reddit, Trustpilot, and feedback channels — verbatim quotes preserved."
)

if df.empty:
    st.warning(
        "No data yet. Run `python minecraft_scraper.py` to populate the dataset, "
        "then refresh this page."
    )
    st.stop()


# ---------- Sidebar filters ----------

st.sidebar.header("Filters")

min_date = df["created_date"].min()
max_date = df["created_date"].max()
date_range = st.sidebar.date_input(
    "Date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)
if isinstance(date_range, date):
    date_range = (date_range, date_range)

available_pillars = sorted(
    {p for pillars in df["pillars"] for p in pillars}
)
selected_pillars = st.sidebar.multiselect(
    "Pillars",
    options=available_pillars,
    default=[p for p in available_pillars if p != "Unclassified"],
)

sentiment_filter = st.sidebar.multiselect(
    "Sentiment",
    options=["positive", "neutral", "negative"],
    default=["positive", "neutral", "negative"],
)

available_subs = sorted(df["subreddit"].unique())
selected_subs = st.sidebar.multiselect(
    "Subreddits",
    options=available_subs,
    default=available_subs,
)

min_score = st.sidebar.slider(
    "Minimum upvote score (filter low-engagement noise)",
    min_value=int(df["score"].min()),
    max_value=int(df["score"].max()),
    value=0,
)


# ---------- Apply filters ----------

mask = (
    (df["created_date"] >= date_range[0])
    & (df["created_date"] <= date_range[1])
    & (df["sentiment_label"].isin(sentiment_filter))
    & (df["subreddit"].isin(selected_subs))
    & (df["score"] >= min_score)
)
if selected_pillars:
    mask &= df["pillars"].apply(
        lambda ps: any(p in selected_pillars for p in ps)
    )

filtered = df[mask].copy()


# ---------- Top metrics ----------

c1, c2, c3, c4 = st.columns(4)
c1.metric("Records", f"{len(filtered):,}")
c2.metric(
    "Net sentiment",
    f"{filtered['sentiment_compound'].mean():+.2f}" if len(filtered) else "—",
)
c3.metric(
    "Negative share",
    f"{(filtered['sentiment_label']=='negative').mean()*100:.0f}%"
    if len(filtered) else "—",
)
c4.metric("Unique authors", f"{filtered['author'].nunique():,}")

st.divider()


# ---------- Timeline with event overlays ----------

st.subheader("Sentiment over time")
st.caption(
    "Daily volume by sentiment. Vertical lines mark known events from events.csv — "
    "look for volume shifts after each event."
)

ts = (
    filtered.groupby(["created_date", "sentiment_label"])
    .size()
    .reset_index(name="count")
)

fig_ts = px.area(
    ts,
    x="created_date",
    y="count",
    color="sentiment_label",
    color_discrete_map={
        "positive": "#1d9e75",
        "neutral": "#888780",
        "negative": "#E24B4A",
    },
)

# Overlay events as vertical lines if any are in range
if not events.empty:
    relevant_events = events[
        (events["date"] >= date_range[0]) & (events["date"] <= date_range[1])
    ]
    for _, ev in relevant_events.iterrows():
        # Convert date to pandas Timestamp for Plotly compatibility
        event_x = pd.Timestamp(ev["date"])
        try:
            fig_ts.add_vline(
                x=event_x,
                line_dash="dash",
                line_color="#3C3489",
                annotation_text=ev["event"][:30] + ("…" if len(ev["event"]) > 30 else ""),
                annotation_position="top right",
            )
        except Exception:
            # If vline fails on this Plotly version, silently skip the annotation
            # (the chart still renders without it)
            pass

st.plotly_chart(fig_ts, use_container_width=True)


# ---------- Two-column breakdowns ----------

col_a, col_b = st.columns(2)

with col_a:
    st.subheader("Volume by pillar")
    pillar_counts = {}
    for pillars in filtered["pillars"]:
        for p in pillars:
            pillar_counts[p] = pillar_counts.get(p, 0) + 1
    pillar_df = (
        pd.DataFrame(
            {"pillar": list(pillar_counts.keys()), "count": list(pillar_counts.values())}
        )
        .sort_values("count", ascending=False)
    )
    fig_p = px.bar(pillar_df, x="pillar", y="count")
    st.plotly_chart(fig_p, use_container_width=True)

with col_b:
    st.subheader("Mean sentiment by subreddit")
    sub_sent = (
        filtered.groupby("subreddit")["sentiment_compound"]
        .agg(["mean", "count"])
        .reset_index()
        .rename(columns={"mean": "sentiment", "count": "n"})
        .sort_values("sentiment")
    )
    fig_s = px.bar(
        sub_sent,
        x="sentiment",
        y="subreddit",
        orientation="h",
        text="n",
        labels={"sentiment": "Mean compound sentiment", "n": "records"},
    )
    fig_s.update_traces(textposition="outside")
    st.plotly_chart(fig_s, use_container_width=True)


# ---------- Verbatim quote drawer ----------

st.divider()
st.subheader("Player voices — verbatim")
st.caption("Every record links to the original post or comment on Reddit.")

view = st.radio(
    "Show:",
    ["Top complaints", "Top praise", "Most recent", "Most engagement"],
    horizontal=True,
)

if view == "Top complaints":
    display = filtered[filtered["sentiment_label"] == "negative"].nlargest(20, "score")
elif view == "Top praise":
    display = filtered[filtered["sentiment_label"] == "positive"].nlargest(20, "score")
elif view == "Most recent":
    display = filtered.nlargest(20, "created_utc")
else:
    display = filtered.nlargest(20, "score")

for _, row in display.iterrows():
    label_color = {
        "positive": "🟢",
        "neutral": "⚪",
        "negative": "🔴",
    }.get(row["sentiment_label"], "⚪")

    source = row["subreddit"]
    source_display = f"r/{source}" if "." not in source else source
    score_label = "upvotes" if "." not in source else "votes"

    title_line = (
        f"{label_color} **{source_display}** · "
        f"{row['score']} {score_label} · "
        f"{row['created_utc'].strftime('%Y-%m-%d')} · "
        f"_{row['pillars_str']}_"
    )

    with st.expander(title_line):
        if row["title"]:
            st.markdown(f"**{row['title']}**")
        body = row["body"] or ""
        if len(body) > 2000:
            body = body[:2000] + "…"
        st.write(body)
        link_label = "Open on Reddit ↗" if "." not in source else "Open source ↗"
        author_prefix = "u/" if "." not in source else ""
        st.markdown(
            f"by {author_prefix}{row['author']} · "
            f"sentiment {row['sentiment_compound']:+.2f} · "
            f"[{link_label}]({row['url']})"
        )


# ---------- Footer ----------

st.divider()
st.caption(
    f"Data: {len(df):,} total records · "
    f"latest scrape {df['scraped_at'].max().strftime('%Y-%m-%d %H:%M UTC')} · "
    f"earliest content {df['created_utc'].min().strftime('%Y-%m-%d')}"
)
