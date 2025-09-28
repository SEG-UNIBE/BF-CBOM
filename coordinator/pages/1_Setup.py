import datetime as dt
import json

import streamlit as st

from common.config import GITHUB_TOKEN
from common.utils import get_available_workers
from coordinator.logger_config import logger
from coordinator.redis_io import create_benchmark, get_redis
from coordinator.utils import (
    enrich_repos_with_github,
    github_search_multi_language,
    parse_repo_urls,
    set_query_bench_id,
)

# ---------- Streamlit Page ----------

from coordinator.utils import get_favicon_path
from pathlib import Path
ico_path = Path(get_favicon_path())
st.set_page_config(
    page_title="Setup",
    page_icon=str(ico_path),
    layout="wide",
    initial_sidebar_state="expanded",
)


r = get_redis()
st.title("Setup")

# Form area
col1, mid, col2 = st.columns([6, 0.5, 3], vertical_alignment="top")

with col1:
    # Place source selector outside the form so UI updates immediately on change
    source = st.radio(
        "Repository source",
        options=["Search GitHub", "Paste list", "Paste config"],
        horizontal=True,
        key="setup_repo_source",
    )

    CONFIG_SESSION_KEY = "setup_config_payload"
    CONFIG_TEXT_KEY = "setup_config_text"
    if source != "Paste config":
        st.session_state.pop(CONFIG_SESSION_KEY, None)
        st.session_state.pop(CONFIG_TEXT_KEY, None)
        cached_config = None
    else:
        cached_config = st.session_state.get(CONFIG_SESSION_KEY)

    config_data = cached_config

    with st.form("create_bench_form", clear_on_submit=False):
        name = st.text_input("Benchmark name", placeholder="e.g. python-top1k-cdxgen-vs-syft")

        available_workers = get_available_workers()
        workers: list[str] = []
        if source != "Paste config":
            workers = st.multiselect("Workers", options=available_workers, default=available_workers)
        else:
            st.caption("")

        if source == "Search GitHub":
            st.caption("Find repositories by filters. These map to GitHub search.")
            from common.config import AVAILABLE_LANGUAGES

            lang_options = AVAILABLE_LANGUAGES or ["python", "java"]
            languages = st.multiselect(
                "Languages", options=lang_options, default=lang_options[:1]
            )  # allow multi like Workers

            stars_min, stars_max = st.slider(
                "Stars range",
                min_value=0,
                max_value=75_000,
                value=(100, 5_000),
                step=10,
            )

            size_min, size_max = st.slider(
                "Repo size (KB)",
                min_value=0,
                max_value=500_000,
                value=(500, 20_000),
                step=100,
            )

            days_since = st.slider(
                "Days since last commit",
                min_value=1,
                max_value=1_095,
                value=365,
                step=1,
            )

            limit = st.slider("Number of repositories", min_value=1, max_value=200, value=50, step=1)

            # Build base query parts (without language)
            base_parts = [
                f"stars:{stars_min}..{stars_max}",
                f"size:{size_min}..{size_max}",
            ]
            pushed_date = (dt.datetime.now(dt.UTC) - dt.timedelta(days=int(days_since))).date().isoformat()
            base_parts.append(f"pushed:>={pushed_date}")

            params = {
                "source": "github_search",
                "limit": limit,
                "languages": languages,
                "stars_min": stars_min,
                "stars_max": stars_max,
                "size_kb_min": size_min,
                "size_kb_max": size_max,
                "days_since": int(days_since),
                "pushed_date": pushed_date,
            }
        elif source == "Paste list":
            pasted = st.text_area(
                "Paste GitHub repos (one per line)",
                placeholder="org1/repo1\nhttps://github.com/org2/repo2",
                height=160,
            )
            params = {"source": "manual_list", "count": None}
        else:
            uploaded = st.file_uploader(
                "Upload benchmark config (JSON)",
                type=["json"],
                key="setup_config_uploader",
            )
            params = {"source": "config"}
            if uploaded is not None:
                try:
                    config_text = uploaded.getvalue().decode("utf-8")
                    config_data = json.loads(config_text)
                    st.session_state[CONFIG_SESSION_KEY] = config_data
                    st.session_state[CONFIG_TEXT_KEY] = config_text
                    st.caption(
                        f"Config contains {len(config_data.get('repos') or [])} "
                        f"repos and {{len(config_data.get('workers') or [])}} workers."
                    )
                except Exception as exc:
                    st.error(f"Invalid config JSON: {exc}")
                    st.session_state.pop(CONFIG_SESSION_KEY, None)
                    st.session_state.pop(CONFIG_TEXT_KEY, None)
            elif cached_config:
                st.caption(
                    f"Loaded config with {len(cached_config.get('repos') or [])} "
                    f"repos and {len(cached_config.get('workers') or [])} workers."
                )
            else:
                st.caption("Uploaded a config.")

        submitted = st.form_submit_button("Create benchmark")

    if submitted:
        bench_name = name.strip()
        try:
            if source == "Search GitHub":
                if not bench_name:
                    st.error("Please provide a benchmark name.")
                    st.stop()
                if not workers:
                    st.error("Select at least one worker.")
                    st.stop()
                if not params.get("languages"):
                    st.error("Select at least one language.")
                    st.stop()
                with st.spinner("Querying GitHub…"):
                    repos, pushed_date = github_search_multi_language(
                        languages=params["languages"],
                        stars_min=stars_min,
                        stars_max=stars_max,
                        size_kb_min=size_min,
                        size_kb_max=size_max,
                        days_since=int(days_since),
                        limit=int(params["limit"]),
                        token=GITHUB_TOKEN,
                    )
                    params["pushed_date"] = pushed_date
            elif source == "Paste list":
                if not bench_name:
                    st.error("Please provide a benchmark name.")
                    st.stop()
                if not workers:
                    st.error("Select at least one worker.")
                    st.stop()
                repos = parse_repo_urls(pasted)
                if not repos:
                    st.error("No valid repository entries found.")
                    st.stop()
                with st.spinner("Fetching repository metadata…"):
                    repos = enrich_repos_with_github(repos, token=GITHUB_TOKEN)
                params["count"] = len(repos)
            else:
                config_data = st.session_state.get(CONFIG_SESSION_KEY)
                if not config_data:
                    st.error("Upload a benchmark config before creating the benchmark.")
                    st.stop()
                if not bench_name:
                    bench_name = (config_data.get("name") or "").strip()
                if not bench_name:
                    st.error("The config is missing a name; please provide one above.")
                    st.stop()
                cfg_workers = config_data.get("workers") or []
                if not cfg_workers:
                    st.error("Config must include a non-empty list of workers.")
                    st.stop()
                workers = cfg_workers
                raw_repos = config_data.get("repos") or []
                if not raw_repos:
                    st.error("Config must include at least one repository.")
                    st.stop()
                repos = []
                for item in raw_repos:
                    if not isinstance(item, dict):
                        st.error("Unexpected repo entry in config; expected JSON objects.")
                        st.stop()
                    data = dict(item)
                    full = (data.get("full_name") or data.get("repo") or "").strip()
                    if not full:
                        st.error("Each repo in the config must include 'full_name'.")
                        st.stop()
                    git_url = data.get("git_url") or data.get("clone_url")
                    if not git_url:
                        git_url = f"https://github.com/{full}.git"
                    data["full_name"] = full
                    data["git_url"] = git_url
                    if not data.get("branch") and data.get("default_branch"):
                        data["branch"] = data.get("default_branch")
                    repos.append(data)
                params = {
                    "source": "config",
                    "schema_version": config_data.get("schema_version"),
                    "count": len(repos),
                }
        except Exception as e:
            st.error(f"Failed to resolve repositories: {e}")
            st.stop()

        if not repos:
            st.warning("No repositories found for the given input.")
            st.stop()

        logger.info(
            "Creating benchmark '%s' with %d repos and %d workers (source=%s)",
            bench_name,
            len(repos),
            len(workers),
            source,
        )
        bench_id = create_benchmark(r, name=bench_name, params=params, repos=repos, workers=workers)

        # Persist deep-link and offer navigation
        set_query_bench_id(bench_id)

        st.success(f"Benchmark created: {bench_name} · ID {bench_id}")
        # Persist for next render so the Next button remains clickable after rerun
        st.session_state["created_bench_id"] = bench_id

    # Show Next button if a bench was just created (or is present in session)
    created_bench_id = st.session_state.get("created_bench_id")
    if created_bench_id:
        if st.button("Next", type="primary"):
            # Ensure the newly created bench is pre-selected on the next page
            set_query_bench_id(created_bench_id)
            st.switch_page("pages/2_Execution.py")
with mid:
    st.write("")
with col2:
    st.subheader("Notes")
    st.markdown(
        "- Benchmarks are persisted in Redis.\n"
        "- The repo snapshot is stored and will not be re-queried.\n"
    )
