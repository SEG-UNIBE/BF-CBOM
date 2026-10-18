"""Scoring without ground truth (RQ4, CLAUDE.md Phase 3 `score.py`), on the exact matcher's groups.

Voters are voter groups (`common/tools.yaml`; tools sharing an engine count once, `--merge-llm` merges the LLM
workers). Per tool, dimension and ladder level:
- **confirmation rate:** the share of the tool's items that at least one other voter group reports too;
- **exclusivity:** the share no other voter group reports at all;
- **weighted support:** a Dawid-Skene-style EM without labels over the repository-level items (algorithm
  dimension): every voter group is an annotator with a sensitivity and a specificity, every item has a posterior
  probability of being real; a tool's support is the mean posterior of its items. Seeded from the majority vote;
- **leave-one-voter-out:** the ranking by confirmation rate recomputed without each voter group, Kendall's tau
  against the full ranking;
- **bootstrap CIs** over repositories (snapshots resampled with replacement).

Items per dimension: `algorithm` = a repo-scope group (one canonical algorithm per repository); a parameter
(`key_size`, `mode`, `padding`, `curve`, `digest`) = (group, value) as the tool reports it, confirmed when another
voter group reports the same value for the group; `location` = a group the tool locates, confirmed when another
voter group's located record sits in one of the tool's call-site groups. (tool, repository) pairs the tool was tuned
on (`tailored_on`, refinement 13) are left out: they neither count for the tool nor confirm the others.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

from .matchers import PARAMETER_DIMS, MatchGroup

DIMS = ("algorithm", *PARAMETER_DIMS, "location")
SCORE_COLUMNS = [
    "dimension", "level", "tool", "voter_group", "items", "confirmed", "confirmation", "ci_low", "ci_high",
    "exclusive", "exclusivity", "rank", "ds_support", "ds_sensitivity", "ds_specificity",
]  # fmt: skip
LOO_COLUMNS = ["dimension", "level", "left_out", "kendall_tau", "ranking"]


@dataclass
class Observations:
    """What every tool reports per snapshot, as sets, built from the matcher's groups (`observe`)."""

    level: str
    voter_of: dict[str, str]
    # (snapshot, tool) -> {item key}; item key: (group key) | (group key, value) | (group key, site id)
    items: dict[tuple[str, str], dict[str, set]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    snapshots: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    # snapshot -> tools that ran there (a voter absent from a snapshot never confirms or contradicts)
    ran: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def voter(self, tool: str) -> str:
        return self.voter_of.get(tool, tool)


def observe(
    groups_by_snapshot: dict[str, list[MatchGroup]],
    *,
    level: str,
    voter_of: dict[str, str],
    tools_ran: dict[str, set[str]],
    tailored: dict[str, frozenset[str]] | None = None,
    repo_of: dict[str, str] | None = None,
) -> Observations:
    """`groups_by_snapshot`: {snapshot key: groups of the exact matcher}; `tools_ran`: {snapshot key: tools with an
    ok run}; `tailored`: {tool: repos}; `repo_of`: {snapshot key: repo full name} for the tailored check."""
    obs = Observations(level=level, voter_of=voter_of)
    tailored, repo_of = tailored or {}, repo_of or {}
    for snapshot, groups in groups_by_snapshot.items():
        obs.snapshots.add(snapshot)
        skip = {t for t, repos in tailored.items() if repo_of.get(snapshot, snapshot) in repos}
        obs.ran[snapshot] = set(tools_ran.get(snapshot, set())) - skip
        sites = {g.group_id: g for g in groups if g.scope == "site"}
        for g in groups:
            if g.scope != "repo":
                continue
            for r in g.members:
                if r.tool in skip:
                    continue
                obs.tools.add(r.tool)
                bucket = obs.items[(snapshot, r.tool)]
                bucket["algorithm"].add(g.key)
                for dim in PARAMETER_DIMS:
                    v = r.values.get(dim)
                    if v is not None:
                        bucket[dim].add((g.key, str(v)))
            for s in sites.values():
                if s.extra.get("parent") != g.group_id:
                    continue
                for r in s.members:
                    if r.tool not in skip:
                        obs.items[(snapshot, r.tool)]["location"].add((g.key, s.group_id))
    return obs


def _others(obs: Observations, snapshot: str, tool: str, dim: str) -> set:
    """Items other voter groups report in the snapshot."""
    out: set = set()
    mine = obs.voter(tool)
    for t in obs.ran[snapshot]:
        if obs.voter(t) != mine:
            out |= obs.items.get((snapshot, t), {}).get(dim, set())
    return out


def _others_any(obs: Observations, snapshot: str, tool: str, dim: str) -> set:
    """Group keys other voter groups report *something* for, in `dim` (exclusivity ignores the value)."""
    out: set = set()
    mine = obs.voter(tool)
    for t in obs.ran[snapshot]:
        if obs.voter(t) != mine:
            for item in obs.items.get((snapshot, t), {}).get(dim, set()):
                out.add(item if dim == "algorithm" else item[0])
    return out


def confirmation(obs: Observations, dim: str, snapshots: list[str] | None = None) -> dict[str, tuple[int, int, int]]:
    """{tool: (items, confirmed, exclusive)} over `snapshots` (default all; a list with repeats for bootstraps)."""
    snapshots = list(obs.snapshots) if snapshots is None else snapshots
    out: dict[str, list[int]] = {t: [0, 0, 0] for t in obs.tools}
    for snapshot in snapshots:
        for tool in obs.tools:
            mine = obs.items.get((snapshot, tool), {}).get(dim, set())
            if not mine:
                continue
            others = _others(obs, snapshot, tool, dim)
            others_any = _others_any(obs, snapshot, tool, dim)
            out[tool][0] += len(mine)
            out[tool][1] += sum(item in others for item in mine)
            out[tool][2] += sum((item if dim == "algorithm" else item[0]) not in others_any for item in mine)
    return {t: (n, c, e) for t, (n, c, e) in out.items()}


def ranking(scores: dict[str, tuple[int, int, int]]) -> list[str]:
    """Tools by confirmation rate, best first; tools without items last."""
    rate = {t: (c / n if n else -1.0, t) for t, (n, c, _) in scores.items()}
    return sorted(scores, key=lambda t: (-rate[t][0], t))


def kendall_tau(a: list[str], b: list[str]) -> float | None:
    """Kendall's tau-a between two rankings of the same set (None when fewer than two common elements)."""
    common = [x for x in a if x in b]
    if len(common) < 2:
        return None
    pos_a = {x: i for i, x in enumerate(common)}
    pos_b = {x: i for i, x in enumerate([x for x in b if x in pos_a])}
    concordant = discordant = 0
    for i, x in enumerate(common):
        for y in common[i + 1 :]:
            s = (pos_a[x] - pos_a[y]) * (pos_b[x] - pos_b[y])
            concordant += s > 0
            discordant += s < 0
    pairs = len(common) * (len(common) - 1) / 2
    return round((concordant - discordant) / pairs, 3)


def leave_one_out(obs: Observations, dim: str) -> list[dict]:
    """Rankings without each voter group and their Kendall tau against the full ranking."""
    full = ranking(confirmation(obs, dim))
    rows = []
    for voter in sorted({obs.voter(t) for t in obs.tools}):
        reduced = Observations(level=obs.level, voter_of=obs.voter_of)
        reduced.snapshots, reduced.tools = set(obs.snapshots), {t for t in obs.tools if obs.voter(t) != voter}
        reduced.ran = {s: {t for t in ts if obs.voter(t) != voter} for s, ts in obs.ran.items()}
        reduced.items = {k: v for k, v in obs.items.items() if obs.voter(k[1]) != voter}
        order = ranking(confirmation(reduced, dim))
        rows.append(
            {
                "dimension": dim,
                "level": obs.level,
                "left_out": voter,
                "kendall_tau": kendall_tau([t for t in full if obs.voter(t) != voter], order),
                "ranking": order,
            }
        )
    return rows


def bootstrap_ci(obs: Observations, dim: str, *, samples: int = 200, seed: int = 20261018) -> dict[str, tuple]:
    """{tool: (low, high)} 2.5 / 97.5 percentiles of the confirmation rate over resampled snapshots."""
    rng = random.Random(seed)
    snapshots = sorted(obs.snapshots)
    rates: dict[str, list[float]] = defaultdict(list)
    for _ in range(samples):
        drawn = [rng.choice(snapshots) for _ in snapshots]
        for tool, (n, c, _) in confirmation(obs, dim, drawn).items():
            if n:
                rates[tool].append(c / n)
    out = {}
    for tool, values in rates.items():
        values.sort()
        lo, hi = values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]
        out[tool] = (round(lo, 3), round(hi, 3))
    return out


def dawid_skene(obs: Observations, *, iterations: int = 50, tol: float = 1e-6) -> dict:
    """Binary Dawid-Skene EM on the repository-level algorithm items: annotators = voter groups, items = (snapshot,
    algorithm) pairs some voter reports; a voter absent from a snapshot gives no observation there. Returns
    {"posterior": {(snapshot, key): p}, "sensitivity": {voter: p}, "specificity": {voter: p}, "prior": p,
    "iterations": n}. Seeded from the majority vote (share of present voters that report the item)."""
    # every voter group that ran somewhere is an annotator, also one that never reported an algorithm (cdxgen on
    # Java): its silence counts against the items of the snapshots it was present in
    voters = sorted({obs.voter(t) for t in obs.tools} | {obs.voter(t) for ts in obs.ran.values() for t in ts})
    reports: dict[tuple[str, str], set[str]] = {}  # item -> voters reporting it (sorted keys: reproducible sums)
    present: dict[str, list[str]] = {}  # snapshot -> voters present
    for snapshot in sorted(obs.snapshots):
        present[snapshot] = sorted({obs.voter(t) for t in obs.ran[snapshot]})
        for tool in sorted(obs.tools):
            for key in sorted(obs.items.get((snapshot, tool), {}).get("algorithm", set())):
                reports.setdefault((snapshot, key), set()).add(obs.voter(tool))
    reports = dict(sorted(reports.items()))
    if not reports or len(voters) < 2:
        return {"posterior": {}, "sensitivity": {}, "specificity": {}, "prior": None, "iterations": 0}
    posterior = {item: len(vs) / max(1, len(present[item[0]])) for item, vs in reports.items()}
    sens = dict.fromkeys(voters, 0.8)
    spec = dict.fromkeys(voters, 0.8)
    prior = 0.5
    n_iter = 0
    for _ in range(iterations):
        n_iter += 1
        # M step: per voter, over the items of snapshots it was present in
        tp, fn, fp, tn = (dict.fromkeys(voters, 1e-3) for _ in range(4))  # small priors keep rates off 0 and 1
        for item, vs in reports.items():
            p = posterior[item]
            for v in present[item[0]]:
                if v in vs:
                    tp[v] += p
                    fp[v] += 1 - p
                else:
                    fn[v] += p
                    tn[v] += 1 - p
        new_sens = {v: tp[v] / (tp[v] + fn[v]) for v in voters}
        new_spec = {v: tn[v] / (tn[v] + fp[v]) for v in voters}
        new_prior = sum(posterior.values()) / len(posterior)
        # E step
        new_post = {}
        for item, vs in reports.items():
            like_true, like_false = new_prior, 1 - new_prior
            for v in present[item[0]]:
                if v in vs:
                    like_true *= new_sens[v]
                    like_false *= 1 - new_spec[v]
                else:
                    like_true *= 1 - new_sens[v]
                    like_false *= new_spec[v]
            new_post[item] = like_true / (like_true + like_false) if like_true + like_false else 0.5
        delta = max(abs(new_post[i] - posterior[i]) for i in posterior)
        posterior, sens, spec, prior = new_post, new_sens, new_spec, new_prior
        if delta < tol:
            break
    return {"posterior": posterior, "sensitivity": sens, "specificity": spec, "prior": prior, "iterations": n_iter}


def score_rows(obs: Observations, *, samples: int = 200, seed: int = 20261018) -> tuple[list[dict], list[dict]]:
    """(score rows, leave-one-out rows) for every dimension; Dawid-Skene columns on the algorithm dimension."""
    ds = dawid_skene(obs)
    rows, loo = [], []
    for dim in DIMS:
        scores = confirmation(obs, dim)
        if not any(n for n, _, _ in scores.values()):
            continue
        order = ranking(scores)
        cis = bootstrap_ci(obs, dim, samples=samples, seed=seed) if samples else {}
        for tool in sorted(scores):
            n, c, e = scores[tool]
            voter = obs.voter(tool)
            row = {
                "dimension": dim,
                "level": obs.level,
                "tool": tool,
                "voter_group": voter,
                "items": n,
                "confirmed": c,
                "confirmation": round(c / n, 3) if n else None,
                "ci_low": cis.get(tool, (None, None))[0],
                "ci_high": cis.get(tool, (None, None))[1],
                "exclusive": e,
                "exclusivity": round(e / n, 3) if n else None,
                "rank": order.index(tool) + 1 if n else None,
                "ds_support": None,
                "ds_sensitivity": None,
                "ds_specificity": None,
            }
            if dim == "algorithm" and ds["posterior"]:
                mine = [
                    ds["posterior"][(s, k)]
                    for s in obs.snapshots
                    for k in obs.items.get((s, tool), {}).get("algorithm", set())
                    if (s, k) in ds["posterior"]
                ]
                row["ds_support"] = round(sum(mine) / len(mine), 3) if mine else None
                row["ds_sensitivity"] = round(ds["sensitivity"].get(voter, 0.0), 3)
                row["ds_specificity"] = round(ds["specificity"].get(voter, 0.0), 3)
            rows.append(row)
        loo += leave_one_out(obs, dim)
    return rows, loo
