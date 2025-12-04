import datetime as dt
import json
import re

import streamlit as st

from common.config import GITHUB_TOKEN
from common.utils import get_available_workers
from coordinator.logger_config import logger
from coordinator.redis_io import create_inspection, get_redis
from coordinator.utils import (
    enrich_repos_with_github,
    github_search_multi_language,
    parse_repo_urls,
    set_query_insp_id,
    get_favicon_path,
)

# ---------- Streamlit Page ----------

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

available_workers = get_available_workers()

# ============================================================
# Shared: Inspection Name & Workers (outside tabs)
# ============================================================
shared_col1, shared_col2 = st.columns([6, 4])

with shared_col1:
    insp_name_input = st.text_input(
        "Inspection Name",
        placeholder="e.g. my-inspection-001",
        key="shared_insp_name",
    )

    shared_workers = st.multiselect(
        "Workers",
        options=available_workers,
        default=available_workers,
        key="shared_workers",
    )

# ============================================================
# Tabs: Individual vs Batch Inspection
# ============================================================
tab_individual, tab_batch = st.tabs(["🔍 Individual Inspection", "📦 Batch Inspection"])

# ============================================================
# TAB 1: Individual Inspection
# ============================================================
with tab_individual:
    st.caption("Inspect a single repository using multiple CBOM generators.")
    
    ind_col1, ind_mid, ind_col2 = st.columns([6, 0.5, 3], vertical_alignment="top")

    # Render Notes column FIRST (before any st.stop() calls)
    with ind_col2:
        st.subheader("Notes")
        st.markdown(
            "- Inspection is persisted in Redis.\n"
            "- Repository metadata is fetched automatically.\n"
            "- Workers selected above will be used.\n"
        )

    with ind_mid:
        st.write("")
    
    with ind_col1:
        repo_url = st.text_input(
            "GitHub Repository URL",
            placeholder="https://github.com/owner/repo",
            key="individual_repo_url",
        )

        # Parse and fetch metadata if URL is provided
        repo_meta = None
        if repo_url.strip():
            # Extract owner/repo from URL
            match = re.match(r"(?:https?://)?(?:www\.)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url.strip())
            if match:
                full_name = match.group(1)
                with st.spinner("Fetching repository info..."):
                    try:
                        enriched = enrich_repos_with_github([{"full_name": full_name}], token=GITHUB_TOKEN)
                        if enriched:
                            repo_meta = enriched[0]
                    except Exception as e:
                        st.error(f"Failed to fetch repository info: {e}")
            else:
                st.warning("Please enter a valid GitHub URL (e.g., https://github.com/owner/repo)")

        # Display repo metadata inline (less prominent)
        if repo_meta:
            name = repo_meta.get("full_name", "Unknown")
            stars = repo_meta.get("stargazers_count", 0)
            language = repo_meta.get("language", "Unknown")
            size_kb = repo_meta.get("size", 0)
            size_display = f"{size_kb / 1024:.1f} MB" if size_kb >= 1024 else f"{size_kb} KB"
            description = repo_meta.get("description", "")
            default_branch = repo_meta.get("default_branch", "main")
            pushed_at = repo_meta.get("pushed_at", "")
            
            # Compact info line
            info_parts = [
                f"💬 {language or 'N/A'}",
                f"⭐ {stars:,}",
                f"💾 {size_display}",
            ]
            if pushed_at:
                try:
                    pushed_date = dt.datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                    days_ago = (dt.datetime.now(dt.UTC) - pushed_date).days
                    info_parts.append(f"📅 {days_ago}d ago")
                except Exception:
                    pass
            
            st.caption(f"**{name}** · " + " · ".join(info_parts))
            if description:
                st.caption(f"_{description[:120]}{'...' if len(description) > 120 else ''}_")

        if st.button("Create Individual Inspection", disabled=not repo_meta):
            if not shared_workers:
                st.error("Select at least one worker.")
            elif repo_meta:
                # Use shared name or generate from repo
                insp_name = insp_name_input.strip()
                if not insp_name:
                    insp_name = f"{repo_meta.get('full_name', 'repo').replace('/', '-')}"
                
                # Ensure repo has required fields
                repo_data = dict(repo_meta)
                if not repo_data.get("git_url"):
                    repo_data["git_url"] = f"https://github.com/{repo_data['full_name']}.git"
                if not repo_data.get("branch"):
                    repo_data["branch"] = repo_data.get("default_branch", "main")
                
                params = {
                    "source": "individual",
                    "count": 1,
                }
                
                logger.info(
                    "Creating individual inspection '%s' with %d workers",
                    insp_name,
                    len(shared_workers),
                )
                insp_id = create_inspection(r, name=insp_name, params=params, repos=[repo_data], workers=shared_workers)
                
                set_query_insp_id(insp_id)
                st.success(f"Inspection created: {insp_name} · ID {insp_id}")
                logger.info("Inspection created: %s · ID %s", insp_name, insp_id)
                st.session_state["created_insp_id"] = insp_id
                # Note: No st.rerun() here - let the "Next" button appear at the bottom

# ============================================================
# TAB 2: Batch Inspection
# ============================================================
with tab_batch:
    st.caption("Batch-inspect a multiple repositories using multiple CBOM generators (benchmark).")

    # Form area
    col1, mid, col2 = st.columns([6, 0.5, 3], vertical_alignment="top")

    # Render Notes column FIRST (before any st.stop() calls)
    with col2:
        st.subheader("Notes")
        st.markdown(
            "- Inspections are persisted in Redis.\n"
            "- The repo snapshot is stored and will not be re-queried.\n"
            "- Workers selected above will be used.\n"
        )

    with mid:
        st.write("")

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

        with st.form("create_insp_form", clear_on_submit=False):
            if source == "Search GitHub":
                st.caption("Find repositories by filters. These map to GitHub search.")
                from common.config import AVAILABLE_LANGUAGES

                lang_options = AVAILABLE_LANGUAGES or ["python", "java"]
                languages = st.multiselect(
                    "Languages", options=lang_options, default=lang_options[:1]
                )

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
                    "Upload inspection config (JSON)",
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
                            f"repos and {len(config_data.get('workers') or [])} workers."
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
                    st.caption("Upload a config.")

            submitted = st.form_submit_button("Create Batch Inspection")

        if submitted:
            insp_name = insp_name_input.strip()
            workers = shared_workers
            try:
                if source == "Search GitHub":
                    if not insp_name:
                        st.error("Please provide an inspection name.")
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
                    if not insp_name:
                        st.error("Please provide an inspection name.")
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
                        st.error("Upload an inspection config before creating the inspection.")
                        st.stop()
                    if not insp_name:
                        insp_name = (config_data.get("name") or "").strip()
                    if not insp_name:
                        st.error("The config is missing a name; please provide one above.")
                        st.stop()
                    cfg_workers = config_data.get("workers") or []
                    if cfg_workers:
                        workers = cfg_workers  # Override with config workers if present
                    if not workers:
                        st.error("Select at least one worker or include workers in config.")
                        st.stop()
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
                "Creating batch inspection '%s' with %d repos and %d workers (source=%s)",
                insp_name,
                len(repos),
                len(workers),
                source,
            )
            insp_id = create_inspection(r, name=insp_name, params=params, repos=repos, workers=workers)

            # Persist deep-link and offer navigation
            set_query_insp_id(insp_id)

            st.success(f"Batch Inspection created: {insp_name} · ID {insp_id}")
            logger.info("Batch Inspection created: %s · ID %s", insp_name, insp_id)
            # Persist for next render so the Next button remains clickable after rerun
            st.session_state["created_insp_id"] = insp_id

# ============================================================
# Navigation: Show Next button if an inspection was created
# ============================================================
created_insp_id = st.session_state.get("created_insp_id")
if created_insp_id:
    st.divider()
    col_btn, _ = st.columns([1, 4])
    with col_btn:
        if st.button("Show Execution", type="primary"):
            set_query_insp_id(created_insp_id)
            st.switch_page("pages/2_Execution.py")