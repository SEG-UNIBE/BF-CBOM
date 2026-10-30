# Figures from the analysis tables

The analysis writes flat CSV tables into `<exp>/_ANALYSIS/` (`experiments/README.md` lists every file and column).
The figures of the paper are built from them later, by hand, with pandas and matplotlib; nothing here runs in the
pipeline. Use one accessible palette consistently, with light and dark variants where needed.
Recipes, one per planned figure:

```python
import pandas as pd
A = "experiments/results/corpus-icpc/_ANALYSIS"   # or any copied export
```

**Tools per item, UpSet plot (RQ5).** Which voter groups report which items per repository.
```python
groups = pd.read_csv(f"{A}/groups-exact-run1.csv").query("scope == 'repo'")
# one row per (repo group, voter group); `voters` is a ';'-joined list in groups-exact
sets = groups.dropna(subset=["voters"]).assign(voter=lambda d: d["voters"].str.split(";")).explode("voter")
# Group ids restart in each snapshot: keep the complete key.
keys = ["snapshot", "variant", "run", "group_id"]
memberships = pd.crosstab([sets[c] for c in keys], sets["voter"]).astype(bool)  # UpSet input
```

**Fill-rate heatmap (RQ2).** Share of algorithm assets per tool and dimension with a reported value.
```python
cov = pd.read_csv(f"{A}/coverage-run1.csv")
heat = cov.pivot(index="tool", columns="dimension", values="reported_share")
```

**Meaningful: standard fields vs any source (RQ3).**
```python
m = pd.read_csv(f"{A}/meaningful-run1.csv").query("asset_type == 'algorithm'")
pd.melt(m, id_vars=["tool", "dimension"], value_vars=["standard_share", "any_share"])   # grouped bars per dimension
```

**The normalisation ladder.** Confirmed share per tool from L0 to L4; parameters from L2.
```python
lv = pd.read_csv(f"{A}/levels-run1.csv")
ladder = lv.query("dimension == 'algorithm' and tool != 'all'").pivot(index="tool", columns="level", values="share")
ladder = ladder[["L0", "L1", "L2", "L3", "L4"]]       # one line per tool, levels on the x axis
params = lv.query("dimension != 'algorithm' and tool == 'all'").pivot(index="dimension", columns="level", values="share")
```

**Matchability per level (RQ4).** Items by the number of voter groups in their largest group, and the agreement per
dimension inside the groups, exact matcher vs RaQuN, level by level.
```python
ml = pd.read_csv(f"{A}/matchlevels-run1.csv")
k = ml.set_index(["matcher", "level"])[["k1", "k2", "k3", "k4plus"]].div(ml["items"].values, axis=0)  # stacked bars
agree = ml.set_index(["matcher", "level"]).filter(like="_agree")                                   # heatmap
```

**Per-tool rates with confidence intervals (RQ5; the ranking by F1 is RQ6's, `mutation-scores.csv`).**
```python
sc = pd.read_csv(f"{A}/score-run1.csv").query("dimension == 'algorithm'")
ranked = sc.dropna(subset=["rank", "ci_low", "ci_high"]).sort_values("rate")
# dot = rate (mean per-repository confirmation), whiskers = ci_low .. ci_high
err = [ranked["rate"] - ranked["ci_low"], ranked["ci_high"] - ranked["rate"]]
```
Stability of the ranking: `loo-run1.csv` (`kendall_tau` per left-out voter group); stability across levels: rerun
`make analysis ARGS="report <exp> --level L2"` and compare `rank`. Save the previous tables first:
each report replaces `_ANALYSIS/`.

**Cost per found and per confirmed item (for historical or future LLM runs).** The current experiment specs do
not include these providers. Use a report scored on one repetition (`--run 1 --no-consensus`) so cost and item
counts refer to the same runs.
```python
runs = pd.read_csv(f"{A}/runs.csv").query("variant == 'original' and run == 1 and tailored == False")
cost = runs.groupby("tool")["cost_usd"].sum()
items = sc.set_index("tool")[["items", "confirmed"]]  # sc from the single-repetition report above
per_item = pd.concat([cost, items], axis=1).dropna().assign(
    per_found=lambda d: d["cost_usd"] / d["items"], per_confirmed=lambda d: d["cost_usd"] / d["confirmed"]
)
```

**Run-to-run stability.** `stability.csv`: `jaccard_mean` per tool as a box plot over repositories.

**Matchers compared (RQ5).** `matchers-run1.csv`: `pair_jaccard` per repository, exact vs RaQuN.
