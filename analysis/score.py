"""Scoring without ground truth (RQ4, CLAUDE.md Phase 3 `score.py`), on the exact matcher's groups.

Voters are voter groups (`common/tools.yaml`; tools sharing an engine count once, `--merge-llm` merges the LLM
workers). Per tool, dimension and ladder level:
- **confirmation rate:** the share of the tool's items that at least one other voter group reports too. The point
  estimate is the **mean of the per-repository rates** (the repository is the unit of analysis, refinement 1; a
  repository with 800 items weighs as much as one with 8), the pooled rate is reported next to it;
- **exclusivity:** the share no other voter group reports at all (pooled);
- **weighted support:** a Dawid-Skene-style EM without labels over the repository-level items (algorithm
  dimension): every voter group is an annotator with a sensitivity and a specificity, every item has a posterior
  probability of being real; a tool's support is the mean posterior of its items. Seeded from the majority vote;
- **leave-one-voter-out:** the ranking by the per-repository rate recomputed without each voter group, Kendall's
  tau against the full ranking;
- **bootstrap CIs** of the per-repository mean over repositories (snapshots resampled with replacement).

A tool is **ranked** (and gets a CI) only with items in at least MIN_REPOS repositories and MIN_ITEMS items in total;
below that its rate is shown but no rank is claimed (a 100 % from one item in one repository is not a rank).

Items per dimension: `algorithm` = one canonical algorithm (its L4 identity, the ladder's unit) a tool reports in
a repository; a parameter (`key_size`, `mode`, `padding`, `curve`, `digest`) = (algorithm, value) as the tool
reports it, confirmed when another voter group reports the same value inside a shared cluster; `location` =
(algorithm, file:line) for the tool's located records, confirmed when another voter group's located record sits in
the same call-site group (same file, lines within the matcher's window). Confirmation is read off the matcher's
groups (`analysis/levels.py` clusters, one per key at the level): a tool's item is confirmed when a group holding one
of its records for that item also holds a record of another voter group, so attached generic reports (SHA-2 next to
SHA-256) confirm and are confirmed, and a group is never counted twice however many units it spans. (tool,
repository) pairs the tool was tuned on (`tailored_on`, refinement 13) are left out: they neither count for the tool
nor confirm the others.
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .levels import UNIT_LEVEL, identity, parameter_at
from .matchers import PARAMETER_DIMS, MatchGroup
from .normalize.vocab import Vocabulary, load_vocabulary

DIMS = ("algorithm", *PARAMETER_DIMS, "location")
MIN_REPOS = 3  # a tool is ranked only with items in at least this many repositories ...
MIN_ITEMS = 10  # ... and at least this many items in total
SCORE_COLUMNS = [
    "dimension", "level", "tool", "voter_group", "repos", "items", "confirmed", "rate", "rate_pooled", "ci_low",
    "ci_high", "exclusive", "exclusivity", "rank", "ds_support", "ds_sensitivity", "ds_specificity",
]  # fmt: skip
LOO_COLUMNS = ["dimension", "level", "left_out", "kendall_tau", "ranking"]

AgreeSet = dict[str, set]  # tool -> the items it reports inside one group (a cluster, or a call-site group)


@dataclass
class Tally:
    items: set = field(default_factory=set)
    confirmed: set = field(default_factory=set)
    exclusive: set = field(default_factory=set)


@dataclass
class Observations:
    """What every tool reports per snapshot, as agreement sets built from the matcher's groups (`observe`): per
    (snapshot, dimension) one set per group, {tool: items}. Items: algorithm -> the L4 identity; parameter ->
    (identity, value); location -> (identity, file:line of the record)."""

    level: str
    voter_of: dict[str, str]
    sets: dict[tuple[str, str], list[AgreeSet]] = field(default_factory=lambda: defaultdict(list))
    snapshots: set[str] = field(default_factory=set)
    tools: set[str] = field(default_factory=set)
    # snapshot -> tools that ran there (a voter absent from a snapshot never confirms or contradicts)
    ran: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _tallies: dict[tuple[str, str], dict[str, Tally]] = field(default_factory=dict, repr=False)

    def voter(self, tool: str) -> str:
        return self.voter_of.get(tool, tool)

    def tallies(self, snapshot: str, dim: str) -> dict[str, Tally]:
        """Per tool in `snapshot`: its items of `dim`, those confirmed (another voter group inside a shared group
        reports the item: any item for algorithm and location, the same value for a parameter) and those exclusive
        (no other voter group reports anything inside any group holding the item)."""
        key = (snapshot, dim)
        if key in self._tallies:
            return self._tallies[key]
        out: dict[str, Tally] = defaultdict(Tally)
        contested: dict[str, set] = defaultdict(set)  # another voter group reports something inside a shared group
        unit_voters: dict[str, set[str]] = defaultdict(set)  # algorithm -> voter groups reporting it (any group)
        for aset in self.sets.get(key, []):
            voters = {self.voter(t) for t, its in aset.items() if its}
            for tool, its in aset.items():
                mine = self.voter(tool)
                others = voters - {mine}
                for item in its:
                    out[tool].items.add(item)
                    unit_voters[item if dim == "algorithm" else item[0]].add(mine)
                    if not others:
                        continue
                    contested[tool].add(item)
                    if dim in ("algorithm", "location") or any(
                        self.voter(t2) != mine and any(v2 == item[1] for _, v2 in its2) for t2, its2 in aset.items()
                    ):
                        out[tool].confirmed.add(item)
        for tool, t in out.items():
            mine = self.voter(tool)
            # exclusive: nobody else inside a shared group, and no other voter group reports the algorithm at all
            # (for a parameter or location: reports any value or any location for it)
            t.exclusive = {
                it
                for it in t.items - contested[tool]
                if not (unit_voters[it if dim == "algorithm" else it[0]] - {mine})
            }
        self._tallies[key] = dict(out)
        return self._tallies[key]

    def restrict(self, snapshot: str, tool: str, units: set[str]) -> None:
        """Keep only the tool's items in `snapshot` whose algorithm is in `units` (its consensus set over repeated
        runs, analysis/stability.py)."""
        for (snap, dim), sets in self.sets.items():
            if snap != snapshot:
                continue
            for aset in sets:
                if tool in aset:
                    aset[tool] = {it for it in aset[tool] if (it if dim == "algorithm" else it[0]) in units}
        self._tallies.clear()

    def without(self, voter: str) -> Observations:
        """The observations without one voter group's tools (leave-one-voter-out)."""
        out = Observations(level=self.level, voter_of=self.voter_of)
        out.snapshots = set(self.snapshots)
        out.tools = {t for t in self.tools if self.voter(t) != voter}
        out.ran = {s: {t for t in ts if self.voter(t) != voter} for s, ts in self.ran.items()}
        for key, sets in self.sets.items():
            kept = [{t: its for t, its in aset.items() if self.voter(t) != voter} for aset in sets]
            out.sets[key] = [aset for aset in kept if aset]
        return out


def observe(
    groups_by_snapshot: dict[str, list[MatchGroup]],
    *,
    level: str,
    voter_of: dict[str, str],
    tools_ran: dict[str, set[str]],
    tailored: dict[str, frozenset[str]] | None = None,
    repo_of: dict[str, str] | None = None,
    vocab: Vocabulary | None = None,
) -> Observations:
    """`groups_by_snapshot`: {snapshot key: groups of the exact matcher}; `tools_ran`: {snapshot key: tools with an
    ok run}; `tailored`: {tool: repos}; `repo_of`: {snapshot key: repo full name} for the tailored check."""
    vocab = vocab or load_vocabulary()
    obs = Observations(level=level, voter_of=voter_of)
    tailored, repo_of = tailored or {}, repo_of or {}
    for snapshot, groups in groups_by_snapshot.items():
        obs.snapshots.add(snapshot)
        skip = {t for t, repos in tailored.items() if repo_of.get(snapshot, snapshot) in repos}
        obs.ran[snapshot] = set(tools_ran.get(snapshot, set())) - skip
        sites: dict[str, list[MatchGroup]] = defaultdict(list)
        for g in groups:
            if g.scope == "site":
                sites[str(g.extra.get("parent"))].append(g)
        for g in groups:
            if g.scope != "repo":
                continue
            # an implying record (closure) stands for the group's algorithm, every other record for its own identity
            unit_of = {
                r.record_id: (g.key if r.record_id in g.implied else identity(r, UNIT_LEVEL, vocab)) for r in g.members
            }
            algo: AgreeSet = defaultdict(set)
            params: dict[str, AgreeSet] = {dim: defaultdict(set) for dim in PARAMETER_DIMS}
            for r in g.members:
                unit = unit_of[r.record_id]
                if r.tool in skip or unit is None:
                    continue
                obs.tools.add(r.tool)
                algo[r.tool].add(unit)
                for dim in PARAMETER_DIMS:
                    v = parameter_at(r, level, dim)
                    if v is not None:
                        params[dim][r.tool].add((unit, str(v)))
            if algo:
                obs.sets[(snapshot, "algorithm")].append(dict(algo))
            for dim in PARAMETER_DIMS:
                if params[dim]:
                    obs.sets[(snapshot, dim)].append(dict(params[dim]))
            for s in sites.get(g.group_id, []):
                # the item is the record's own place (a record in several clusters is one located item), the group
                # is the call-site group: confirmed when another voter group's record sits in the same one
                loc: AgreeSet = defaultdict(set)
                for r in s.members:
                    unit = unit_of.get(r.record_id)
                    if r.tool not in skip and unit is not None:
                        loc[r.tool].add((unit, f"{r.file}:{r.line}"))
                if loc:
                    obs.sets[(snapshot, "location")].append(dict(loc))
    return obs


def confirmation(obs: Observations, dim: str, snapshots: list[str] | None = None) -> dict[str, tuple[int, int, int]]:
    """{tool: (items, confirmed, exclusive)} pooled over `snapshots` (default all; a list with repeats for
    bootstraps)."""
    snapshots = sorted(obs.snapshots) if snapshots is None else snapshots
    out: dict[str, list[int]] = {t: [0, 0, 0] for t in obs.tools}
    for snapshot in snapshots:
        for tool, t in obs.tallies(snapshot, dim).items():
            if tool in out and t.items:
                out[tool][0] += len(t.items)
                out[tool][1] += len(t.confirmed)
                out[tool][2] += len(t.exclusive)
    return {t: (n, c, e) for t, (n, c, e) in out.items()}


def repo_rates(obs: Observations, dim: str, snapshots: list[str] | None = None) -> dict[str, list[float]]:
    """{tool: confirmation rate per snapshot with at least one item}, in snapshot order (repeats for bootstraps)."""
    snapshots = sorted(obs.snapshots) if snapshots is None else snapshots
    out: dict[str, list[float]] = {t: [] for t in obs.tools}
    for snapshot in snapshots:
        for tool, t in obs.tallies(snapshot, dim).items():
            if tool in out and t.items:
                out[tool].append(len(t.confirmed) / len(t.items))
    return out


def macro(rates: list[float]) -> float | None:
    return statistics.mean(rates) if rates else None


def supported(repos: int, items: int, *, min_repos: int = MIN_REPOS, min_items: int = MIN_ITEMS) -> bool:
    return repos >= min_repos and items >= min_items


def ranking(
    rates: dict[str, list[float]],
    pooled: dict[str, tuple[int, int, int]],
    *,
    min_repos: int = MIN_REPOS,
    min_items: int = MIN_ITEMS,
) -> list[str]:
    """Tools by their mean per-repository confirmation rate, best first; only tools with enough support take part."""
    ranked = [
        t
        for t in rates
        if supported(len(rates[t]), pooled.get(t, (0, 0, 0))[0], min_repos=min_repos, min_items=min_items)
    ]
    return sorted(ranked, key=lambda t: (-(macro(rates[t]) or 0.0), t))


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


def leave_one_out(obs: Observations, dim: str, *, min_repos: int = MIN_REPOS, min_items: int = MIN_ITEMS) -> list[dict]:
    """Rankings without each voter group and their Kendall tau against the full ranking."""
    full = ranking(repo_rates(obs, dim), confirmation(obs, dim), min_repos=min_repos, min_items=min_items)
    rows = []
    for voter in sorted({obs.voter(t) for t in obs.tools}):
        reduced = obs.without(voter)
        order = ranking(repo_rates(reduced, dim), confirmation(reduced, dim), min_repos=min_repos, min_items=min_items)
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
    """{tool: (low, high)} 2.5 / 97.5 percentiles of the mean per-repository rate over resampled snapshots."""
    rng = random.Random(seed)
    snapshots = sorted(obs.snapshots)
    means: dict[str, list[float]] = defaultdict(list)
    for _ in range(samples):
        drawn = [rng.choice(snapshots) for _ in snapshots]
        for tool, rates in repo_rates(obs, dim, drawn).items():
            if rates:
                means[tool].append(statistics.mean(rates))
    out = {}
    for tool, values in means.items():
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
        tallies = obs.tallies(snapshot, "algorithm")
        for tool in sorted(obs.tools):
            for key in sorted(tallies.get(tool, Tally()).items):
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


def score_rows(
    obs: Observations,
    *,
    samples: int = 200,
    seed: int = 20261018,
    min_repos: int = MIN_REPOS,
    min_items: int = MIN_ITEMS,
) -> tuple[list[dict], list[dict]]:
    """(score rows, leave-one-out rows) for every dimension; Dawid-Skene columns on the algorithm dimension. `rate`
    is the mean per-repository confirmation rate (the point estimate, with its bootstrap CI), `rate_pooled` the
    pooled share; `rank` and the CI are given only to tools with enough support (`supported`)."""
    ds = dawid_skene(obs)
    rows, loo = [], []
    for dim in DIMS:
        pooled = confirmation(obs, dim)
        if not any(n for n, _, _ in pooled.values()):
            continue
        rates = repo_rates(obs, dim)
        order = ranking(rates, pooled, min_repos=min_repos, min_items=min_items)
        cis = bootstrap_ci(obs, dim, samples=samples, seed=seed) if samples else {}
        for tool in sorted(pooled):
            n, c, e = pooled[tool]
            if not n:
                continue
            voter = obs.voter(tool)
            ok = supported(len(rates[tool]), n, min_repos=min_repos, min_items=min_items)
            ci = cis.get(tool, (None, None)) if ok else (None, None)
            row = {
                "dimension": dim,
                "level": obs.level,
                "tool": tool,
                "voter_group": voter,
                "repos": len(rates[tool]),
                "items": n,
                "confirmed": c,
                "rate": round(macro(rates[tool]) or 0.0, 3),
                "rate_pooled": round(c / n, 3),
                "ci_low": ci[0],
                "ci_high": ci[1],
                "exclusive": e,
                "exclusivity": round(e / n, 3),
                "rank": order.index(tool) + 1 if tool in order else None,
                "ds_support": None,
                "ds_sensitivity": None,
                "ds_specificity": None,
            }
            if dim == "algorithm" and ds["posterior"]:
                mine = [
                    ds["posterior"][(s, k)]
                    for s in sorted(obs.snapshots)
                    for k in sorted(obs.tallies(s, "algorithm").get(tool, Tally()).items)
                    if (s, k) in ds["posterior"]
                ]
                row["ds_support"] = round(sum(mine) / len(mine), 3) if mine else None
                row["ds_sensitivity"] = round(ds["sensitivity"].get(voter, 0.0), 3)
                row["ds_specificity"] = round(ds["specificity"].get(voter, 0.0), 3)
            rows.append(row)
        loo += leave_one_out(obs, dim, min_repos=min_repos, min_items=min_items)
    return rows, loo
