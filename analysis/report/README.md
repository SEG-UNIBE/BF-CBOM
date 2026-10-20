# Figures from the analysis tables

The analysis writes flat CSV tables into `<exp>/_ANALYSIS/` (`experiments/README.md` lists every file and column).
The figures of the paper are built from them later, by hand, with pandas and matplotlib; nothing here runs in the
pipeline. When drawing them, follow the `dataviz` skill (one palette, light and dark variants, accessible colours).
Recipes, one per planned figure:

```python
import pandas as pd
A = "exports/e1-icpc-corpus/_ANALYSIS"   # or any copied export
```

Observers (`role: observer` in `common/tools.yaml`: qelens, cryptarium, qScan) are in every table; their records never
confirm anyone and they are in no `voters` list, so voter-based figures exclude them already. To leave them out of a
per-tool figure, drop them by role where the table has the column (`levels-`, `algorithms-`, `score-run<k>.csv`) and
by name elsewhere:
```python
OBSERVERS = {"qelens", "cryptarium", "qscan"}   # or: tools.yaml entries with role == "observer"
scores = pd.read_csv(f"{A}/score-run1.csv").query("role == 'voter'")
```

**Tools per algorithm, UpSet plot (RQ2).** Which voter groups report which algorithms per repository.
```python
groups = pd.read_csv(f"{A}/groups-exact-run1.csv").query("scope == 'repo'")
members = pd.read_csv(f"{A}/members-exact-run1.csv").query("implied != True")
# one row per (repo group, voter group); `voters` is a ';'-joined list in groups-exact
sets = groups.assign(voter=groups["voters"].str.split(";")).explode("voter")
memberships = pd.crosstab(sets["group_id"], sets["voter"]).astype(bool)   # the UpSet input (e.g. upsetplot.plot)
```

**Fill-rate heatmap (RQ2).** Share of algorithm assets per tool and dimension with a reported value.
```python
cov = pd.read_csv(f"{A}/coverage.csv")
heat = cov.pivot(index="tool", columns="dimension", values="reported_share")
```

**Meaningful: standard fields vs any source (RQ1).**
```python
m = pd.read_csv(f"{A}/meaningful-run1.csv")
pd.melt(m, id_vars=["tool", "dimension"], value_vars=["standard_share", "any_share"])   # grouped bars per dimension
```

**The normalisation ladder (refinement 14).** Confirmed share per tool from L0 to L4; parameters from L2.
```python
lv = pd.read_csv(f"{A}/levels-run1.csv")
ladder = lv.query("dimension == 'algorithm' and tool != 'all'").pivot(index="tool", columns="level", values="share")
ladder = ladder[["L0", "L1", "L2", "L3", "L4"]]       # one line per tool, levels on the x axis
params = lv.query("dimension != 'algorithm' and tool == 'all'").pivot(index="dimension", columns="level", values="share")
```

**Matchability per level (RQ3).** Items by the number of voter groups in their largest group, and the agreement per
dimension inside the groups, exact matcher vs RaQuN, level by level.
```python
ml = pd.read_csv(f"{A}/matchlevels-run1.csv")
k = ml.set_index(["matcher", "level"])[["k1", "k2", "k3", "k4plus"]].div(ml["items"].values, axis=0)  # stacked bars
agree = ml.set_index(["matcher", "level"]).filter(like="_agree")                                   # heatmap
```

**Ranking with confidence intervals (RQ4).**
```python
sc = pd.read_csv(f"{A}/score-run1.csv").query("dimension == 'algorithm'").sort_values("confirmation")
# dot = confirmation, whiskers = ci_low .. ci_high; a second marker column for ds_support
err = [sc["confirmation"] - sc["ci_low"], sc["ci_high"] - sc["confirmation"]]
```
Stability of the ranking: `loo-run1.csv` (`kendall_tau` per left-out voter group); stability across levels: rerun
`analysis report --level L2` and compare `rank`.

**Cost per found and per confirmed asset (LLM workers).**
```python
runs = pd.read_csv(f"{A}/runs.csv")
cost = runs.groupby("tool")["cost_usd"].sum()
items = sc.set_index("tool")[["items", "confirmed"]]
per_asset = pd.concat([cost, items], axis=1).dropna().assign(
    per_found=lambda d: d["cost_usd"] / d["items"], per_confirmed=lambda d: d["cost_usd"] / d["confirmed"]
)
```

**Run-to-run stability (E0).** `stability.csv`: `jaccard_mean` per tool as a box plot over repositories.

**Matchers compared (RQ3).** `matchers-run1.csv`: `pair_jaccard` per repository, exact vs RaQuN.
