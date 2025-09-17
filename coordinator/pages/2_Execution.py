import time

import streamlit as st

from common.utils import get_status_emoji
from coordinator.redis_io import (
    cancel_benchmark,
    collect_results_once,
    get_bench_meta,
    get_bench_repos,
    get_bench_workers,
    get_redis,
    list_benchmarks,
    now_iso,
    pair_key,
    reexecute_benchmark,
    retry_non_completed_benchmark,
    start_benchmark,
)
from coordinator.utils import (
    format_benchmark_header,
    get_query_bench_id,
    repos_to_table_df,
    set_query_bench_id,
    summarize_result_cell,
)

st.set_page_config(
    page_title="Execution",
    page_icon="🎈",
    layout="wide",
    initial_sidebar_state="expanded",
)

r = get_redis()
st.title("Execution")

# Resolve bench selection
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
left, mid, right = st.columns([6, 0.5, 3])
with left:
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
    expected = int(meta.get("expected_jobs", "0") or 0)
    workers = get_bench_workers(r, bench_id)
    repos = get_bench_repos(r, bench_id)

    created = meta.get("created_at") or meta.get("started_at") or "?"
    expected = meta.get("expected_jobs") or "?"
    st.markdown(
        format_benchmark_header(name, bench_id, created, expected),
        unsafe_allow_html=True,
    )
    st.caption(f"Workers: {', '.join(workers) if workers else '(none)'} · Status: {status}")

    # Action controls
    if status == "running":
        st.button("Start benchmark", type="primary", disabled=True)
    elif status == "created":
        if st.button("Start benchmark", type="primary"):
            issued = start_benchmark(r, bench_id)
            set_query_bench_id(bench_id)
            st.rerun()
    else:
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Start benchmark", type="primary"):
                only_nc = st.session_state.get("only_nc_toggle", False)
                if only_nc:
                    issued = retry_non_completed_benchmark(r, bench_id)
                    st.toast(f"Retried {issued} jobs (non-completed)")
                else:
                    issued = reexecute_benchmark(r, bench_id)
                    st.toast(f"Re-executed {issued} jobs")
                set_query_bench_id(bench_id)
                st.rerun()
        with c2:
            st.toggle("Only non-completed", value=False, key="only_nc_toggle")

with mid:
    st.write("")

with right:
    st.subheader("Notes")
    em = get_status_emoji
    notes = [
        f"{em('pending')} Pending: job queued or running.",
        f"{em('completed')} Completed: worker returned a CBOM successfully.",
        f"{em('failed')} Failed: worker errored while processing the repo.",
        f"{em('timeout')} Timeout: worker exceeded its time budget.",
        f"{em('cancelled')} Cancelled: job was cancelled before completion.",
    ]
    st.markdown("\n".join(f"- {line}" for line in notes))
# Auto-refresh is always on while running; no toggle needed

# Ingest results while running, and keep ingesting after cancellation to capture late results
if status in ("running", "cancelled"):
    done, total = collect_results_once(r, bench_id)
    current_status = get_bench_meta(r, bench_id).get("status", status)
    if total and done >= total and current_status == "running":
        r.hset(
            f"bench:{bench_id}",
            mapping={"status": "completed", "finished_at": now_iso()},
        )
        status = "completed"
        st.toast("Benchmark completed.")
        # Force a rerender so controls/status update immediately
        set_query_bench_id(bench_id)
        st.rerun()

# Compute grid
job_idx = r.hgetall(f"bench:{bench_id}:job_index") or {}
df = repos_to_table_df(repos)
if not df.empty:
    # Initialize worker columns
    for w in workers:
        df[w] = ""
    # Fill worker result cells
    for i, repo in enumerate(repos):
        full = repo.get("full_name")
        for w in workers:
            j_id = job_idx.get(pair_key(full, w))
            if j_id:
                stt = r.hget(f"bench:{bench_id}:job:{j_id}", "status") or ""
                raw = r.hget(f"bench:{bench_id}:job:{j_id}", "result_json")
                df.at[i, w] = summarize_result_cell(stt, raw)

    show_cols = ["repo", "info", "url"] + workers
    view_df = df.loc[:, show_cols].copy()
    st.dataframe(
        view_df,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("URL", display_text="🔗"),
        },
        width="stretch",
    )

    # Progress (full width)
    jobs = r.lrange(f"bench:{bench_id}:jobs", 0, -1) or []
    done = 0
    for job_id in jobs:
        stt = r.hget(f"bench:{bench_id}:job:{job_id}", "status") or ""
        if stt in ("completed", "failed", "cancelled"):
            done += 1
    total = len(jobs)
    if total > 0:
        st.progress(done / total, text=f"Completed {done}/{total}")
        st.caption(f"Expected jobs: {expected}")

# Place Show Analysis and Cancel side-by-side
# Show Analysis is enabled for completed/cancelled; Cancel only when running
analysis_disabled = status not in ("completed", "cancelled")
analysis_help = None
if status == "running":
    analysis_help = "Available after run completes or is cancelled"
elif status == "created":
    analysis_help = "Start the benchmark first"

col_a, col_b, _ = st.columns([1, 1, 5])
with col_a:
    if st.button("Show analysis", type="primary", disabled=analysis_disabled, help=analysis_help):
        set_query_bench_id(bench_id)
        st.switch_page("pages/3_Analysis.py")
with col_b:
    cancel_disabled = status != "running"
    if st.button("Cancel", type="primary", disabled=cancel_disabled):
        cancelled = cancel_benchmark(r, bench_id)
        st.toast(f"Cancelled {cancelled} pending jobs")
        set_query_bench_id(bench_id)
        st.rerun()

if status == "running":
    # Regular refresh cadence while running
    time.sleep(2)
    st.rerun()
