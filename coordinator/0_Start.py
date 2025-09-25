import json
import os

import streamlit as st

from common.utils import (
    get_available_workers,
    get_project_version_from_toml,
    get_status_emoji,
)
from coordinator.redis_io import delete_benchmark, get_redis, list_benchmarks
from coordinator.utils import (
    build_minimal_config_json,
    human_duration,
    set_query_bench_id,
)
from coordinator.logger_config import logger


print("[coordinator] Starting Streamlit app…")
logger.info("Coordinator: starting Streamlit app (version=%s)", getattr(st, "__version__", "?"))

st.set_page_config(
    page_title="Start",
    page_icon="🎈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Establish Redis connection early to report readiness in container logs
try:
    r = get_redis()
    print("[coordinator] Connected to Redis")
    logger.info(
        "Coordinator: connected to Redis host=%s port=%s",
        os.getenv("REDIS_HOST", "localhost"),
        os.getenv("REDIS_PORT", "6379"),
    )
except Exception as e:
    logger.error("Coordinator: failed to connect to Redis: %s", e)
    raise


version = get_project_version_from_toml()

st.markdown(
    f"""
    <div style='display:flex;align-items:center;justify-content:space-between;'>
      <div style='display:flex;align-items:center;'>
        <h1 style='color:#FF4B4B;font-size:68px;margin:0;'>BF-CBOM</h1>
        <span style='color:#888;font-size:1.3rem;font-family:monospace;
                font-weight:400;margin-left:0.8rem;margin-top:1.5em;'>v{version}</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.caption("Benchmarking Framework for Cryptography Bill of Material (CBOM) Generator Tools")

# Top action: create new benchmark
if st.button("➕ Create New Benchmark", type="primary"):
    logger.info("UI: navigate to Setup page")
    st.switch_page("pages/1_Setup.py")

st.divider()

# Below: Existing benchmarks (left) and Redis stats (right) with a thin divider
left, mid, right = st.columns([6, 0.02, 4])

with left:
    st.subheader("Existing Benchmarks")
    benches = list_benchmarks(r)
    if not benches:
        st.info("No benchmarks yet. Create one to get started.")
    else:
        for bid, meta in benches:
            name = meta.get("name", "(unnamed)")
            status = meta.get("status", "?")
            expected = int(meta.get("expected_jobs", "0") or 0)
            created = meta.get("created_at") or meta.get("started_at") or "?"
            cols = st.columns([4, 2, 3], vertical_alignment="top")
            with cols[0]:
                st.markdown(
                    (
                        f"**{name}**<br/>"
                        f"<span style='opacity:0.75; font-size:0.9rem;'>"
                        f"<b>ID:</b> {bid}<br/>"
                        f"<b>Created:</b> {created}  ·  <b>Jobs:</b> {expected}"
                        f"</span>"
                    ),
                    unsafe_allow_html=True,
                )
            with cols[1]:
                st.markdown(
                    f"<span style='font-size:0.95rem;'>{status}</span>",
                    unsafe_allow_html=True,
                )
            with cols[2]:
                b1, b2, b3 = st.columns([1, 1, 0.5], vertical_alignment="top")
                with b1:
                    if st.button("Execution", key=f"run_{bid}"):
                        # Preselect this benchmark on the next page via query param and session hint
                        set_query_bench_id(bid)
                        st.session_state["created_bench_id"] = bid
                        logger.info("UI: open Execution for bench %s", bid)
                        st.switch_page("pages/2_Execution.py")
                with b2:
                    if st.button("Analysis", key=f"ana_{bid}"):
                        set_query_bench_id(bid)
                        st.session_state["created_bench_id"] = bid
                        logger.info("UI: open Analysis for bench %s", bid)
                        st.switch_page("pages/3_Analysis.py")
                with b3:
                    d1, d2 = st.columns([1, 1], vertical_alignment="top")
                    with d1:
                        try:
                            # Build minimal config text for this benchmark row
                            workers = st.session_state.get("_tmp_workers", None)  # unused guard
                            # Defer to utility to keep shape consistent
                            repos_key = f"bench:{bid}:repos"
                            repos = [__import__("json").loads(x) for x in r.lrange(repos_key, 0, -1) or []]
                            name_txt = meta.get("name", bid)
                            # Workers list is stored in bench meta as JSON
                            try:
                                workers_list = __import__("json").loads(meta.get("workers_json", "[]"))
                            except Exception:
                                workers_list = []
                            cfg_text = build_minimal_config_json(name=name_txt, workers=workers_list, repos=repos)
                            st.download_button(
                                "⬇️",
                                data=cfg_text,
                                file_name=f"bench-{bid[:8]}.json",
                                help="Download config (JSON)",
                                key=f"dl_{bid}",
                            )
                        except Exception:
                            pass
                    with d2:
                        if st.button("🗑️", key=f"del_{bid}", help="Delete benchmark"):
                            deleted = delete_benchmark(r, bid)
                            st.toast(f"Deleted benchmark {bid[:8]} (jobs removed: {deleted})")
                            st.rerun()

with mid:
    # Visual thin divider (approx height)
    st.markdown(
        "<div style='width:1px;height:100vh;min-height:100vh;background:#e0e0e0;margin:0 auto;'></div>",
        unsafe_allow_html=True,
    )

with right:
    st.subheader("Redis Stats")
    try:
        info = r.info() or {}
    except Exception:
        info = {}
    used_mem = info.get("used_memory_human") or "?"
    sys_mem = info.get("total_system_memory_human") or "?"
    peak_mem = info.get("used_memory_peak_human") or info.get("used_memory_peak") or "?"
    keys = r.dbsize()
    total_benches = len(benches)

    # Global job stats across all benches
    status_counts = {k: 0 for k in ("completed", "failed", "cancelled", "pending", "timeout")}
    total_duration = 0.0
    duration_count = 0
    try:
        for key in r.scan_iter("bench:*:job:*"):
            stt = (r.hget(key, "status") or "").lower()
            if stt == "completed":
                status_counts["completed"] += 1
            elif stt == "cancelled":
                status_counts["cancelled"] += 1
            elif stt == "failed":
                # Inspect payload to separate timeouts
                raw = r.hget(key, "result_json")
                if raw:
                    try:
                        payload = json.loads(raw)
                        if (payload or {}).get("status") == "timeout":
                            status_counts["timeout"] += 1
                        else:
                            status_counts["failed"] += 1
                    except Exception:
                        status_counts["failed"] += 1
                else:
                    status_counts["failed"] += 1
            else:
                status_counts["pending"] += 1
            raw = r.hget(key, "result_json")
            if raw:
                try:
                    payload = json.loads(raw)
                    dur = payload.get("duration_sec")
                    if isinstance(dur, int | float):
                        total_duration += float(dur)
                        duration_count += 1
                except Exception:
                    pass
    except Exception:
        pass

    # Row 1: uptime, connected clients, Redis keys
    try:
        uptime_s = int(info.get("uptime_in_seconds", 0) or 0)
    except Exception:
        uptime_s = 0
    uptime_txt = human_duration(uptime_s)
    clients = int(info.get("connected_clients") or 0)
    r1c1, r1c2, r1c3 = st.columns(3)
    with r1c1:
        st.metric("uptime", uptime_txt)
    with r1c2:
        st.metric("connected clients", f"{clients:,}")
    with r1c3:
        st.metric("Redis keys", f"{keys:,}")

    # Row 2: used memory trio
    r2c1, r2c2, r2c3 = st.columns(3)
    with r2c1:
        st.metric("used memory", str(used_mem))
    with r2c2:
        st.metric("available memory", str(sys_mem))
    with r2c3:
        st.metric("peak memory", str(peak_mem))

    # Last row: benchmarks, total jobs, and per-status breakdown (as caption next to jobs)
    total_jobs = sum(status_counts.values())
    left, right = st.columns([1, 3])
    with left:
        st.metric("benchmarks", f"{total_benches:,}")
    with right:
        j_left, j_right = st.columns([1, 3])
        with j_left:
            st.metric("jobs", f"{total_jobs:,}")
        with j_right:
            em = get_status_emoji
            breakdown = " · ".join(
                [
                    f"{em('completed')} {status_counts['completed']:,}",
                    f"{em('failed')} {status_counts['failed']:,}",
                    f"{em('timeout')} {status_counts['timeout']:,}",
                    f"{em('cancelled')} {status_counts['cancelled']:,}",
                    f"{em('pending')} {status_counts['pending']:,}",
                ]
            )
            st.markdown(
                f"<div style='display:flex;align-items:flex-end;height:4.5rem;padding-bottom:0.8rem;'>"
                f"<span style='opacity:0.65;font-size:0.95rem;'>{breakdown}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Full Redis info
    with st.expander("more details"):
        st.json(info or {})

    st.subheader("Worker Stats")
    # Available workers discovered from the repo
    try:
        workers = get_available_workers()
    except Exception:
        workers = []
    total_time_s = int(total_duration)
    avg_time_s = int(total_duration / duration_count) if duration_count else 0
    w1, w2, w3 = st.columns(3)
    with w1:
        st.metric("available workers", f"{len(workers):,}")
    with w2:
        st.metric("total working time", human_duration(total_time_s))
    with w3:
        st.metric("average working time", human_duration(avg_time_s))
