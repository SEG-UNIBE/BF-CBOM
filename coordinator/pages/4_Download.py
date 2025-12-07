import json
from pathlib import Path

import streamlit as st

from common.utils import get_status_emoji
from coordinator.redis_io import (
    get_insp_meta,
    get_insp_repos,
    get_insp_workers,
    get_redis,
    list_inspections,
    pair_key,
)
from coordinator.utils import (
    build_cboms_zip,
    derive_status_key_from_payload,
    format_inspection_header,
    get_favicon_path,
    get_query_insp_id,
    set_query_insp_id,
)

ico_path = Path(get_favicon_path())
st.set_page_config(
    page_title="Downloads",
    page_icon=str(ico_path),
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(show_spinner=False, ttl=600)
def _payload_matches(raw_text: str, tokens: tuple[str, ...]) -> bool:
    try:
        payload = json.loads(raw_text) if raw_text else {}
    except Exception:
        return False
    meta_parts = []
    for key, value in payload.items():
        if key == "json":
            continue
        try:
            meta_parts.append(f"{key}:{value}")
        except Exception:
            pass
    cbom_txt = payload.get("json") or ""
    hay = (" ".join(meta_parts) + " " + cbom_txt[:100_000]).lower()
    return all(tok in hay for tok in tokens)


def _render_job_results(
    r,
    insp_id: str,
    repos: list[dict],
    workers: list[str],
    job_idx: dict,
) -> None:
    if not repos or not workers:
        st.info("No repos or workers registered for this inspection yet.")
        return

    page_key = f"dl_page_{insp_id}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 0

    def reset_page() -> None:
        st.session_state[page_key] = 0

    colf1, colf2, colf3, colf4 = st.columns([2, 2, 2, 1])
    q = colf1.text_input(
        "Search (repo/worker, space-separated tokens)",
        placeholder="e.g., numpy worker-x timeout",
        key=f"dl_search_{insp_id}",
        on_change=reset_page,
    ).strip()
    sel_repo = colf2.selectbox(
        "Repo filter",
        ["(any)"] + [r.get("full_name", "") for r in repos],
        key=f"dl_sel_repo_{insp_id}",
        on_change=reset_page,
    )
    sel_worker = colf3.selectbox(
        "Worker filter",
        ["(any)"] + workers,
        key=f"dl_sel_worker_{insp_id}",
        on_change=reset_page,
    )
    batch_size = int(
        colf4.number_input(
            "Batch",
            10,
            500,
            50,
            step=10,
            key=f"dl_batch_{insp_id}",
            on_change=reset_page,
        )
    )

    deep = st.toggle(
        "Deep search in JobResult/CBOM (slow)",
        value=False,
        key=f"dl_deep_{insp_id}",
        on_change=reset_page,
    )
    if deep:
        st.caption("Deep search scans JobResult metadata and CBOM JSON bodies for your tokens. Expect slower response.")

    pairs_all: list[tuple[str, str, str]] = []
    for repo in repos:
        full = repo.get("full_name")
        for worker in workers:
            j_id = job_idx.get(pair_key(full, worker))
            if j_id:
                pairs_all.append((full, worker, j_id))

    tokens = [t.lower() for t in q.split()] if q else []

    def match_dropdowns(repo_name: str, worker_name: str) -> bool:
        if sel_repo != "(any)" and repo_name != sel_repo:
            return False
        if sel_worker != "(any)" and worker_name != sel_worker:
            return False
        return True

    pre_dropdown = [
        (repo_name, worker_name, j_id)
        for (repo_name, worker_name, j_id) in pairs_all
        if match_dropdowns(repo_name, worker_name)
    ]

    def quick_token_match(repo_name: str, worker_name: str) -> bool:
        if not tokens:
            return True
        hay = f"{repo_name} {worker_name}".lower()
        return all(tok in hay for tok in tokens)

    if deep and tokens:
        candidates = pre_dropdown
    else:
        candidates = [
            (repo_name, worker_name, j_id)
            for (repo_name, worker_name, j_id) in pre_dropdown
            if quick_token_match(repo_name, worker_name)
        ]

    def needs_deep_filter() -> bool:
        return deep and tokens

    deep_filtered = candidates
    if needs_deep_filter() and candidates:
        pipe = r.pipeline()
        for _, _, j_id in candidates:
            pipe.hget(f"insp:{insp_id}:job:{j_id}", "result_json")
        raw_list = pipe.execute()
        tok_tuple = tuple(tokens)
        deep_filtered = []
        for (repo_name, worker_name, j_id), raw in zip(candidates, raw_list, strict=False):
            if isinstance(raw, bytes | bytearray):
                try:
                    raw = raw.decode("utf-8", "ignore")
                except Exception:
                    raw = None
            if isinstance(raw, str) and _payload_matches(raw, tok_tuple):
                deep_filtered.append((repo_name, worker_name, j_id))

    if deep and not tokens:
        st.caption("Type a search query to use deep search over payloads.")

    total = len(deep_filtered)
    max_page = max(0, (total - 1) // batch_size)
    page = min(st.session_state.get(page_key, 0), max_page)
    st.session_state[page_key] = page

    colp1, colp2, colp_mid, colp3 = st.columns([1, 1, 5, 3])
    if colp1.button("◀ Prev", disabled=page <= 0, key=f"dl_prev_{insp_id}"):
        st.session_state[page_key] = max(0, page - 1)
        st.rerun()
    if colp2.button("Next ▶", disabled=page >= max_page, key=f"dl_next_{insp_id}"):
        st.session_state[page_key] = min(max_page, page + 1)
        st.rerun()
    colp_mid.caption(f"Showing page {page + 1} of {max_page + 1} · {total} matches")

    start = page * batch_size
    window = deep_filtered[start : start + batch_size]

    shown = 0
    if window:
        pipe = r.pipeline()
        for _, _, j_id in window:
            pipe.hgetall(f"insp:{insp_id}:job:{j_id}")
        metas = pipe.execute()

        for (repo_name, worker_name, j_id), meta in zip(window, metas, strict=False):
            raw = meta.get("result_json")
            if raw is None and isinstance(meta, dict) and b"result_json" in meta:
                raw = meta.get(b"result_json")
            if isinstance(raw, bytes | bytearray):
                try:
                    raw = raw.decode("utf-8", "ignore")
                except Exception:
                    raw = None
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
            if not payload:
                continue

            status_key = derive_status_key_from_payload(payload)
            emoji = get_status_emoji(status_key) or ""

            with st.expander(f"{emoji} {repo_name} · {worker_name} · {j_id}"):
                col_instr, col_result = st.columns(2)

                from common.models import JobInstruction, RepoInfo

                try:
                    repo_info_obj = RepoInfo.from_dict(payload.get("repo_info", {}))
                    instr_obj = JobInstruction(
                        job_id=payload.get("job_id", j_id),
                        tool=payload.get("worker", worker_name),
                        repo_info=repo_info_obj,
                    )
                    instr = instr_obj.to_dict()
                except Exception:
                    instr = {
                        "job_id": payload.get("job_id", j_id),
                        "tool": payload.get("worker", worker_name),
                        "repo_info": payload.get("repo_info"),
                    }

                with col_instr:
                    st.caption("JobInstruction")
                    st.json(instr)

                with col_result:
                    jr = {k: v for k, v in payload.items() if k != "json"}
                    st.caption("JobResult")
                    st.json(jr)

                if st.toggle(
                    f"Show CBOM JSON for {repo_name} · {worker_name}",
                    key=f"dl_cbom_{insp_id}_{j_id}",
                    value=False,
                ):
                    cbom_txt = payload.get("json") or "{}"
                    try:
                        obj = json.loads(cbom_txt)
                        pretty = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)
                        st.code(pretty, language="json")
                    except Exception:
                        st.text_area(
                            "CBOM JSON (raw)",
                            cbom_txt[:200_000],
                            height=200,
                            disabled=True,
                        )

            shown += 1

    if shown == 0:
        st.info("No job results to display yet for the current filters.")


r = get_redis()
st.title("Downloads")

insp_id_hint = get_query_insp_id() or st.session_state.get("created_insp_id")
inspections = list_inspections(r)
if not inspections:
    st.info("No inspections found. Please create one first.")
    st.stop()

insp_map = {iid: meta for iid, meta in inspections}
insp_order = [iid for iid, _ in inspections]

initial_id = insp_order[0]
if insp_id_hint and insp_id_hint in insp_map:
    initial_id = insp_id_hint

insp_id = st.selectbox(
    "Select inspection",
    options=insp_order,
    index=insp_order.index(initial_id),
    format_func=lambda iid: f"{insp_map.get(iid, {}).get('name', '(unnamed)')} · "
    f"{iid[:8]} · {insp_map.get(iid, {}).get('status', '?')}",
)
set_query_insp_id(insp_id)

meta = get_insp_meta(r, insp_id) or insp_map.get(insp_id, {})
name = meta.get("name", insp_id)
status = meta.get("status", "?")
created = meta.get("created_at") or meta.get("started_at") or "?"
expected = meta.get("expected_jobs") or "?"
repos = get_insp_repos(r, insp_id)
workers = get_insp_workers(r, insp_id)
job_idx = r.hgetall(f"insp:{insp_id}:job_index") or {}

st.markdown(format_inspection_header(name, insp_id, created, expected), unsafe_allow_html=True)
st.caption(f"Status: {status}")

zip_bytes = build_cboms_zip(r, insp_id)
if not zip_bytes:
    st.info("No completed CBOMs to download yet.")
else:
    st.download_button(
        "Download CBOMs",
        data=zip_bytes,
        file_name=f"cboms_{insp_id[:8]}.zip",
        mime="application/zip",
        key=f"dl_zip_{insp_id}",
    )

st.markdown("### Job Results and CBOMs")
_render_job_results(r, insp_id, repos, workers, job_idx)
