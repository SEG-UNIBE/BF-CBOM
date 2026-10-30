"""Q-DARPAN worker: tree-sitter scanner (Java, Go, C/C++) emitting CycloneDX 1.7 (Apache-2.0).

Pilot verdict (2026-10-08): pass on Java, Go and C. Command as the pilot ran it:
`qdarpan scan <repo> --offline --surface source --out <dir>` -> `<dir>/<run>/cbom.json` + `findings.jsonl`.

Adapter: Q-DARPAN writes locations only into custom properties (`qdarpan:location.N` = "<path>:<line>",
absolute paths). This worker copies them into `evidence.occurrences` with repo-relative paths so the
CBOM is comparable with the other tools; the original properties are kept. The CBOM is otherwise
untouched.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from common.models import JobInstruction, Trace
from common.utils import clone_repo
from common.worker import build_handle_instruction, run_worker

NAME = os.getenv("WORKER_NAME") or os.path.basename(os.path.dirname(__file__))
TIMEOUT_SEC = int(os.getenv("WORKER_TIMEOUT_SEC", "900"))
BINARY = os.getenv("QDARPAN_BIN", "qdarpan")
EXTRA_ARGS = os.getenv("QDARPAN_ARGS", "").split()
_LOC_RE = re.compile(r"^(.*?):(\d+)$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(NAME)


def qdarpan_version() -> str | None:
    try:
        out = subprocess.run([BINARY, "--version"], capture_output=True, text=True, timeout=60)
        text = (out.stdout or "") + (out.stderr or "")
        m = re.search(r"\b(\d+\.\d+\.\d+)\b", text)
        return m.group(1) if m else (text.strip().splitlines() or [None])[0]
    except Exception as e:  # pragma: no cover - only when the binary is missing
        logger.warning("could not determine qdarpan version: %s", e)
        return None


def add_occurrences(cbom: dict, repo_path: Path) -> tuple[int, int]:
    """Copy `qdarpan:location.N` properties into evidence.occurrences (repo-relative).

    Returns (components touched, occurrences added)."""
    root = str(repo_path.resolve()).rstrip("/") + "/"
    touched = added = 0
    for comp in cbom.get("components") or []:
        if not isinstance(comp, dict):
            continue
        locs = []
        for p in comp.get("properties") or []:
            if not isinstance(p, dict) or not str(p.get("name", "")).startswith("qdarpan:location."):
                continue
            m = _LOC_RE.match(str(p.get("value", "")))
            if not m:
                continue
            path = m.group(1)
            if path.startswith(root):
                path = path[len(root) :]
            locs.append({"location": path, "line": int(m.group(2))})
        if not locs:
            continue
        evidence = comp.setdefault("evidence", {}) if isinstance(comp.get("evidence"), dict) else {}
        comp["evidence"] = evidence
        existing = evidence.get("occurrences") or []
        seen = {(o.get("location"), o.get("line")) for o in existing if isinstance(o, dict)}
        for o in locs:
            if (o["location"], o["line"]) not in seen:
                existing.append(o)
                seen.add((o["location"], o["line"]))
                added += 1
        evidence["occurrences"] = existing
        touched += 1
    return touched, added


def run_qdarpan(repo_path: Path, out_dir: Path, trace: Trace, *, timeout: float) -> Path:
    cmd = [
        BINARY,
        "scan",
        str(repo_path),
        "--offline",
        "--surface",
        "source",
        "--out",
        str(out_dir),
        "--quiet",
        *EXTRA_ARGS,
    ]
    trace.add(f"qdarpan cmd: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd, cwd=str(out_dir.parent), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("qdarpan_timeout") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("qdarpan_not_found") from exc
    tail = "\n".join(((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-8:])
    runs = sorted(p for p in out_dir.glob("*/cbom.json") if p.is_file())
    if not runs:
        raise RuntimeError(f"qdarpan_no_output rc={proc.returncode}: {tail[:800]}")
    if proc.returncode != 0:
        trace.add(f"qdarpan exit code {proc.returncode} (output written): {tail[:300]}")
    return runs[-1]


def _produce(instr: JobInstruction, trace: Trace) -> tuple[str, float]:
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="qdarpan-") as tmp:
        repo_dir = Path(tmp) / "repo"
        out_dir = Path(tmp) / "runs"
        out_dir.mkdir()
        cloned = clone_repo(
            instr.repo_info.git_url,
            branch=instr.repo_info.branch,
            target_dir=str(repo_dir),
            commit=instr.repo_info.commit,
        )
        if not cloned:
            trace.add("git clone failed")
            raise RuntimeError("git_clone_failed")
        cbom_path = run_qdarpan(Path(cloned), out_dir, trace, timeout=max(10.0, TIMEOUT_SEC - 20.0))
        cbom = json.loads(cbom_path.read_text(encoding="utf-8"))
        touched, added = add_occurrences(cbom, Path(cloned))
        trace.add(f"adapter: evidence.occurrences added to {touched} components ({added} occurrences)")
    logger.info("%s: %d components", instr.repo_info.full_name, len(cbom.get("components") or []))
    return json.dumps(cbom, ensure_ascii=False), time.monotonic() - start


handle_instruction = build_handle_instruction(NAME, _produce)


def main():
    version = qdarpan_version()
    logger.info("qdarpan version: %s", version)
    # the image's TOOL_VERSION names the pinned source commit (a release and later commits share one version string)
    tool_version = os.getenv("TOOL_VERSION") or (f"qdarpan {version}" if version else None)
    run_worker(NAME, handle_instruction, default_timeout=TIMEOUT_SEC, tool_version=tool_version)


if __name__ == "__main__":
    logger.info("starting up...")
    main()
