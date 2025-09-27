import json
import time
from types import SimpleNamespace

import altair as alt
import pandas as pd
import redis
import streamlit as st

from common.cbom_analysis import (
    analyze_cbom_json,
    component_counts_for_repo,
    load_components,
    render_similarity_matches,
    summarize_component_types,
    summarize_runtime_estimate,
)
from common.cbom_filters import (
    INCLUDE_COMPONENT_TYPE_ONLY,
    filter_cbom_components_include_only,
    is_included_component_type,
)
from common.utils import get_status_emoji, get_status_keys_order, status_text
from coordinator.logger_config import logger
from coordinator.redis_io import (
    collect_repo_cboms,
    enqueue_component_match_instruction,
    get_bench_meta,
    get_bench_repos,
    get_bench_workers,
    get_redis,
    list_benchmarks,
    pair_key,
    prepare_component_match_instruction,
)
from coordinator.utils import (
    build_repo_info_url_map,
    estimate_similarity_runtime,
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

if "component_similarity_jobs" not in st.session_state:
    st.session_state["component_similarity_jobs"] = {}

TREESIM_WORKER = "treesimilartiy"
TREESIM_QUEUE = f"jobs:{TREESIM_WORKER}"
TREESIM_RESULTS_LIST = f"results:{TREESIM_WORKER}"

PYQUN_WORKER = "pyqun"
PYQUN_QUEUE = f"jobs:{PYQUN_WORKER}"
PYQUN_RESULTS_LIST = f"results:{PYQUN_WORKER}"


def _latest_similarity_result(
    redis_conn: redis.Redis, repo_full_name: str, *, target_job_id: str | None = None, result_list=TREESIM_RESULTS_LIST
) -> dict | None:
    """Return the newest similarity result for repo (optionally matching job_id)."""

    entries = redis_conn.lrange(result_list, 0, -1)
    if not entries:
        return None

    target_repo = (repo_full_name or "").strip()
    target_job = target_job_id

    for entry_text in reversed(entries):
        try:
            entry_payload = json.loads(entry_text)
        except json.JSONDecodeError:
            continue

        if target_job and entry_payload.get("job_id") != target_job:
            continue

        if (entry_payload.get("repo_full_name") or "").strip() != target_repo:
            continue

        return entry_payload

    if target_job:
        return None

    # No repo match when scanning entire list
    return None


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
                except json.JSONDecodeError:
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
                        except (TypeError, ValueError):
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
                except json.JSONDecodeError:
                    pass

# Charts: status distribution per worker (fixed set of labels)
chart_data = []
order = [status_text(k, "label") for k in order_keys]
for w, cdict in worker_status_counts.items():
    for lbl in order:
        chart_data.append({"worker": w, "status": lbl, "count": int(cdict.get(lbl, 0))})
if chart_data:
    st.subheader("Job Status Summary")
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

if scatter_rows:
    st.subheader("Job Runtime Summary")
    df_scatter = pd.DataFrame(scatter_rows)
    worker_options = sorted(df_scatter["worker"].unique())
    col_filter, _, col_radio = st.columns([2, 0.3, 1])
    selected_workers = col_filter.multiselect(
        "Filter workers",
        worker_options,
        default=worker_options,
    )

    x_choice = col_radio.radio(
        "X-axis",
        options=("Repository size (KB)", "Reported components"),
        index=0,
        horizontal=True,
        key="scatter_x_axis",
    )
    if x_choice == "Repository size (KB)":
        x_field = "size_kb"
        x_title = "Repository size (KB)"
        x_scale = alt.Scale(zero=False)
    else:
        x_field = "components"
        x_title = "Reported components"
        x_scale = alt.Scale(zero=True)

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
                    f"{x_field}:Q",
                    title=x_title,
                    scale=x_scale,
                ),
                y=alt.Y(
                    "duration_sec:Q",
                    title="Duration (s)",
                    scale=alt.Scale(zero=False),
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
            base.transform_regression(x_field, "duration_sec", groupby=["worker"])
            .mark_line(size=2)
            .encode(
                x=alt.X(
                    f"{x_field}:Q",
                    title=x_title,
                    scale=x_scale,
                ),
                y=alt.Y(
                    "duration_sec:Q",
                    title="Duration (s)",
                    scale=alt.Scale(zero=False),
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
    component_tables = summarize_component_types(comp_rows, workers)
    if not component_tables:
        st.caption("No component type information available yet.")
    else:
        bench_jobs_state = st.session_state["component_similarity_jobs"].setdefault(bench_id, {})
        if any(key in bench_jobs_state for key in ("job_ids", "repo_map", "total")):
            bench_jobs_state.clear()
        repos_by_name = {(repo.get("full_name") or "").strip(): repo for repo in repos or []}
        for repo_name in sorted(component_tables.keys()):
            with st.expander(f"{repo_name}"):
                rows = component_tables.get(repo_name) or []
                if not rows:
                    st.caption("No component type information available yet.")
                    continue
                df_counts = pd.DataFrame(rows)
                # Read toggle state before rendering the table so it updates immediately on toggle
                repo_state_pre = bench_jobs_state.setdefault(repo_name, {})
                toggle_key = f"exclude_libs_{bench_id}_{repo_name}"
                exclude_non_crypto = bool(
                    st.session_state.get(toggle_key, repo_state_pre.get("exclude_libraries", False))
                )
                if exclude_non_crypto and "component.type" in df_counts.columns:
                    df_counts = df_counts[df_counts["component.type"].apply(is_included_component_type)]
                status_map = repo_worker_status.get(repo_name, {})
                rename_map = {
                    "component.type": "Component type",
                    "name": "Name",
                    "asset.type": "Asset type",
                }
                ordered_cols = ["component.type", "name", "asset.type"]
                # Compute per-worker totals after filtering so headers reflect current view
                worker_totals: dict[str, int] = {}
                for w in workers:
                    if w in df_counts.columns:
                        try:
                            col_values = pd.to_numeric(df_counts[w], errors="coerce").fillna(0).astype(int)
                            worker_totals[w] = int(col_values.sum())
                        except Exception:
                            # Fallback: best-effort integer coercion
                            total = 0
                            for v in list(df_counts[w] or []):
                                try:
                                    total += int(v)
                                except Exception:
                                    continue
                            worker_totals[w] = total
                    else:
                        worker_totals[w] = 0
                for w in workers:
                    status_key = status_map.get(w, "pending")
                    emoji = get_status_emoji(status_key)
                    total = worker_totals.get(w, 0)
                    rename_map[w] = f"{emoji} {w} ({total})" if emoji else f"{w} ({total})"
                    ordered_cols.append(w)
                df_counts = df_counts[ordered_cols]
                view_types = df_counts.rename(columns=rename_map)
                st.dataframe(
                    view_types,
                    hide_index=True,
                    width="stretch",
                )

                repo_state = bench_jobs_state.setdefault(repo_name, {})
                repo_obj = repos_by_name.get(repo_name) or {}

                job_id = repo_state.get("job_id")
                result_payload = repo_state.get("result")
                # Ensure a stable default for waiting flag per repo
                if "waiting_for_similarity" not in repo_state:
                    repo_state["waiting_for_similarity"] = False

                if toggle_key not in st.session_state:
                    st.session_state[toggle_key] = bool(repo_state.get("exclude_libraries", False))

                # Controls row: [Find button] [Algorithm radio] [Toggle]
                button_col, algo_col, toggle_col = st.columns([3, 3, 2])
                with toggle_col:
                    # More thorough: focus analysis on cryptographic assets only
                    label = "Include cryptographic-assets only"
                    exclude_libraries = st.toggle(label, key=toggle_key)

                repo_state["exclude_libraries"] = exclude_libraries
                # For estimates, when enabled, exclude all types except cryptographic-asset
                if exclude_libraries:
                    observed_types: set[str] = set()
                    for row in comp_rows or []:
                        if row.get("repo") == repo_name and isinstance(row.get("types"), dict):
                            observed_types.update(str(t).lower() for t in row.get("types").keys())
                    excluded_types = {t for t in observed_types if not is_included_component_type(t)}
                else:
                    excluded_types = None

                component_counts = component_counts_for_repo(
                    comp_rows,
                    repo_name,
                    workers,
                    excluded_types=excluded_types,
                )
                estimate_seconds = estimate_similarity_runtime(component_counts)
                if component_counts and any(component_counts.values()) and estimate_seconds and not result_payload:
                    toggle_col.caption(f"Estimated runtime: {summarize_runtime_estimate(estimate_seconds)}")

                # Do not eagerly fetch results on reruns (e.g., toggle changes).
                # Result fetching is driven only when a job is actively waiting.

                waiting_for_result = (
                    bool(repo_state.get("waiting_for_similarity")) and bool(job_id) and not result_payload
                )
                button_disabled = waiting_for_result
                with algo_col:
                    algorithm_choice = st.radio(
                        "Select matching algorithm",
                        ["Clustering Algorithm (RaQuN)", "Optimization Algorithm (JEDI)"],
                        key=f"algorithm_choice_{bench_id}_{repo_name}",
                        horizontal=True,
                    )
                if ("Optimization" in algorithm_choice):
                    match_queue = TREESIM_QUEUE
                    result_list = TREESIM_RESULTS_LIST
                else:
                    match_queue = PYQUN_QUEUE
                    result_list = PYQUN_RESULTS_LIST
                with button_col:
                    if st.button(
                        "Find similar components among workers",
                        key=f"treesimilarity_{bench_id}_{repo_name}",
                        disabled=button_disabled,
                    ):
                        instruction = prepare_component_match_instruction(
                            r, bench_id, repo_obj, exclude_types=exclude_libraries
                        )
                        if not instruction:
                            st.info("Need at least two completed CBOMs for this repository.")
                        else:
                            # Aggregate tool names and component counts from all CbomJsons, per tool
                            tool_stats = [(cbom.tool, len(cbom.components_as_json)) for cbom in instruction.CbomJsons]
                            num_components = sum(count for _, count in tool_stats)
                            stats_str = ", ".join(f"({tool}, {count})" for tool, count in tool_stats)
                            logger.info(
                                "Sending match instruction for repo «%s», "
                                "comparing %d cbomjsons: %s; total_components=%d",
                                instruction.repo_info.full_name,
                                len(instruction.CbomJsons),
                                stats_str,
                                num_components,
                            )
                            # Persist issued tool order and counts for later verification
                            repo_state["issued_tools"] = [t for t, _ in tool_stats]
                            repo_state["issued_counts"] = [c for _, c in tool_stats]
                            # Persist the full filtered component dicts used for this run to render real data later
                            # Build from the raw CBOMs so indices match the minimized list exactly
                            cbom_map_raw_at_issue = collect_repo_cboms(r, bench_id, repo_name, workers) or {}
                            issued_full_components = {}
                            issued_min_docs = {}
                            for cbom in instruction.CbomJsons:
                                raw_json = cbom_map_raw_at_issue.get(cbom.tool)
                                full_list = load_components(raw_json) if raw_json else []
                                if exclude_libraries:
                                    full_list = [
                                        c
                                        for c in full_list
                                        if isinstance(c, dict) and is_included_component_type(c.get("type"))
                                    ]
                                issued_full_components[cbom.tool] = full_list
                                issued_min_docs[cbom.tool] = list(cbom.components_as_json or [])
                            repo_state["issued_full_components"] = issued_full_components
                            repo_state["issued_minimized_documents"] = issued_min_docs
                            enqueue_component_match_instruction(r, instruction, match_queue)
                            # enqueue_component_match_instruction(r, instruction, TREESIM_QUEUE)
                            repo_state["job_id"] = instruction.job_id
                            repo_state.pop("result", None)
                            repo_state.pop("result_exclude_libraries", None)
                            repo_state.pop("cboms", None)
                            repo_state.pop("filtered_cboms", None)
                            repo_state["job_exclude_libraries"] = exclude_libraries
                            # Enter active waiting only on explicit user action
                            repo_state["waiting_for_similarity"] = True
                            st.session_state["component_similarity_jobs"][bench_id] = bench_jobs_state
                            st.rerun()

                if waiting_for_result and not result_payload:
                    result_payload = _latest_similarity_result(
                        r, repo_name, target_job_id=job_id, result_list=result_list
                    )
                    if result_payload:
                        repo_state["result"] = result_payload
                        # Clear waiting state now that we have a result
                        repo_state["waiting_for_similarity"] = False
                        st.session_state["component_similarity_jobs"][bench_id] = bench_jobs_state

                if waiting_for_result and not repo_state.get("result"):
                    poll_interval = 2.0
                    with st.spinner("Waiting for similarity result…", show_time=True):
                        while not repo_state.get("result"):
                            result_payload = _latest_similarity_result(
                                r,
                                repo_name,
                                target_job_id=repo_state.get("job_id"),
                                result_list=result_list,
                            )
                            if result_payload:
                                repo_state["result"] = result_payload
                                result_exclude_flag = repo_state.get(
                                    "job_exclude_libraries",
                                    repo_state.get("result_exclude_libraries", False),
                                )
                                repo_state["result_exclude_libraries"] = bool(result_exclude_flag)
                                repo_state["waiting_for_similarity"] = False
                                st.session_state["component_similarity_jobs"][bench_id] = bench_jobs_state
                                break
                            time.sleep(poll_interval)

                if repo_state.get("result"):
                    result_payload = repo_state["result"]
                    match_count = result_payload.get("match_count", 0)
                    tools = result_payload.get("tools") or []
                    duration = result_payload.get("duration_sec")
                    status_label = result_payload.get("status", "ok")
                    header = f"Status: {status_label}"
                    if duration is not None:
                        header += f" · {duration:.2f}s"
                    header += f" · Matches: {match_count}"
                    if tools:
                        header += f" · Tools: {', '.join(sorted(tools))}"
                    st.caption(header)
                    if status_label != "ok":
                        st.error(result_payload.get("error") or "Unknown error")
                    else:
                        matches = result_payload.get("matches") or []
                        if not matches:
                            st.info("No component matches were returned.")
                        else:
                            result_filter_enabled = bool(repo_state.get("result_exclude_libraries", False))
                            if result_filter_enabled:
                                st.caption("Non ‘cryptographic-asset’ components were excluded for this run.")
                            elif exclude_libraries:
                                st.caption(
                                    "Toggle is set to exclude non ‘cryptographic-asset’. Re-run similarity to apply."
                                )

                            cbom_map_raw = repo_state.get("cboms")
                            if cbom_map_raw is None:
                                cbom_map_raw = collect_repo_cboms(r, bench_id, repo_name, workers) or {}
                                repo_state.pop("filtered_cboms", None)
                                repo_state["cboms"] = cbom_map_raw
                            else:
                                cbom_map_raw = cbom_map_raw or {}

                            filtered_cache = repo_state.setdefault("filtered_cboms", {})
                            issued_tools = repo_state.get("issued_tools") or []
                            tools_for_render = tools or issued_tools
                            use_issued_full = bool(repo_state.get("issued_full_components")) and bool(tools_for_render)
                            use_issued_min = bool(repo_state.get("issued_minimized_documents")) and bool(
                                tools_for_render
                            )
                            if use_issued_full:
                                # Build synthetic CBOMs from the stored full components to ensure exact index mapping
                                issued_full = repo_state.get("issued_full_components") or {}
                                cbom_map_full = {}
                                for t in tools_for_render:
                                    comp_list = issued_full.get(t) or []
                                    cbom_map_full[t] = json.dumps({"components": comp_list}, ensure_ascii=False)
                                # Optionally build minimized map (used by the view switch)
                                cbom_map_min = None
                                if use_issued_min:
                                    issued_min = repo_state.get("issued_minimized_documents") or {}
                                    cbom_map_min = {}
                                    for t in tools_for_render:
                                        doc_list = issued_min.get(t) or []
                                        comps = []
                                        for s in doc_list:
                                            try:
                                                comps.append(json.loads(s))
                                            except json.JSONDecodeError:
                                                continue
                                        cbom_map_min[t] = json.dumps({"components": comps}, ensure_ascii=False)
                                # View mode selector (default Real)
                                view_key = f"view_mode_{bench_id}_{repo_name}"
                                options = ["Real (full)"] + (["Minimized (matched)"] if cbom_map_min else [])
                                choice = st.radio("View", options=options, index=0, key=view_key, horizontal=True)
                                cbom_map_for_render = (
                                    cbom_map_min if (cbom_map_min and choice.startswith("Minimized")) else cbom_map_full
                                )
                            else:
                                if result_filter_enabled:
                                    cbom_map_for_render = filtered_cache.get(True)
                                    if cbom_map_for_render is None:
                                        cbom_map_for_render = {
                                            tool: filter_cbom_components_include_only(
                                                payload, set(INCLUDE_COMPONENT_TYPE_ONLY)
                                            )
                                            for tool, payload in cbom_map_raw.items()
                                        }
                                        filtered_cache[True] = cbom_map_for_render
                                else:
                                    cbom_map_for_render = filtered_cache.get(False)
                                    if cbom_map_for_render is None:
                                        filtered_cache[False] = cbom_map_raw
                                        cbom_map_for_render = cbom_map_raw

                            renderer = SimpleNamespace(
                                info=st.info,
                                json=st.json,
                                caption=st.caption,
                                expander=st.expander,
                                columns=st.columns,
                            )

                            # Optional sanity check: issued counts vs reconstructed counts
                            issued_tools = repo_state.get("issued_tools") or []
                            issued_counts = repo_state.get("issued_counts") or []
                            effective_tools = tools_for_render
                            if (
                                effective_tools
                                and issued_tools
                                and issued_counts
                                and len(issued_tools) == len(issued_counts)
                            ):
                                # Build current counts based on the CBOMs used for rendering
                                current_counts = []
                                for t in effective_tools:
                                    raw_json = cbom_map_for_render.get(t)
                                    comps = load_components(raw_json) if raw_json else []
                                    current_counts.append(len(comps))
                                if current_counts != issued_counts:
                                    st.warning(
                                        "Component counts differ between issued job and current view. "
                                        "Indices may not align perfectly."
                                    )

                            render_similarity_matches(
                                matches=matches,
                                tools=tools_for_render,
                                cboms_by_tool=cbom_map_for_render,
                                renderer=renderer,
                                safe_int_func=safe_int,
                            )

                            st.session_state["component_similarity_jobs"][bench_id] = bench_jobs_state
