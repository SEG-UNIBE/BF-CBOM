"""E4 (RQ4 validation): did a tool pick up the crypto call a mutant inserted (mutation/, mutants.json)?

Each (mutant, tool) is compared with the same tool on the original snapshot. A mutant record counts only if it is new:
not one of the original's findings moved by the inserted lines (`shifts`, same unit, file and moved line). With
repeated runs a dimension counts as detected when it holds in most of the mutant's runs, so an answer that ignores
the change (an LLM's memorised repository) is not credited.
- `found_repo`: the expected unit (the L4 identity; a protocol for TLS) is among the mutant's units and not among the
  original's (majority over runs). `masked` when the original already has it: the repository level cannot decide,
  the row has no value there. Location-less tools are judged here only.
- `found_file`, `found_span` (on an inserted line, imports included, ±`window`), `found_site` (the line naming the
  algorithm ±`window`), `found_group` (lenient: a record of the expected group at the site, e.g. SHA-2 or EC).
- parameters (`key_size`, `mode`, `padding`, `curve`, `digest`, `iterations`, `protocol_version`): a new record of
  the expected unit on the inserted lines carries the expected value; empty where nothing is expected or no such
  record exists.
- "no other change": `extra_units` (new units besides the expected one, `also` and what they imply), `lost_units`,
  `extra_sites`, `lost_sites` (located findings, the original's moved through `shifts`), majority sets on both
  sides; read next to the tool's run-to-run instability on the original (stability.csv).
Rates (`mutation-rates.csv`): per tool, language, style and dimension the mean of the per-repository detection rates
(refinement 1), the pooled rate, and a bootstrap CI over repositories when the support suffices (score.py's
thresholds). Comparing these with the E3 ranking is Phase 5.
"""

from __future__ import annotations

import random
from collections import defaultdict
from statistics import mean

from common.experiment import ORIGINAL_VARIANT, repo_dir_name
from common.tools_registry import role, voter_groups

from .levels import identity
from .normalize.vocab import Vocabulary, load_vocabulary
from .score import MIN_ITEMS, MIN_REPOS, supported

DETECTIONS = ("found_repo", "found_file", "found_span", "found_site", "found_group")
PARAMETERS = ("key_size", "mode", "padding", "curve", "digest", "iterations", "protocol_version")
DIMENSIONS = DETECTIONS + PARAMETERS
BOOTSTRAP_SEED = 20261020

MUTANTS_COLUMNS = [
    "repo", "snapshot", "commit", "language", "variant", "variant_commit", "snippet", "style", "catalog_version",
    "unit", "family", "expected", "also", "file", "call_line", "related_lines", "spans", "host", "host_line",
    "host_callers", "host_entry", "imports_added", "qualified", "patch",
]  # fmt: skip
MUTATION_COLUMNS = [
    "repo", "snapshot", "language", "variant", "snippet", "style", "tool", "voter_group", "role", "status",
    "runs_mutant", "runs_original", "unit", "masked", *DETECTIONS, *PARAMETERS, "extra_units", "lost_units",
    "extra_sites", "lost_sites", "new_records",
]  # fmt: skip
RATE_COLUMNS = [
    "tool", "voter_group", "role", "language", "style", "dimension", "repos", "mutants", "detected", "rate",
    "pooled", "ci_low", "ci_high", "supported",
]  # fmt: skip


def _key(rec, vocab: Vocabulary) -> str | None:
    """A record's comparison unit: its L4 identity, or `protocol:<name>` for a protocol record without one."""
    unit = identity(rec, "L4", vocab)
    if unit:
        return unit
    protocol = rec.values.get("protocol")
    return f"protocol:{protocol}" if protocol else None


def expected_key(mutant: dict) -> str | None:
    exp = mutant.get("expected") or {}
    if exp.get("unit"):
        return str(exp["unit"])
    return f"protocol:{exp['protocol']}" if exp.get("protocol") else None


def _majority(sets: list[set]) -> set:
    """Elements present in more than half of `sets`."""
    count: dict = defaultdict(int)
    for s in sets:
        for x in s:
            count[x] += 1
    return {x for x, n in count.items() if n * 2 > len(sets)}


def _vote(values: list) -> bool | None:
    """Majority over runs of True/False values (None = not judged in that run); None when no run judged."""
    judged = [v for v in values if v is not None]
    if not judged:
        return None
    return sum(bool(v) for v in judged) * 2 > len(judged)


def _shift(line: int, shifts: list) -> int:
    return line + sum(count for after, count in shifts if line > after)


def _near(rec, lo: int, hi: int) -> bool:
    if rec.line is None:
        return False
    end = rec.line_end if rec.line_end and rec.line_end >= rec.line else rec.line
    return rec.line <= hi and end >= lo


def _allowed_units(mutant: dict, vocab: Vocabulary) -> set[str]:
    """Units a mutant may add without counting as another change: the expected unit, `also`, and what they imply
    (refinement 2: HMAC-SHA256 implies SHA-256, PBKDF2 implies HMAC)."""
    exp = dict(mutant.get("expected") or {})
    out = {k for k in (expected_key(mutant), *(mutant.get("also") or ())) if k}
    for unit in list(out):
        out |= set(vocab.implied({"algorithm": unit, "family": vocab.family_of_algorithm(unit) or unit}))
    out |= set(vocab.implied({k: v for k, v in exp.items() if k in ("family", "digest")}))
    for unit in list(out):
        fam = vocab.family_of_algorithm(unit)
        if fam:
            out.add(fam)
    if exp.get("family"):
        out.add(str(exp["family"]))
    return out


def _sites(records, vocab: Vocabulary, shifts: list | None = None, file: str | None = None) -> set:
    """(unit, file, line) of the located records; with `shifts`, the lines of `file` moved into the mutant."""
    out = set()
    for r in records:
        key = _key(r, vocab)
        if key and r.file and r.line:
            line = _shift(r.line, shifts) if shifts is not None and r.file == file else r.line
            out.add((key, r.file, line))
    return out


def evaluate(
    mutant: dict, mutant_runs: dict[int, list], original_runs: dict[int, list], *, window: int = 2, vocab=None
) -> dict:
    """Detection and side effects of one tool on one mutant: `*_runs` map a run index to that run's records (only
    ok runs). Returns the MUTATION_COLUMNS values from `masked` on."""
    vocab = vocab or load_vocabulary()
    want = expected_key(mutant)
    exp = mutant.get("expected") or {}
    group = vocab.group_of(exp.get("family")) if exp.get("family") else None
    path, call, shifts = mutant["file"], int(mutant["call_line"]), mutant.get("shifts") or []
    spans = [(s["start"] - window, s["end"] + window) for s in mutant.get("spans") or []]
    original_sites = set().union(*(_sites(recs, vocab, shifts, path) for recs in original_runs.values()))
    original_units = _majority([{k for r in recs if (k := _key(r, vocab))} for recs in original_runs.values()])
    per_run = []
    mutant_unit_sets, mutant_site_sets = [], []
    new_total = 0
    for recs in mutant_runs.values():
        new = [
            r for r in recs if not (r.file and r.line and (_key(r, vocab), r.file, r.line) in original_sites)
        ]  # a finding of the original moved by the insertion is not new
        new_total += len(new)
        mine = [r for r in new if _key(r, vocab) == want]
        in_file = [r for r in mine if r.file == path]
        on_span = [r for r in in_file if any(_near(r, lo, hi) for lo, hi in spans)]
        at_site = [r for r in in_file if _near(r, call - window, call + window)]
        grouped = [
            r for r in new if r.file == path and _near(r, call - window, call + window) and group and r.group == group
        ]
        run = {
            "units": {want} & {k for r in recs if (k := _key(r, vocab))},
            "found_file": bool(in_file),
            "found_span": bool(on_span),
            "found_site": bool(at_site),
            "found_group": bool(grouped) or bool(at_site),
        }
        for dim in PARAMETERS:
            if exp.get(dim) is None:
                run[dim] = None
                continue
            values = {r.values.get(dim) for r in on_span if r.values.get(dim) is not None}
            run[dim] = (str(exp[dim]) in {str(v) for v in values}) if values else None
        per_run.append(run)
        mutant_unit_sets.append({k for r in recs if (k := _key(r, vocab))})
        mutant_site_sets.append(_sites(recs, vocab))
    masked = bool(want and want in original_units)
    out = {"unit": want, "masked": masked, "new_records": round(new_total / max(len(per_run), 1), 1)}
    out["found_repo"] = None if masked or not want else _vote([bool(r["units"]) for r in per_run])
    for dim in ("found_file", "found_span", "found_site", "found_group", *PARAMETERS):
        out[dim] = _vote([r[dim] for r in per_run])
    mutant_units = _majority(mutant_unit_sets)
    allowed = _allowed_units(mutant, vocab)
    out["extra_units"] = sorted(mutant_units - original_units - allowed)
    out["lost_units"] = sorted(original_units - mutant_units)
    original_site_major = _majority([_sites(recs, vocab, shifts, path) for recs in original_runs.values()])
    mutant_sites = _majority(mutant_site_sets)
    inserted = [(lo, hi) for lo, hi in spans]
    extra = {
        s
        for s in mutant_sites - original_site_major
        if not (s[1] == path and any(lo <= s[2] <= hi for lo, hi in inserted))
    }
    out["extra_sites"] = len(extra)
    out["lost_sites"] = len(original_site_major - mutant_sites)
    return out


def mutant_rows(manifest: dict, *, languages: dict[str, str] | None = None) -> list[dict]:
    """mutants.csv: one row per mutant of mutants.json."""
    rows = []
    for full_name, entry in sorted((manifest.get("repos") or {}).items()):
        snapshot = repo_dir_name(full_name, entry["commit"])
        for m in entry.get("mutants") or []:
            exp = m.get("expected") or {}
            rows.append(
                {
                    "repo": full_name,
                    "snapshot": snapshot,
                    "commit": entry["commit"],
                    "language": (languages or {}).get(full_name) or entry.get("language"),
                    "variant": m["id"],
                    "variant_commit": m["commit"],
                    "snippet": m["snippet"],
                    "style": m["style"],
                    "catalog_version": m.get("catalog_version"),
                    "unit": expected_key(m),
                    "family": exp.get("family"),
                    "expected": ";".join(f"{k}={v}" for k, v in sorted(exp.items()) if v is not None),
                    "also": m.get("also") or [],
                    "file": m["file"],
                    "call_line": m["call_line"],
                    "related_lines": m.get("related_lines") or [],
                    "spans": [f"{s['kind']}:{s['start']}-{s['end']}" for s in m.get("spans") or []],
                    "host": m["host"]["name"],
                    "host_line": m["host"]["line"],
                    "host_callers": m["host"]["callers"],
                    "host_entry": m["host"]["entry"],
                    "imports_added": m.get("imports_added") or [],
                    "qualified": m.get("qualified") or [],
                    "patch": m.get("patch", "").rstrip("\n"),
                }
            )
    return rows


def detection_rows(
    manifest: dict,
    records: dict[tuple[str, str, str], dict[int, list]],
    statuses: dict[tuple[str, str, str], dict[int, str]],
    tools: list[str],
    *,
    window: int = 2,
    languages: dict[str, str] | None = None,
    voter_of=None,
    vocab=None,
) -> list[dict]:
    """mutation.csv: one row per (mutant, tool). `records`: {(snapshot, variant, tool): {run: records}} of the ok
    runs; `statuses`: the same keys -> {run: status} of every stored run."""
    vocab = vocab or load_vocabulary()
    voter_of = voter_of if voter_of is not None else voter_groups()
    rows = []
    for full_name, entry in sorted((manifest.get("repos") or {}).items()):
        snapshot = repo_dir_name(full_name, entry["commit"])
        for m in entry.get("mutants") or []:
            for tool in tools:
                base = {
                    "repo": full_name,
                    "snapshot": snapshot,
                    "language": (languages or {}).get(full_name) or entry.get("language"),
                    "variant": m["id"],
                    "snippet": m["snippet"],
                    "style": m["style"],
                    "tool": tool,
                    "voter_group": voter_of.get(tool, tool),
                    "role": role(voter_of, tool),
                }
                mutant_runs = records.get((snapshot, m["id"], tool), {})
                original_runs = records.get((snapshot, ORIGINAL_VARIANT, tool), {})
                base["runs_mutant"], base["runs_original"] = len(mutant_runs), len(original_runs)
                if not mutant_runs or not original_runs:
                    mutant_status = _status(statuses.get((snapshot, m["id"], tool)))
                    original_status = _status(statuses.get((snapshot, ORIGINAL_VARIANT, tool)))
                    base["status"] = mutant_status if not mutant_runs else f"original {original_status}"
                    base["unit"] = expected_key(m)
                    rows.append(base)
                    continue
                base["status"] = "ok"
                base.update(evaluate(m, mutant_runs, original_runs, window=window, vocab=vocab))
                rows.append(base)
    return rows


def _status(by_run: dict[int, str] | None) -> str:
    if not by_run:
        return "missing"
    statuses = sorted(set(by_run.values()))
    return statuses[0] if len(statuses) == 1 else "/".join(statuses)


def _bootstrap(per_repo: dict[str, list[bool]], samples: int, seed: int) -> tuple[float, float] | None:
    repos = sorted(per_repo)
    if not repos:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draw = [repos[rng.randrange(len(repos))] for _ in repos]
        means.append(mean(sum(per_repo[r]) / len(per_repo[r]) for r in draw))
    means.sort()
    return means[int(0.025 * (samples - 1))], means[int(0.975 * (samples - 1))]


def rate_rows(rows: list[dict], *, samples: int = 200, seed: int = BOOTSTRAP_SEED) -> list[dict]:
    """mutation-rates.csv: per (tool, language, style or `all`, dimension) the detection rate over the judged mutants:
    mean of per-repository rates, pooled rate, bootstrap CI over repositories when supported (score.py)."""
    cells: dict[tuple, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    meta: dict[str, dict] = {}
    for r in rows:
        if r.get("status") != "ok":
            continue
        meta[r["tool"]] = {"voter_group": r["voter_group"], "role": r["role"]}
        for dim in DIMENSIONS:
            v = r.get(dim)
            if v is None:
                continue
            for style in (r["style"], "all"):
                cells[(r["tool"], r["language"], style, dim)][r["repo"]].append(bool(v))
    out = []
    for (tool, language, style, dim), per_repo in sorted(cells.items(), key=lambda kv: tuple(str(x) for x in kv[0])):
        n = sum(len(v) for v in per_repo.values())
        detected = sum(sum(v) for v in per_repo.values())
        ok = supported(len(per_repo), n, min_repos=MIN_REPOS, min_items=MIN_ITEMS)
        ci = _bootstrap(per_repo, samples, seed) if ok else None
        out.append(
            {
                "tool": tool,
                **meta[tool],
                "language": language,
                "style": style,
                "dimension": dim,
                "repos": len(per_repo),
                "mutants": n,
                "detected": detected,
                "rate": round(mean(sum(v) / len(v) for v in per_repo.values()), 4),
                "pooled": round(detected / n, 4),
                "ci_low": round(ci[0], 4) if ci else None,
                "ci_high": round(ci[1], 4) if ci else None,
                "supported": ok,
            }
        )
    return out
