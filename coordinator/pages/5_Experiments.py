"""Stored batch experiments (the `results-data` volume, mounted read-only): the analysis summary, the runs table and
the CSV tables of `make analysis ARGS="report <exp>"`. Read-only: experiments run from `make experiment`."""

import csv
import io
from pathlib import Path

import streamlit as st

from common.experiment import RESULTS_DIR, analysis_dir, is_experiment, load_run_log, spec_path
from coordinator.utils import get_favicon_path

st.set_page_config(
    page_title="Experiments",
    page_icon=str(Path(get_favicon_path())),
    layout="wide",
    initial_sidebar_state="expanded",
)
st.title("Batch experiments")
st.caption(
    f'Stored under `{RESULTS_DIR}` by `make experiment`; the tables come from `make analysis ARGS="report <exp>"` '
    "(also run by `make experiment-export`). This page only reads."
)


def _experiments() -> list[Path]:
    if not RESULTS_DIR.exists():
        return []
    return sorted((p for p in RESULTS_DIR.iterdir() if p.is_dir() and is_experiment(p)), key=lambda p: p.name)


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


experiments = _experiments()
if not experiments:
    st.info("No experiments stored yet. Run one with `make experiment EXP=experiments/<spec>.yaml`.")
    st.stop()

names = [p.name for p in experiments]
chosen = st.sidebar.selectbox("Experiment", names, index=len(names) - 1)
exp_dir = RESULTS_DIR / chosen
out_dir = analysis_dir(exp_dir)

with st.expander("Spec and run log", expanded=False):
    st.code(spec_path(exp_dir).read_text(encoding="utf-8"), language="yaml")
    sessions = load_run_log(exp_dir)
    if sessions:
        st.dataframe(
            [
                {
                    "command": s.get("command"),
                    "status": s.get("status"),
                    "started": s.get("started_at"),
                    "finished": s.get("finished_at"),
                    "results stored": s.get("results_stored"),
                    "budget GiB": s.get("memory_budget_gb"),
                    "waves": len(s.get("waves") or []),
                    "peak RAM MB": s.get("ram_peak_used_mb"),
                    "runner commit": s.get("runner_commit"),
                }
                for s in sessions
            ],
            width="stretch",
        )

summary = out_dir / "_SUMMARY.md"
if not summary.exists():
    st.warning(f'No analysis yet for `{chosen}`: run `make analysis ARGS="report {chosen}"`.')
    st.stop()

tab_summary, tab_runs, tab_files = st.tabs(["Summary", "Runs", "Files"])
with tab_summary:
    st.markdown(summary.read_text(encoding="utf-8"))

with tab_runs:
    runs_csv = out_dir / "runs.csv"
    if runs_csv.exists():
        rows = _read_csv(runs_csv)
        tools = sorted({r["tool"] for r in rows})
        statuses = sorted({r["status"] for r in rows})
        c1, c2, c3 = st.columns([2, 2, 3])
        pick_tools = c1.multiselect("Tools", tools, default=tools)
        pick_status = c2.multiselect("Status", statuses, default=statuses)
        needle = c3.text_input("Repo contains", "")
        shown = [
            r
            for r in rows
            if r["tool"] in pick_tools and r["status"] in pick_status and needle.lower() in r["repo"].lower()
        ]
        columns = [
            "repo", "tool", "run", "status", "duration_sec", "peak_memory_mb", "crypto_assets", "algorithm_assets",
            "tool_version", "bfcbom_commit", "cost_usd", "error",
        ]  # fmt: skip
        st.caption(f"{len(shown)} of {len(rows)} runs")
        st.dataframe([{c: r.get(c) for c in columns} for r in shown], width="stretch", hide_index=True)
    else:
        st.info("runs.csv missing: rerun the report.")

with tab_files:
    files = sorted(p for p in out_dir.iterdir() if p.is_file())
    for path in files:
        c1, c2 = st.columns([5, 1])
        c1.write(f"`{path.name}` ({path.stat().st_size / 1024:.0f} KB)")
        c2.download_button("download", data=path.read_bytes(), file_name=path.name, key=f"dl-{path.name}")
    if any(p.suffix == ".csv" for p in files):
        buf = io.BytesIO()
        import zipfile

        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, arcname=f"{chosen}/_ANALYSIS/{path.name}")
        st.download_button("download all as zip", data=buf.getvalue(), file_name=f"{chosen}-analysis.zip")
