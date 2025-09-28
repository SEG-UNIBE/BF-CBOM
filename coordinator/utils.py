import datetime as dt
import io
import json
import zipfile

import pandas as pd  # local import to avoid hard dependency at module import
import requests
import streamlit as st

from common.utils import format_repo_info, repo_html_url
from coordinator.redis_io import get_bench_repos, get_bench_workers, pair_key
from pathlib import Path

# ----- Query param helpers -----


def hide_streamlit_status() -> None:
    """Hide Streamlit's top-right run status widget to prevent flicker.

    This targets the status widget via its test id. Safe no-op if the DOM changes.
    """
    st.markdown(
        """
        <style>
        /* Hide the top-right status/stop indicator */
        [data-testid="stStatusWidget"] { display: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_favicon_path() -> str:
    """Return absolute path to the SVG favicon bundled with the coordinator image.

    Assumes the repo layout has docs/favicon.svg at the project root and that
    docker/Dockerfile.coordinator copies it to /app/docs/.
    """
    try:
        here = Path(__file__).resolve()
        root = here.parents[1]  # /app
        icon = root / "docs" / "favicon.svg"
        return str(icon)
    except Exception:
        return "favicon.svg"


def get_query_bench_id() -> str | None:
    try:
        return st.query_params.get("bench_id")  # Streamlit >= 1.33
    except Exception:
        q = st.experimental_get_query_params()
        vals = q.get("bench_id", [])
        return vals[0] if vals else None


def set_query_bench_id(bench_id: str) -> None:
    try:
        st.query_params["bench_id"] = bench_id
    except Exception:
        st.experimental_set_query_params(bench_id=bench_id)


# ----- Repo input helpers -----


def parse_repo_urls(text: str) -> list[dict]:
    repos = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Accept forms like "org/repo" or full GitHub URL
        if line.startswith("http"):
            parts = line.rstrip("/").split("/")
            if len(parts) >= 2:
                full_name = "/".join(parts[-2:])
            else:
                continue
        else:
            full_name = line
        repos.append(
            {
                "id": None,
                "full_name": full_name,
                "html_url": f"https://github.com/{full_name}",
                "default_branch": None,
                "stargazers_count": None,
                "language": None,
                "pushed_at": None,
                "clone_url": f"https://github.com/{full_name}.git",
                "ssh_url": f"git@github.com:{full_name}.git",
            }
        )
    return repos


def _sanitize_repo(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "full_name": item.get("full_name"),
        "html_url": item.get("html_url"),
        "default_branch": item.get("default_branch"),
        "stargazers_count": item.get("stargazers_count"),
        "language": item.get("language"),
        "pushed_at": item.get("pushed_at"),
        "clone_url": item.get("clone_url"),
        "ssh_url": item.get("ssh_url"),
        "size": item.get("size"),
    }


def github_search_repos(query: str, limit: int, token: str | None) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = "https://api.github.com/search/repositories"
    per_page = min(100, max(1, limit))
    results: list[dict] = []
    page = 1
    while len(results) < limit:
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": per_page,
            "page": page,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub API error {resp.status_code}: {resp.text}")
        data = resp.json()
        items = data.get("items", [])
        if not items:
            break
        for it in items:
            results.append(_sanitize_repo(it))
            if len(results) >= limit:
                break
        page += 1
    return results[:limit]


def github_search_multi_language(
    languages: list[str],
    stars_min: int,
    stars_max: int,
    size_kb_min: int,
    size_kb_max: int,
    days_since: int,
    limit: int,
    token: str | None,
) -> tuple[list[dict], str]:
    """
    Distribute a GitHub code search across multiple languages and combine results.

    Returns (repos, pushed_date) where pushed_date is the ISO date string used
    for the pushed: filter.
    """
    # De-duplicate languages, preserve order
    langs = [lang for lang in languages or [] if lang]
    seen = set()
    uniq_langs: list[str] = []
    for lang in langs:
        if lang not in seen:
            uniq_langs.append(lang)
            seen.add(lang)

    # Compute pushed date cutoff
    pushed_date = (dt.datetime.now(dt.UTC) - dt.timedelta(days=int(days_since))).date().isoformat()

    base_parts = [
        f"stars:{int(stars_min)}..{int(stars_max)}",
        f"size:{int(size_kb_min)}..{int(size_kb_max)}",
        f"pushed:>={pushed_date}",
    ]

    total_limit = max(0, int(limit))
    if total_limit == 0 or not uniq_langs:
        return [], pushed_date

    # Distribute limit evenly over languages
    n = len(uniq_langs)
    base, rem = divmod(total_limit, n)
    per_limits = [base + (1 if i < rem else 0) for i in range(n)]

    # Collect and de-duplicate repos by full_name
    out: list[dict] = []
    seen_full = set()
    for i, lang in enumerate(uniq_langs):
        need = per_limits[i]
        if need <= 0:
            continue
        q = " ".join([f"language:{lang}"] + base_parts)
        try:
            chunk = github_search_repos(q, need, token) or []
        except Exception:
            chunk = []
        for it in chunk:
            fn = it.get("full_name")
            if fn and fn not in seen_full:
                out.append(it)
                seen_full.add(fn)
            if len(out) >= total_limit:
                break
        if len(out) >= total_limit:
            break

    return out, pushed_date


# ----- Formatting helpers -----


def format_duration_and_size(duration_sec, size_bytes) -> str:
    dur_txt = f"{duration_sec:.1f}s" if isinstance(duration_sec, int | float) else ""
    if isinstance(size_bytes, int) and size_bytes >= 0:
        kb = size_bytes / 1024.0
        size_txt = f"{kb:.1f} KB"
    else:
        size_txt = ""
    return " ".join([t for t in [dur_txt, "·" if dur_txt and size_txt else "", size_txt] if t]).strip()


# ----- Coordinator helpers (moved out of pages) -----


def human_duration(seconds: int | float | None) -> str:
    try:
        total = float(seconds or 0)
    except Exception:
        total = 0.0
    if total <= 0:
        return "0s"
    if total < 1.0:
        # Millisecond precision for sub-second durations
        ms = int(round(total * 1000))
        return f"{ms}ms"

    sec_int = int(total)
    days, rem = divmod(sec_int, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    if days > 0:
        return f"{days}d {hours}h {minutes}m {secs:02d}s"
    if hours > 0:
        return f"{hours}h {minutes}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def derive_status_key_from_payload(payload: dict | None) -> str:
    """Map a worker payload status to our canonical keys.

    Returns one of: 'completed', 'timeout', 'failed', 'cancelled', 'pending'
    """
    if not isinstance(payload, dict):
        return "pending"
    stt = str(payload.get("status") or "").lower()
    if stt == "ok":
        return "completed"
    if stt == "timeout":
        return "timeout"
    if stt == "cancelled":
        return "cancelled"
    if stt:
        return "failed"
    return "pending"


# ----- Table/data helpers for pages -----


def repos_to_table_df(repos: list[dict]):
    """Return a DataFrame with standard repo columns: repo, info, url.

    - repo: "owner/name"
    - info: formatted using language, stars, size
    - url: direct GitHub URL for LinkColumn usage
    """

    rows = []
    for r in repos or []:
        full = (r.get("full_name") or "").strip()
        rows.append(
            {
                "repo": full,
                "info": format_repo_info(r),
                "url": repo_html_url(r),
            }
        )
    return pd.DataFrame(rows)


def build_repo_info_url_map(repos: list[dict]) -> dict[str, dict[str, str]]:
    """Map repo full_name -> {info, url} for quick lookup by name."""

    out: dict[str, dict[str, str]] = {}
    for r in repos or []:
        full = (r.get("full_name") or "").strip()
        if not full:
            continue
        out[full] = {"info": format_repo_info(r), "url": repo_html_url(r)}
    return out


def estimate_similarity_runtime(component_counts: dict[str, int]) -> float:
    """Return a conservative runtime estimate for n-way component matching.

    Model: base + linear * total_components + pair_coef * sum_{i<j} (c_i * c_j)
    - base: fixed overhead (queueing, setup)
    - linear: per-component processing
    - pairwise: cross-comparisons across tools (dominant factor), but with a small coefficient

    This intentionally under-promises compared to the prior exponential model.
    """

    if not component_counts:
        return 0.0

    counts = [max(0, int(v or 0)) for v in component_counts.values()]
    total = sum(counts)
    if total <= 0:
        return 0.0

    # Efficient computation of sum_{i<j} c_i*c_j = ( (sum c)^2 - sum(c^2) ) / 2
    sum_sq = sum(c * c for c in counts)
    pair_sum = max(0.0, (total * total - sum_sq) / 2.0)

    base = 1.5
    linear_coef = 0.01  # seconds per component
    pair_coef = 0.00002  # seconds per cross-tool pair

    estimate = base + linear_coef * total + pair_coef * pair_sum
    return float(max(0.0, estimate))


def safe_int(value, default: int = 0) -> int:
    """Return int(value) if possible, otherwise default."""

    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def format_benchmark_header(name: str, bench_id: str, created: str, expected: str) -> str:
    """Return HTML snippet for consistent benchmark header formatting."""

    created = created or "?"
    expected = expected or "?"
    return (
        f"**{name}**<br/>"
        f"<span style='opacity:0.75; font-size:0.9rem;'>"
        f"<b>ID:</b> {bench_id}<br/>"
        f"<b>Created:</b> {created}  ·  <b>Jobs:</b> {expected}"
        f"</span>"
    )


def summarize_result_cell(stt: str, raw_payload_json: str | None) -> str:
    """Return compact cell text for a job in grids.

    - fired -> pending emoji
    - completed -> ✅ + duration/size
    - cancelled -> 🚫 Cancelled
    - failed/timeout -> appropriate emoji + label
    """
    from common.utils import get_status_emoji, status_text

    stt = (stt or "").lower().strip()
    payload = None
    if raw_payload_json:
        try:
            payload = json.loads(raw_payload_json)
        except Exception:
            payload = None

    if stt == "fired":
        return get_status_emoji("pending") or ""
    if stt == "completed":
        dur = None
        size_b = None
        if isinstance(payload, dict):
            dur = payload.get("duration_sec")
            size_b = payload.get("size_bytes")
            if size_b is None:
                try:
                    size_b = len((payload.get("json") or "").encode("utf-8"))
                except Exception:
                    size_b = None
        meta_txt = format_duration_and_size(dur, size_b)
        return f"{get_status_emoji('completed')} {meta_txt}".strip()
    if stt == "cancelled":
        return f"{get_status_emoji('cancelled')} {status_text('cancelled', 'label')}"

    # Default: determine via payload
    key = derive_status_key_from_payload(payload)
    return f"{get_status_emoji(key)} {status_text(key, 'label')}"


# ----- GitHub enrichment for manual repo lists -----


def _github_headers(token: str | None) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _fetch_repo(full_name: str, token: str | None, timeout: int = 10) -> dict | None:
    try:
        url = f"https://api.github.com/repos/{full_name}"
        resp = requests.get(url, headers=_github_headers(token), timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def enrich_repos_with_github(repos: list[dict], token: str | None = None) -> list[dict]:
    """Return a new list of repos enriched with language, stars, size, default_branch.

    - Non-destructive: original entries are copied and augmented when data is available
    - Public-only: uses unauthenticated calls if token is None (limited rate)
    - Best-effort: failures are ignored; fields remain as provided
    """
    out: list[dict] = []
    for r in repos or []:
        full = (r.get("full_name") or "").strip()
        if not full:
            out.append(r)
            continue
        data = _fetch_repo(full, token)
        if not data:
            out.append(r)
            continue
        # Merge key fields
        merged = dict(r)
        merged["language"] = data.get("language", merged.get("language"))
        merged["stargazers_count"] = data.get("stargazers_count", merged.get("stargazers_count"))
        merged["size"] = data.get("size", merged.get("size"))  # KB
        merged["default_branch"] = data.get("default_branch", merged.get("default_branch"))
        merged["html_url"] = data.get("html_url", merged.get("html_url"))
        merged["clone_url"] = data.get("clone_url", merged.get("clone_url"))
        merged["ssh_url"] = data.get("ssh_url", merged.get("ssh_url"))
        out.append(merged)
    return out


def build_cboms_zip(r, bench_id: str) -> bytes:
    """Collect completed CBOM JSONs for a benchmark and return a ZIP as bytes."""

    repos = get_bench_repos(r, bench_id)
    workers = get_bench_workers(r, bench_id)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Count total ok artifacts for progress
        total = 0
        for repo in repos:
            full = repo.get("full_name")
            for w in workers:
                j_id = r.hget(f"bench:{bench_id}:job_index", pair_key(full, w))
                if not j_id:
                    continue
                raw = r.hget(f"bench:{bench_id}:job:{j_id}", "result_json")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                    if payload.get("status") == "ok":
                        total += 1
                except Exception:
                    continue

        done = 0
        prog = st.progress(0.0, text="Preparing CBOMs…")
        for repo in repos:
            full = repo.get("full_name")
            for w in workers:
                j_id = r.hget(f"bench:{bench_id}:job_index", pair_key(full, w))
                if not j_id:
                    continue
                raw = r.hget(f"bench:{bench_id}:job:{j_id}", "result_json")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if payload.get("status") != "ok":
                    continue
                content = payload.get("json", "{}")
                # Beautify JSON and strip known wrappers (e.g., {"bom": {...}, extra fields})
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and isinstance(parsed.get("bom"), dict):
                        parsed = parsed["bom"]
                    content = json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
                except Exception:
                    pass
                safe_repo = (full or "").replace("/", "_")
                path = f"{bench_id}/{w}/{safe_repo}_{w}.json"
                zf.writestr(path, content)
                done += 1
                if total:
                    prog.progress(done / total, text=f"Prepared {done}/{total} CBOMs")
        prog.empty()
    mem.seek(0)
    return mem.read()


# ----- Config export helpers -----


def _repo_minimal(d: dict) -> dict:
    full = d.get("full_name")
    git_url = d.get("clone_url") or d.get("git_url") or (f"https://github.com/{full}.git" if full else "")
    out_d: dict = {"full_name": full, "git_url": git_url}
    branch = d.get("default_branch") or d.get("branch")
    if branch:
        out_d["branch"] = branch
    return out_d


def build_minimal_config_dict(*, name: str, workers: list[str], repos: list[dict]) -> dict:
    """Return minimal benchmark config dict used by CLI/CI downloads.

    Includes only:
      - schema_version
      - name
      - workers
      - repos[{full_name, git_url, branch?}]
    """
    return {
        "schema_version": "1",
        "name": name,
        "workers": list(workers or []),
        "repos": [_repo_minimal(x) for x in (repos or [])],
    }


def build_minimal_config_json(*, name: str, workers: list[str], repos: list[dict]) -> str:
    return json.dumps(
        build_minimal_config_dict(name=name, workers=workers, repos=repos),
        ensure_ascii=False,
        indent=2,
    )
