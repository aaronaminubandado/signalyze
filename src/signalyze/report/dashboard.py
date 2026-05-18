"""Streamlit dashboard.

Run via:
    streamlit run -m signalyze.report.dashboard

The dashboard intentionally stays *thin*: every datum it shows is computed by
`signalyze.analytics.metrics` or fetched from the canonical SQLite store. Adding
new charts should mean adding a query + a Plotly figure here, not embedding new
business logic.
"""

from __future__ import annotations

from signalyze.analytics import iter_group_metrics
from signalyze.compare import compute_discrepancies
from signalyze.config import get_settings
from signalyze.storage import open_database


def main() -> None:
    import pandas as pd
    import plotly.express as px
    import streamlit as st

    st.set_page_config(page_title="Signalyze", layout="wide")
    st.title("Signalyze — Reported vs Actual performance")

    settings = get_settings()
    db_path = settings.resolve(settings.paths.db_path)

    with st.sidebar:
        st.header("Filters")
        start_utc = st.text_input("Start (UTC ISO)", value="")
        end_utc = st.text_input("End (UTC ISO)", value="")
        min_signals = st.slider("Min signals per group", min_value=1, max_value=500, value=20)

    if not db_path.exists():
        st.error(f"DB not found at {db_path}. Run `signalyze init-db` first.")
        return

    with open_database(db_path) as db:
        metrics = list(
            iter_group_metrics(
                db=db,
                start_utc=start_utc or None,
                end_utc=end_utc or None,
            )
        )
        discrepancies = compute_discrepancies(db=db)

    metrics = [m for m in metrics if m.n_signals >= min_signals]
    if not metrics:
        st.info("No groups match the current filters.")
        return

    metrics_df = pd.DataFrame(
        {
            "group_id": [m.group_id for m in metrics],
            "n_signals": [m.n_signals for m in metrics],
            "reported_win_rate": [m.reported_win_rate for m in metrics],
            "actual_win_rate": [m.actual_win_rate for m in metrics],
            "win_rate_gap": [m.win_rate_gap for m in metrics],
            "avg_realized_pips": [m.avg_realized_pips for m in metrics],
            "avg_realized_rr": [m.avg_realized_rr for m in metrics],
            "ambiguous_bars": [m.ambiguous_bars for m in metrics],
            "insufficient_data": [m.insufficient_data for m in metrics],
        }
    )

    leaderboard_col, gap_col = st.columns(2)
    with leaderboard_col:
        st.subheader("Leaderboard (sorted by actual win rate)")
        st.dataframe(
            metrics_df.sort_values("actual_win_rate", ascending=False),
            hide_index=True,
            use_container_width=True,
        )
    with gap_col:
        st.subheader("Reported vs Actual win-rate gap")
        chart_df = metrics_df.dropna(subset=["reported_win_rate", "actual_win_rate"])
        if chart_df.empty:
            st.info("No groups with both reported + actual outcomes yet.")
        else:
            fig = px.scatter(
                chart_df,
                x="reported_win_rate",
                y="actual_win_rate",
                size="n_signals",
                hover_data=["group_id", "win_rate_gap", "avg_realized_pips"],
                labels={
                    "reported_win_rate": "Reported win rate",
                    "actual_win_rate": "Actual win rate",
                },
            )
            fig.add_shape(
                type="line", x0=0, y0=0, x1=1, y1=1,
                line=dict(color="gray", dash="dash"),
            )
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Discrepancy categories")
    if discrepancies:
        cat_counts: dict[str, int] = {}
        for row in discrepancies:
            cat_counts[row.category.value] = cat_counts.get(row.category.value, 0) + 1
        cat_df = pd.DataFrame(
            sorted(cat_counts.items(), key=lambda kv: -kv[1]),
            columns=["category", "signals"],
        )
        st.dataframe(cat_df, hide_index=True, use_container_width=True)
    else:
        st.info("Run `signalyze compare run` to populate the discrepancy table.")


if __name__ == "__main__":
    main()
