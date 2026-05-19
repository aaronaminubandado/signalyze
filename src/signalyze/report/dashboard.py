"""Streamlit dashboard.

Run via:
    streamlit run -m signalyze.report.dashboard

The dashboard intentionally stays *thin*: every datum it shows is computed by
`signalyze.analytics.metrics` or fetched from the canonical SQLite store. Adding
new charts should mean adding a query + a Plotly figure here, not embedding new
business logic.
"""

from __future__ import annotations

from signalyze.analytics import iter_group_metrics, iter_tp_depth
from signalyze.compare import compute_discrepancies
from signalyze.config import get_settings
from signalyze.ingest import build_label_map, groups_manifest_hint, resolve_group_label
from signalyze.storage import open_database


def main() -> None:
    import pandas as pd
    import plotly.express as px
    import streamlit as st

    st.set_page_config(page_title="Signalyze", layout="wide")
    st.title("Signalyze — Reported vs Actual performance")

    settings = get_settings()
    db_path = settings.resolve(settings.paths.db_path)
    groups_path = settings.resolve(settings.paths.groups_file)
    label_map = build_label_map(groups_path)
    hint = groups_manifest_hint(groups_path)
    if hint:
        st.warning(hint)

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
        tp_depth_rows = list(
            iter_tp_depth(
                db=db,
                start_utc=start_utc or None,
                end_utc=end_utc or None,
            )
        )
        discrepancies = compute_discrepancies(db=db)

    metrics = [m for m in metrics if m.n_signals >= min_signals]
    tp_depth_rows = [r for r in tp_depth_rows if r.n_signals >= min_signals]
    if not metrics:
        st.info("No groups match the current filters.")
        return

    metrics_df = _metrics_to_dataframe(metrics, label_map=label_map, pd=pd)

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
            fig = px.bar(
                chart_df.sort_values("win_rate_gap", ascending=False),
                x="group",
                y=["reported_win_rate", "actual_win_rate"],
                barmode="group",
                title="Reported (claimed) vs Actual (market-confirmed) win rate",
            )
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("TP-depth breakdown")
    if not tp_depth_rows:
        st.info("No TP-depth data for the current filters.")
    else:
        tp_df = _tp_depth_to_dataframe(tp_depth_rows, label_map=label_map, pd=pd)
        st.dataframe(tp_df, hide_index=True, use_container_width=True)
        chart_df = _tp_depth_to_long(tp_depth_rows, label_map=label_map, pd=pd)
        if not chart_df.empty:
            fig = px.bar(
                chart_df,
                x="group",
                y="hit_rate",
                color="tp_level",
                barmode="group",
                title="Hit rate per TP level (denom: signals that defined TPn and were reported)",
            )
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Discrepancy categories")
    cat_df = pd.DataFrame(
        [(row.category.value, 1) for row in discrepancies],
        columns=["category", "count"],
    )
    cat_df = cat_df.groupby("category").sum().reset_index()
    st.bar_chart(cat_df.set_index("category"))

    st.subheader("Per-signal drilldown")
    drilldown_options = list(zip(metrics_df["group_id"], metrics_df["group"], strict=True))
    selected_idx = st.selectbox(
        "Group",
        options=list(range(len(drilldown_options))),
        format_func=lambda i: drilldown_options[i][1],
        index=0,
    )
    selected_group_id = drilldown_options[selected_idx][0]
    drilldown = _drilldown_dataframe(
        selected_group=selected_group_id, discrepancies=discrepancies, pd=pd
    )
    st.dataframe(drilldown, hide_index=True, use_container_width=True)


def _metrics_to_dataframe(metrics, *, label_map, pd):
    return pd.DataFrame(
        [
            {
                "group": resolve_group_label(m.group_id, label_map, max_len=0),
                "group_id": m.group_id,
                "n_signals": m.n_signals,
                "reported_win_rate": m.reported_win_rate,
                "actual_win_rate": m.actual_win_rate,
                "win_rate_gap": m.win_rate_gap,
                "avg_realized_pips": m.avg_realized_pips,
                "avg_realized_rr": m.avg_realized_rr,
                "ambiguous_bars": m.ambiguous_bars,
                "insufficient_data": m.insufficient_data,
            }
            for m in metrics
        ]
    )


def _tp_depth_to_dataframe(rows, *, label_map, pd):
    visible_levels = min(5, max(r.max_tp_level for r in rows))
    visible_levels = max(visible_levels, 1)
    records = []
    for row in rows:
        record: dict[str, object] = {
            "group": resolve_group_label(row.group_id, label_map, max_len=0),
            "group_id": row.group_id,
            "n_signals": row.n_signals,
            "n_reported": row.n_reported,
            "no_report_rate": row.no_report_rate,
            "sl_hit_rate": row.sl_hit_rate,
        }
        for level in range(1, visible_levels + 1):
            stat = row.level(level)
            record[f"tp{level}_hit_rate"] = stat.hit_rate if stat is not None else None
        records.append(record)
    return pd.DataFrame(records)


def _tp_depth_to_long(rows, *, label_map, pd):
    visible_levels = min(5, max(r.max_tp_level for r in rows))
    visible_levels = max(visible_levels, 1)
    records = []
    for row in rows:
        label = resolve_group_label(row.group_id, label_map, max_len=0)
        for level in range(1, visible_levels + 1):
            stat = row.level(level)
            if stat is None or stat.hit_rate is None:
                continue
            records.append(
                {
                    "group": label,
                    "group_id": row.group_id,
                    "tp_level": f"TP{level}",
                    "hit_rate": stat.hit_rate,
                }
            )
    return pd.DataFrame(records)


def _drilldown_dataframe(*, selected_group, discrepancies, pd):
    rows = [
        {
            "signal_id": d.signal_id,
            "reported_state": d.reported_state.value,
            "actual_state": d.actual_state.value,
            "category": d.category.value,
            "reported_pips": d.reported_pips,
            "actual_pips": d.actual_pips,
        }
        for d in discrepancies
        if d.group_id == selected_group
    ]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    main()
