import json

import altair as alt
import pandas as pd
import streamlit as st

from common.cbom_analysis import analyze_cbom_json
from common.utils import get_status_emoji, get_status_keys_order, status_text
from coordinator.redis_io import (
    get_bench_meta,
    get_bench_repos,
    get_bench_workers,
    get_redis,
    list_benchmarks,
    pair_key,
)
from coordinator.utils import (
    build_repo_info_url_map,
    format_benchmark_header,
    get_query_bench_id,
    safe_int,
)

st.set_page_config(
    page_title="Analysis",
    page_icon="🎈",
    layout="wide",
    initial_sidebar_state="expanded",
)

r = get_redis()
st.title("Analysis")

bench_id_hint = get_query_bench_id() or st.session_state.get("created_bench_id")
benches = list_benchmarks(r)
if not benches:
    st.info("No benchmarks found. Please create one first.")
    st.stop()

labels = [f"{m.get('name', '(unnamed)')} · {bid[:8]} · {m.get('status', '?')}" for bid, m in benches]
default_idx = 0
if bench_id_hint:
    try:
        default_idx = [bid for bid, _ in benches].index(bench_id_hint)
    except ValueError:
        default_idx = 0
idx = st.selectbox(
    "Select benchmark",
    options=list(range(len(benches))),
    index=default_idx,
    format_func=lambda i: labels[i],
)
bench_id, _ = benches[idx]

meta = get_bench_meta(r, bench_id)
name = meta.get("name", bench_id)
status = meta.get("status", "?")
workers = get_bench_workers(r, bench_id)
repos = get_bench_repos(r, bench_id)

created = meta.get("created_at") or meta.get("started_at") or "?"
expected = meta.get("expected_jobs") or "?"

st.markdown(format_benchmark_header(name, bench_id, created, expected), unsafe_allow_html=True)

job_idx = r.hgetall(f"bench:{bench_id}:job_index") or {}
order_keys = get_status_keys_order()
worker_status_counts = {w: {status_text(k, "label"): 0 for k in order_keys} for w in workers}
comp_rows = []  # rows: repo, worker, total_components, types (Counter)
repo_worker_status: dict[str, dict[str, str]] = {}

repo_sizes = {}
for repo in repos:
    full = (repo.get("full_name") or "").strip()
    if not full:
        continue
    size_val = repo.get("size")
    if size_val in (None, ""):
        size_val = repo.get("size_kb")
    repo_sizes[full] = safe_int(size_val, default=0)

scatter_rows: list[dict] = []

jobs = r.lrange(f"bench:{bench_id}:jobs", 0, -1) or []
job_meta_cache = {}
for j_id in jobs:
    job_meta_cache[j_id] = r.hgetall(f"bench:{bench_id}:job:{j_id}") or {}

for repo in repos:
    full = repo.get("full_name")
    for w in workers:
        j_id = job_idx.get(pair_key(full, w))
        if not j_id:
            # treat as pending if job wasn't issued
            worker_status_counts[w][status_text("pending", "label")] += 1
            repo_worker_status.setdefault(full, {})[w] = "pending"
            continue
        meta = job_meta_cache.get(j_id) or {}
        stt = meta.get("status") or ""
        status_key = "pending"
        if stt == "completed":
            status_key = "completed"
        elif stt == "failed":
            # Distinguish timeout using worker payload
            raw = meta.get("result_json")
            is_timeout = False
            if raw:
                try:
                    payload = json.loads(raw)
                    if payload.get("status") == "timeout":
                        is_timeout = True
                except Exception:
                    pass
            status_key = "timeout" if is_timeout else "failed"
        elif stt == "cancelled":
            status_key = "cancelled"
        else:
            status_key = "pending"
        worker_status_counts[w][status_text(status_key, "label")] += 1
        repo_worker_status.setdefault(full, {})[w] = status_key

        # Components only for completed jobs
        if stt == "completed":
            raw = meta.get("result_json")
            if raw:
                try:
                    payload = json.loads(raw)
                    cbom_text = payload.get("json", "{}")
                    total, types, type_assets, type_asset_names = analyze_cbom_json(cbom_text, w)
                    comp_rows.append(
                        {
                            "repo": full,
                            "worker": w,
                            "total_components": total,
                            "types": dict(types),
                            "type_asset_counts": dict(type_assets),
                            "type_asset_name_counts": dict(type_asset_names),
                        }
                    )
                    duration = payload.get("duration_sec")
                    if duration is not None:
                        try:
                            duration = float(duration)
                        except Exception:
                            duration = None
                    if duration is not None:
                        size_val = repo_sizes.get(full)
                        if not size_val:
                            repo_info = payload.get("repo_info") or {}
                            size_val = safe_int(
                                repo_info.get("size") or repo_info.get("size_kb"),
                                default=0,
                            )
                        scatter_rows.append(
                            {
                                "repo": full,
                                "worker": w,
                                "size_kb": size_val,
                                "duration_sec": duration,
                                "components": total,
                            }
                        )
                except Exception:
                    pass

# Charts: status distribution per worker (fixed set of labels)
chart_data = []
order = [status_text(k, "label") for k in order_keys]
for w, cdict in worker_status_counts.items():
    for lbl in order:
        chart_data.append({"worker": w, "status": lbl, "count": int(cdict.get(lbl, 0))})
if chart_data:
    st.subheader("Job Status Distribution")
    df_status = pd.DataFrame(chart_data)
    # Build color scale using centralized status meta and order
    from common.utils import get_status_meta

    status_meta = get_status_meta()
    domain = order
    # Map from label to color via key order
    key_order = get_status_keys_order()
    label_to_color = {status_text(k, "label"): status_meta[k].get("color", "#999999") for k in key_order}
    colors = [label_to_color.get(lbl, "#999999") for lbl in domain]

    chart = (
        alt.Chart(df_status)
        .mark_bar()
        .encode(
            x=alt.X("worker:N", title="Worker"),
            y=alt.Y("count:Q", title="Jobs"),
            color=alt.Color(
                "status:N",
                scale=alt.Scale(domain=domain, range=colors),
                legend=alt.Legend(title="Status"),
            ),
        )
        .properties(height=500)
    )
    st.altair_chart(chart, use_container_width=True)

if scatter_rows:
    st.subheader("Runtime Scatter")
    df_scatter = pd.DataFrame(scatter_rows)
    worker_options = sorted(df_scatter["worker"].unique())
    col_filter, _, col_radio = st.columns([2, 0.3, 1])
    selected_workers = col_filter.multiselect(
        "Filter workers",
        worker_options,
        default=worker_options,
    )

    y_choice = col_radio.radio(
        "Y-axis",
        options=("Repository size (KB)", "Reported components"),
        index=0,
        horizontal=True,
        key="scatter_y_axis",
    )
    if y_choice == "Repository size (KB)":
        y_field = "size_kb"
        y_title = "Repository size (KB)"
        y_scale = alt.Scale(zero=False)
    else:
        y_field = "components"
        y_title = "Reported components"
        y_scale = alt.Scale(zero=True)

    filtered = df_scatter[df_scatter["worker"].isin(selected_workers)]

    if filtered.empty:
        st.info("No data points for the selected filters.")
    else:
        worker_domain = worker_options
        base = alt.Chart(filtered)
        scatter_layer = (
            base.mark_circle(size=80, opacity=0.75)
            .encode(
                x=alt.X(
                    "duration_sec:Q",
                    title="Duration (s)",
                    scale=alt.Scale(zero=False),
                ),
                y=alt.Y(
                    f"{y_field}:Q",
                    title=y_title,
                    scale=y_scale,
                ),
                color=alt.Color(
                    "worker:N",
                    title="Worker",
                    scale=alt.Scale(domain=worker_domain, scheme="tableau10"),
                    legend=alt.Legend(orient="right", values=selected_workers),
                ),
                tooltip=[
                    alt.Tooltip("repo:N", title="Repository"),
                    alt.Tooltip("worker:N", title="Worker"),
                    alt.Tooltip("duration_sec:Q", title="Duration (s)", format=",.2f"),
                    alt.Tooltip("size_kb:Q", title="Size (KB)", format=","),
                    alt.Tooltip("components:Q", title="Components"),
                ],
            )
            .properties(height=500)
        )
        trend_layer = (
            base.transform_regression("duration_sec", y_field, groupby=["worker"])
            .mark_line(size=2)
            .encode(
                x=alt.X(
                    "duration_sec:Q",
                    title="Duration (s)",
                    scale=alt.Scale(zero=False),
                ),
                y=alt.Y(
                    f"{y_field}:Q",
                    title=y_title,
                    scale=y_scale,
                ),
                color=alt.Color(
                    "worker:N",
                    title="Worker",
                    scale=alt.Scale(domain=worker_domain, scheme="tableau10"),
                    legend=None,
                ),
            )
            .properties(height=500)
        )
        st.altair_chart((scatter_layer + trend_layer).interactive(), use_container_width=True)

# Worker-level summary table
st.subheader("Worker Summary")
summary_rows = []
for w in workers:
    job_count = len(repos)
    completed = int(worker_status_counts[w].get(status_text("completed", "label"), 0))
    failed = int(worker_status_counts[w].get(status_text("failed", "label"), 0))
    timeout = int(worker_status_counts[w].get(status_text("timeout", "label"), 0))
    empty_cboms = 0
    for row in comp_rows:
        if row.get("worker") == w and int(row.get("total_components") or 0) == 0:
            empty_cboms += 1
    summary_rows.append(
        {
            "worker": w,
            "jobs": job_count,
            "failed": failed,
            "timeout": timeout,
            "completed": completed,
            "empty cboms": empty_cboms,
        }
    )
if summary_rows:
    df_sum = pd.DataFrame(summary_rows)
    # Add emojis to status columns
    df_sum = df_sum.rename(
        columns={
            "completed": f"{get_status_emoji('completed')} completed",
            "failed": f"{get_status_emoji('failed')} failed",
            "timeout": f"{get_status_emoji('timeout')} timeout",
        }
    )
    st.dataframe(df_sum, hide_index=True, width="stretch")

# Components insights
st.subheader("Components Summary")
if comp_rows:
    comp_df = pd.DataFrame(comp_rows)
    # Pivot: rows=repo, cols=workers, values=total_components
    pivot = (
        comp_df.pivot_table(index="repo", columns="worker", values="total_components", aggfunc="max")
        .fillna(0)
        .astype(int)
    )
    # Ensure all workers are present as columns
    for w in workers:
        if w not in pivot.columns:
            pivot[w] = 0
    pivot = pivot[workers]
    # Add split columns: info (lang/stars/size) and URL link
    pivot_reset = pivot.reset_index()
    info_url_map = build_repo_info_url_map(repos)
    pivot_reset["info"] = pivot_reset["repo"].apply(lambda name: (info_url_map.get(name, {}) or {}).get("info", ""))
    pivot_reset["url"] = pivot_reset["repo"].apply(lambda name: (info_url_map.get(name, {}) or {}).get("url", ""))
    # Order columns: repo/info/url then worker counts
    ordered = ["repo", "info", "url"] + [w for w in workers]
    view_df = pivot_reset[ordered]

    st.caption("Per-repo component counts (by worker)")
    st.dataframe(
        view_df,
        hide_index=True,
        column_config={"url": st.column_config.LinkColumn("URL", display_text="🔗")},
        width="stretch",
    )

    st.subheader("Component Types")
    type_summary: dict[str, dict[tuple[str, str, str], dict[str, int]]] = {}
    for row in comp_rows:
        repo_name = row.get("repo")
        worker_name = row.get("worker")
        combo_counts = row.get("type_asset_counts") or {}
        detail_counts = row.get("type_asset_name_counts") or {}
        type_counts = row.get("types") or {}
        if not repo_name or not worker_name:
            continue
        repo_map = type_summary.setdefault(repo_name, {})
        if detail_counts:
            source_iter = detail_counts.items()
        elif combo_counts:
            source_iter = [
                ((str(comp_type) or "(unknown)", str(asset_label or ""), ""), count)
                for (comp_type, asset_label), count in combo_counts.items()
            ]
        else:
            source_iter = [((str(comp_type) or "(unknown)", "", ""), count) for comp_type, count in type_counts.items()]
        for key, count in source_iter:
            if isinstance(key, tuple) and len(key) == 3:
                base_type, asset_label, name = key
            elif isinstance(key, tuple) and len(key) == 2:
                base_type, asset_label = key
                name = ""
            else:
                base_type, asset_label, name = key, "", ""
            base_type_str = str(base_type or "(unknown)")
            asset_label_str = str(asset_label or "")
            name_str = str(name or "")
            type_map = repo_map.setdefault((base_type_str, asset_label_str, name_str), {})
            type_map[worker_name] = safe_int(count)

    if not type_summary:
        st.caption("No component type information available yet.")
    else:
        for repo_name in sorted(type_summary.keys()):
            with st.expander(f"{repo_name}"):
                type_map = type_summary[repo_name]
                rows = []
                for (comp_type, asset_type, name), counts in sorted(type_map.items()):
                    entry = {
                        "component.type": comp_type,
                        "asset.type": asset_type,
                        "name": name,
                    }
                    for w in workers:
                        entry[w] = safe_int(counts.get(w, 0))
                    rows.append(entry)
                if not rows:
                    st.caption("No component type information available yet.")
                    continue
                df_counts = pd.DataFrame(rows)
                status_map = repo_worker_status.get(repo_name, {})
                rename_map = {
                    "component.type": "Component type",
                    "name": "Name",
                    "asset.type": "Asset type",
                }
                ordered_cols = ["component.type", "name", "asset.type"]
                for w in workers:
                    status_key = status_map.get(w, "pending")
                    emoji = get_status_emoji(status_key)
                    rename_map[w] = f"{emoji} {w}" if emoji else w
                    ordered_cols.append(w)
                df_counts = df_counts[ordered_cols]
                view_types = df_counts.rename(columns=rename_map)
                st.dataframe(
                    view_types,
                    hide_index=True,
                    width="stretch",
                )
