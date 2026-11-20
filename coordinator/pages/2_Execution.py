import time
from pathlib import Path

import streamlit as st

from common.utils import get_status_emoji, get_status_help, get_status_keys_order, status_text
from coordinator.logger_config import logger
from coordinator.redis_io import (
    JOB_SUMMARY_FIELDS,
    cancel_inspection,
    collect_results_once,
    count_done_jobs,
    dead_workers,
    get_insp_meta,
    get_insp_repos,
    get_insp_waves,
    get_insp_workers,
    get_redis,
    list_inspections,
    now_iso,
    pair_key,
    reexecute_inspection,
    retry_non_completed_inspection,
    start_inspection,
)
from coordinator.scheduler import ensure_scheduler
from coordinator.utils import (
    format_inspection_header,
    get_favicon_path,
    get_query_insp_id,
    repos_to_table_df,
    set_query_insp_id,
    summarize_result_cell,
)

ico_path = Path(get_favicon_path())
st.set_page_config(
    page_title="Execution",
    page_icon=str(ico_path),
    layout="wide",
    initial_sidebar_state="expanded",
)

r = get_redis()
ensure_scheduler()
st.title("Execution")

# Resolve insp selection
insp_id_hint = get_query_insp_id() or st.session_state.get("created_insp_id")
inspections = list_inspections(r)
if not inspections:
    st.info("No inspections found. Please create one first.")
    st.stop()

labels = [f"{m.get('name', '(unnamed)')} · {bid[:8]} · {m.get('status', '?')}" for bid, m in inspections]
default_idx = 0
if insp_id_hint:
    try:
        default_idx = [bid for bid, _ in inspections].index(insp_id_hint)
    except ValueError:
        default_idx = 0
left, mid, right = st.columns([6, 0.5, 3])
with left:
    idx = st.selectbox(
        "Select inspection",
        options=list(range(len(inspections))),
        index=default_idx,
        format_func=lambda i: labels[i],
    )
    insp_id, _ = inspections[idx]

    meta = get_insp_meta(r, insp_id)
    name = meta.get("name", insp_id)
    status = meta.get("status", "?")
    expected = int(meta.get("expected_jobs", "0") or 0)
    workers = get_insp_workers(r, insp_id)
    repos = get_insp_repos(r, insp_id)

    # Determine button label based on repo count
    is_batch = len(repos) > 1
    start_button_label = "Start Batch Inspection" if is_batch else "Start Inspection"

    created = meta.get("created_at") or meta.get("started_at") or "?"
    expected = meta.get("expected_jobs") or "?"
    st.markdown(
        format_inspection_header(name, insp_id, created, expected),
        unsafe_allow_html=True,
    )
    st.caption(f"Workers: {', '.join(workers) if workers else '(none)'} · Status: {status}")
    waves, wave_no = get_insp_waves(meta)
    if len(waves) > 1 and status in ("running", "completed", "cancelled"):
        budget = meta.get("memory_budget_gb") or "?"
        plan = " → ".join(
            ("**" if i == wave_no else "") + ", ".join(w) + ("**" if i == wave_no else "")
            for i, w in enumerate(waves, 1)
        )
        st.caption(
            f"Memory budget {budget} GiB: wave {wave_no}/{len(waves)} · {plan} · "
            "⏸️ jobs of later waves are held until the current wave has finished"
        )
    down = dead_workers(r, workers)
    if down and status in ("created", "running"):
        st.warning(
            "No live worker for: " + ", ".join(f"`{w}`" for w in down) + ". "
            "Their jobs will stay queued until the container is up (`make ps`, `make logs`)."
        )

    # Action controls
    if status == "running":
        st.button(start_button_label, type="primary", disabled=True)
    elif status == "created":
        if st.button(start_button_label, type="primary"):
            logger.info("Starting inspection: %s", insp_id)
            issued = start_inspection(r, insp_id)
            set_query_insp_id(insp_id)
            st.rerun()
    else:
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button(start_button_label, type="primary"):
                only_nc = st.session_state.get("only_nc_toggle", False)
                if only_nc:
                    logger.info("Retrying non-completed jobs for inspection: %s", insp_id)
                    issued = retry_non_completed_inspection(r, insp_id)
                    st.toast(f"Retried {issued} jobs (non-completed)")
                else:
                    logger.info("Re-executing all jobs for inspection: %s", insp_id)
                    issued = reexecute_inspection(r, insp_id)
                    st.toast(f"Re-executed {issued} jobs")
                set_query_insp_id(insp_id)
                st.rerun()
        with c2:
            st.toggle("Only non-completed", value=False, key="only_nc_toggle")

with mid:
    st.write("")

with right:
    st.subheader("Notes")
    notes = [f"{get_status_emoji(k)} {status_text(k, 'label')}: {get_status_help(k)}." for k in get_status_keys_order()]
    st.markdown("\n".join(f"- {line}" for line in notes))
# Auto-refresh is always on while running; no toggle needed

# Ingest results while running, and keep ingesting after cancellation to capture late results
if status in ("running", "cancelled"):
    done, total = collect_results_once(r, insp_id)
    current_status = get_insp_meta(r, insp_id).get("status", status)
    if total and done >= total and current_status == "running":
        r.hset(
            f"insp:{insp_id}",
            mapping={"status": "completed", "finished_at": now_iso()},
        )
        status = "completed"
        st.toast("Inspection completed.")
        # Force a rerender so controls/status update immediately
        set_query_insp_id(insp_id)
        st.rerun()

# Compute grid
job_idx = r.hgetall(f"insp:{insp_id}:job_index") or {}
df = repos_to_table_df(repos)
if not df.empty:
    # Initialize worker columns
    for w in workers:
        df[w] = ""
    # Fill worker result cells (one pipelined round-trip; never loads the CBOM bodies)
    cells: list[tuple[int, str, str]] = []
    pipe = r.pipeline()
    for i, repo in enumerate(repos):
        full = repo.get("full_name")
        for w in workers:
            j_id = job_idx.get(pair_key(full, w))
            if j_id:
                cells.append((i, w, j_id))
                pipe.hmget(f"insp:{insp_id}:job:{j_id}", JOB_SUMMARY_FIELDS)
    for (i, w, _), values in zip(cells, pipe.execute(), strict=True):
        df.at[i, w] = summarize_result_cell(dict(zip(JOB_SUMMARY_FIELDS, values, strict=True)))

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
    done, total = count_done_jobs(r, insp_id)
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
    analysis_help = "Start the inspection first"

col_a, col_b, _ = st.columns([1, 1, 5])
with col_a:
    if st.button("Show Analysis", type="primary", disabled=analysis_disabled, help=analysis_help):
        set_query_insp_id(insp_id)
        st.switch_page("pages/3_Analysis.py")
with col_b:
    cancel_disabled = status != "running"
    if st.button("Cancel", type="primary", disabled=cancel_disabled):
        cancelled = cancel_inspection(r, insp_id)
        st.toast(f"Cancelled {cancelled} queued, held or running jobs")
        set_query_insp_id(insp_id)
        st.rerun()

if status == "running":
    # Regular refresh cadence while running
    time.sleep(2)
    st.rerun()
