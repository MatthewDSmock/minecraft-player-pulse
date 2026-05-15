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

import insights

MASTER_PATH = Path("data/processed/minecraft_sentiment.parquet")
EVENTS_PATH = Path("events.csv")

st.set_page_config(
    page_title="Minecraft Player Pulse",
    page_icon="🎮",
    layout="wide",
)


# ---------- Data loading ----------

# Map App Store 2-letter codes to ISO-3 for choropleth rendering
ISO_3 = {
    "us": "USA", "gb": "GBR", "ca": "CAN",
    "au": "AUS", "ie": "IRL", "nz": "NZL",
}
COUNTRY_NAMES = {
    "us": "United States", "gb": "United Kingdom", "ca": "Canada",
    "au": "Australia", "ie": "Ireland", "nz": "New Zealand",
}


def derive_device(subreddit_value: str) -> str:
    """Map a source/subreddit string to a coarse device category."""
    if not isinstance(subreddit_value, str):
        return "Mixed"
    s = subreddit_value.lower()
    if s.startswith("appstore"):
        return "Mobile (iOS)"
    if s.startswith("steam"):
        return "PC (Steam)"
    if s == "trustpilot":
        return "Web"
    if s == "feedback.minecraft.net":
        return "Mixed (Mojang feedback)"
    return "Mixed (Reddit)"


@st.cache_data(ttl=600)
def load_data() -> pd.DataFrame:
    if not MASTER_PATH.exists():
        # Try to download from GitHub Releases (used by deployed instances)
        with st.spinner("First-time setup: fetching dataset from GitHub Releases..."):
            try:
                from download_data import download
                ok = download()
                if not ok:
                    return pd.DataFrame()
            except Exception as e:
                st.error(f"Could not fetch dataset: {e}")
                return pd.DataFrame()
    df = pd.read_parquet(MASTER_PATH)
    df["created_utc"] = pd.to_datetime(df["created_utc"], utc=True)
    df["created_date"] = df["created_utc"].dt.date

    # Derived columns for filtering and visualization
    df["device"] = df["subreddit"].apply(derive_device)
    # Ensure country column exists (App Store records have it; others get "—")
    if "country" not in df.columns:
        df["country"] = "—"
    df["country"] = df["country"].fillna("—")
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
    "Built from Reddit, Mojang's feedback site, Steam (Dungeons + Legends), "
    "and the App Store (six English-speaking markets) — verbatim quotes preserved."
)

if df.empty:
    st.warning(
        "No data yet. Run `python minecraft_scraper.py` to populate the dataset, "
        "then refresh this page."
    )
    st.stop()


# ---------- Insights & Recommendations (auto-generated) ----------

insights.render_insights_section(df)


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

available_devices = sorted(df["device"].unique())
selected_devices = st.sidebar.multiselect(
    "Device / platform",
    options=available_devices,
    default=available_devices,
    help="Derived from the source: App Store → Mobile, Steam → PC, Reddit/Mojang → Mixed.",
)

available_subs = sorted(df["subreddit"].unique())
selected_subs = st.sidebar.multiselect(
    "Sources",
    options=available_subs,
    default=available_subs,
    help="Each source maps to a different community: Reddit subreddits, Mojang's "
         "feedback site, Steam (per game), and the App Store (per country).",
)

# Country filter — only meaningful when App Store data is present
available_countries = sorted([c for c in df["country"].unique() if c and c != "—"])
if available_countries:
    selected_countries = st.sidebar.multiselect(
        "Country (App Store only)",
        options=available_countries + ["—"],
        default=available_countries + ["—"],
        format_func=lambda c: COUNTRY_NAMES.get(c, c.upper()) if c != "—" else "Non-App-Store records",
        help="Country code from the App Store source. Other sources are tagged '—'.",
    )
else:
    selected_countries = None

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
    & (df["device"].isin(selected_devices))
    & (df["score"] >= min_score)
)
if selected_pillars:
    mask &= df["pillars"].apply(
        lambda ps: any(p in selected_pillars for p in ps)
    )
if selected_countries is not None:
    mask &= df["country"].isin(selected_countries)

filtered = df[mask].copy()


# ---------- Top metrics ----------

st.markdown("### Current filter selection")
st.caption(
    "These metrics reflect the sidebar filters below. The Insights section above "
    "uses the **full unfiltered dataset** with its own time-window control."
)

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Records",
    f"{len(filtered):,}",
    help=f"Out of {len(df):,} total in the master parquet.",
)
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


# ---------- Timeline + World Map (tabbed view) ----------

tab_timeline, tab_map = st.tabs(["📈 Sentiment over time", "🌍 Sentiment by country"])

with tab_timeline:
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
                pass

    st.plotly_chart(fig_ts, use_container_width=True)


with tab_map:
    st.caption(
        "App Store sentiment by country — six English-speaking markets. "
        "Hot colors = more negative, cool colors = more positive. "
        "Hover for record count per country."
    )

    # Restrict to records with a real country code (App Store rows)
    geo_df = filtered[filtered["country"].isin(ISO_3.keys())].copy()

    if geo_df.empty:
        st.info(
            "No App Store records in the current filter selection. "
            "Enable App Store sources in the sidebar to populate this view."
        )
    else:
        country_stats = (
            geo_df.groupby("country")
            .agg(
                mean_sentiment=("sentiment_compound", "mean"),
                record_count=("record_id", "count"),
                neg_share=("sentiment_label", lambda s: (s == "negative").mean()),
            )
            .reset_index()
        )
        country_stats["iso3"] = country_stats["country"].map(ISO_3)
        country_stats["country_name"] = country_stats["country"].map(COUNTRY_NAMES)

        fig_map = px.choropleth(
            country_stats,
            locations="iso3",
            color="mean_sentiment",
            hover_name="country_name",
            hover_data={
                "iso3": False,
                "mean_sentiment": ":.2f",
                "record_count": ":,",
                "neg_share": ":.1%",
            },
            color_continuous_scale="RdYlGn",
            range_color=(-0.5, 0.5),
            labels={
                "mean_sentiment": "Avg sentiment",
                "record_count": "Records",
                "neg_share": "% negative",
            },
        )
        fig_map.update_geos(
            showcoastlines=True,
            coastlinecolor="#3C3489",
            showland=True,
            landcolor="#1a1a2e",
            showocean=True,
            oceancolor="#0d0d1a",
            showcountries=True,
            countrycolor="#3C3489",
        )
        fig_map.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=0, b=0),
            height=420,
        )
        st.plotly_chart(fig_map, use_container_width=True)

        # Bar chart below the map for absolute comparison
        st.markdown("**Country breakdown — by mean sentiment**")
        bar_df = country_stats.sort_values("mean_sentiment")
        fig_bar = px.bar(
            bar_df,
            x="mean_sentiment",
            y="country_name",
            orientation="h",
            color="mean_sentiment",
            color_continuous_scale="RdYlGn",
            range_color=(-0.5, 0.5),
            text="record_count",
            labels={"mean_sentiment": "Avg sentiment", "country_name": ""},
        )
        fig_bar.update_traces(texttemplate="%{text:,} records", textposition="outside")
        fig_bar.update_layout(showlegend=False, coloraxis_showscale=False, height=280)
        st.plotly_chart(fig_bar, use_container_width=True)


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
    st.subheader("Mean sentiment by source")
    sub_sent = (
        filtered.groupby("subreddit")["sentiment_compound"]
        .agg(["mean", "count"])
        .reset_index()
        .rename(columns={"mean": "sentiment", "count": "n", "subreddit": "source"})
        .sort_values("sentiment")
    )
    fig_s = px.bar(
        sub_sent,
        x="sentiment",
        y="source",
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
