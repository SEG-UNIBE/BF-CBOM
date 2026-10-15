import os
from pathlib import Path

import streamlit as st

from common import config
from common.utils import (
    get_available_workers,
    get_project_version_from_toml,
    get_status_emoji,
    get_status_keys_order,
)
from coordinator.logger_config import logger
from coordinator.redis_io import JOB_SUMMARY_FIELDS, delete_inspection, get_redis, job_status_key, list_inspections
from coordinator.scheduler import ensure_scheduler
from coordinator.utils import (
    build_minimal_config_json,
    get_favicon_path,
    human_duration,
    safe_float,
    set_query_insp_id,
)

print("[coordinator] Starting Streamlit app…")
logger.info("Coordinator: starting Streamlit app (version=%s)", getattr(st, "__version__", "?"))

ico_path = Path(get_favicon_path())
st.set_page_config(
    page_title="Start",
    page_icon=str(ico_path),
    layout="wide",
    initial_sidebar_state="expanded",
)

# Establish Redis connection early to report readiness in container logs
try:
    r = get_redis()
    ensure_scheduler()
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

# Header with logo (left) and title + version (right) using columns
logo_path = Path(__file__).resolve().parents[1] / "docs" / "logo.svg"
col_logo, col_title = st.columns([5, 4], vertical_alignment="center")
with col_logo:
    try:
        # Pass SVG as a string to st.image for crisp rendering
        svg_text = logo_path.read_text(encoding="utf-8")
        st.image(svg_text, width="stretch")
    except Exception:
        st.markdown("<div style='font-size:48px'>🎈</div>", unsafe_allow_html=True)
with col_title:
    st.markdown(
        f"""
        <div style='display:flex; align-items:flex-end; height:72px;'>
            <span style='color:#888; font-size:1.3rem; font-family:monospace; font-weight:400;
                         margin-left:0.8rem; align-self:flex-end; margin-bottom:1px;'>v{version}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
st.markdown(
    "<p style='font-size:1.3rem; color:#888;'>Your <b>B</b>est <b>F</b>riend for Generating, Understanding, "
    "and Comparing Cryptography Bill of Materials (<b>CBOMs</b>)</p>",
    unsafe_allow_html=True,
)


# Top action: create new inspection
if st.button("➕ Create New Inspection", type="primary"):
    logger.info("UI: navigate to Setup page")
    st.switch_page("pages/1_Setup.py")

st.divider()

# Below: Existing inspections (left) and Redis stats (right) with a thin divider
left, mid, right = st.columns([6, 0.02, 4])

with left:
    st.subheader("Existing Inspections")
    inspections = list_inspections(r)
    if not inspections:
        st.info("No inspections yet. Create one to get started.")
    else:
        for iid, meta in inspections:
            name = meta.get("name", "(unnamed)")
            status = meta.get("status", "?")
            expected = int(meta.get("expected_jobs", "0") or 0)
            created = meta.get("created_at") or meta.get("started_at") or "?"
            cols = st.columns([4, 1, 3], vertical_alignment="top")
            with cols[0]:
                st.markdown(
                    (
                        f"**{name}**<br/>"
                        f"<span style='opacity:0.75; font-size:0.9rem;'>"
                        f"<b>ID:</b> {iid}<br/>"
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
                    if st.button("Execution", key=f"run_{iid}"):
                        # Preselect this inspection on the next page via query param and session hint
                        set_query_insp_id(iid)
                        st.session_state["created_insp_id"] = iid
                        logger.info("UI: open Execution for insp %s", iid)
                        st.switch_page("pages/2_Execution.py")
                with b2:
                    if st.button("Analysis", key=f"ana_{iid}"):
                        set_query_insp_id(iid)
                        st.session_state["created_insp_id"] = iid
                        logger.info("UI: open Analysis for insp %s", iid)
                        st.switch_page("pages/3_Analysis.py")
                with b3:
                    d1, d2 = st.columns([1, 1], vertical_alignment="top")
                    with d1:
                        try:
                            # Build minimal config text for this inspection row
                            workers = st.session_state.get("_tmp_workers", None)  # unused guard
                            # Defer to utility to keep shape consistent
                            repos_key = f"insp:{iid}:repos"
                            repos = [__import__("json").loads(x) for x in r.lrange(repos_key, 0, -1) or []]
                            name_txt = meta.get("name", iid)
                            # Workers list is stored in insp meta as JSON
                            try:
                                workers_list = __import__("json").loads(meta.get("workers_json", "[]"))
                            except Exception:
                                workers_list = []
                            cfg_text = build_minimal_config_json(name=name_txt, workers=workers_list, repos=repos)
                            st.download_button(
                                "⬇️",
                                data=cfg_text,
                                file_name=f"insp-{iid[:8]}.json",
                                help="Download config (JSON)",
                                key=f"dl_{iid}",
                            )
                        except Exception:
                            pass
                    with d2:
                        if st.button("🗑️", key=f"del_{iid}", help="Delete inspection"):
                            deleted = delete_inspection(r, iid)
                            st.toast(f"Deleted inspection {iid[:8]} (jobs removed: {deleted})")
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

    # Global job stats across all inspections (flat fields only; CBOM bodies are never loaded)
    status_counts = {k: 0 for k in get_status_keys_order()}
    total_duration = 0.0
    duration_count = 0
    try:
        job_keys = list(r.scan_iter("insp:*:job:*", count=500))
        pipe = r.pipeline()
        for key in job_keys:
            pipe.hmget(key, JOB_SUMMARY_FIELDS)
        for values in pipe.execute():
            meta = dict(zip(JOB_SUMMARY_FIELDS, values, strict=True))
            status_counts[job_status_key(meta)] += 1
            dur = safe_float(meta.get("duration_sec"))
            if dur is not None:
                total_duration += dur
                duration_count += 1
    except Exception:
        logger.exception("Failed to compute global job stats")

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

    # Last row: inspections, total jobs, and per-status breakdown (as caption next to jobs)
    total_jobs = sum(status_counts.values())
    left, right = st.columns([1, 3])
    with left:
        st.metric("inspections", f"{len(inspections):,}")
    with right:
        j_left, j_right = st.columns([1, 3])
        with j_left:
            st.metric("jobs", f"{total_jobs:,}")
        with j_right:
            em = get_status_emoji
            breakdown = " · ".join(f"{em(k)} {status_counts[k]:,}" for k in get_status_keys_order())
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

    # ============================================================
    # Effective configuration (read-only: values are bound at import time in
    # common.config and the workers; change docker/env/*.env or docker-compose.yml and restart)
    # ============================================================
    with st.expander("Configuration", expanded=False):
        st.caption(
            "Effective settings of this coordinator. "
            "Edit `docker/env/coordinator.env` / `docker-compose.yml` and restart to change them."
        )
        rows = [
            ("GITHUB_TOKEN", "set" if config.GITHUB_TOKEN else "not set (unauthenticated GitHub API, 60 req/h)"),
            ("REDIS_HOST / REDIS_PORT", f"{config.REDIS_HOST}:{config.REDIS_PORT}"),
            ("GITHUB_CACHE_TTL_SEC", str(config.GITHUB_CACHE_TTL_SEC)),
            ("AVAILABLE_LANGUAGES", ", ".join(config.AVAILABLE_LANGUAGES) or "(auto)"),
            ("AVAILABLE_WORKERS", ", ".join(config.AVAILABLE_WORKERS) or "(auto-discovered)"),
        ]
        st.table({"setting": [k for k, _ in rows], "value": [v for _, v in rows]})
