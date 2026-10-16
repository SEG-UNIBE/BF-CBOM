# Experiments

Batch runs for the journal extension (see `CLAUDE.md`, Phase 1). Specs live here and are mounted
read-only into the `runner` container; everything they produce stays in Docker volumes.

```
make experiment EXP=experiments/e0-smoke.yaml      # pin commits, mirror to gitserver, run, persist
make experiment-status NAME=e0-smoke               # Redis progress + what is on disk
make experiment-export NAME=e0-smoke               # exports/e0-smoke.zip (only way data reaches the host)
make corpus ARGS="--per-language 100"              # stratified corpus -> results volume corpus/corpus.yaml
```

## Specs in this folder

| Spec | Tools | Repos | Purpose |
|---|---|---|---|
| `e0-smoke.yaml` | cdxgen, cryptobomforge | 2 | first pipeline test: pin, mirror to the git server, run, persist |
| `e0-cbomkit-smoke.yaml` | cbomkit | 1 | CBOMkit (server image) through the local git server over TLS |
| `e0-llm-smoke.yaml` | deepseek | 1 | the code-reading LLM worker on its own (needs `DEEPSEEK_API_KEY`) |
| `e0-phase2-smoke.yaml` | deepseek, cryptoscan | 2 | the Phase 2 workers together |
| `e0-micro.yaml` | all eleven generators | 5 micro projects (`corpus/micro`) | inclusion check against the hand-written ground truth; analyse with `make analysis ARGS="micro e0-micro"` |
| `e1-pilot-corpus.yaml` | all eleven generators | 16 real projects (`corpus/pilot-2026-10-08.yaml`) | first real workload: location-aware agreement on the pilot's pinned projects; large repos (Kafka, Keycloak) push CBOMkit towards its timeout |

The smoke tests are quick checks after changing a single worker. The plan's main experiments (E0 stability
with three repetitions, E1 on the full ~200-repo corpus, E4 on mutants; see `CLAUDE.md`, Phase 5) have no
specs yet; E1 needs the corpus from `make corpus` first.

## Spec format

Each spec is one `experiments/<name>.yaml`. An example with every field:

```yaml
name: e1-corpus            # [A-Za-z0-9._-]
tools: [cbomkit, cdxgen, cryptobomforge, deepseek]
repetitions: 3             # run_index 1..n per (repo, variant, tool)
corpus: corpus.yaml        # optional: repos from a corpus file (relative to this spec)
repos:                     # and/or explicit repos
  - full_name: authlib/authlib
    git_url: https://github.com/authlib/authlib.git   # default: GitHub
    branch: main           # default: main (resolved once, then frozen in lock.json)
    commit: <40-hex sha>   # optional pin; otherwise resolved with git ls-remote on first run
    language: Python       # helps language-specific tools
    variants: [original, m-rsa2048-insert-01]   # mutants are branches bfcbom/<variant> on gitserver
  - full_name: bfcbom/micro-java
    path: ../corpus/micro/java   # a plain directory (relative to this file) published to gitserver as a
    language: Java               # deterministic fixture commit; no git_url/commit (see corpus/README.md)
```

Rerunning a spec is resumable: runs whose `run<k>.meta.json` exists are skipped, commits come from
`lock.json`, and jobs still pending from an interrupted run are collected (`--requeue-pending` to reissue).
`ARGS=--retry-failed` redoes stored runs whose status is not `ok`.

**Memory budget (waves).** Every worker takes one job at a time, so a batch needs at most the sum of the
compose memory limits of the workers that have jobs queued (`memory_gb` in `common/tools.yaml`, kept equal
to `docker-compose.yml` by a test). `EXPERIMENT_MEMORY_BUDGET_GB` (Makefile, default 14 for a laptop whose
Docker Desktop VM has ~20 GiB) caps that sum: the runner packs the tools into waves (largest first), feeds
one wave, collects it, then feeds the next; workers of later waves sit idle meanwhile. With the default and
all eleven workers that is four waves. On a machine with enough RAM set it to 0 (one wave, everything in
parallel):

```
make experiment EXP=experiments/e1-pilot-corpus.yaml EXPERIMENT_MEMORY_BUDGET_GB=0   # per call
```

or change the default in the Makefile once per machine. The wave plan is printed before anything is
enqueued and stored in the experiment's Redis status (`waves`, `wave`). `--no-wait` enqueues only the
first wave; after `experiment collect` rerun `run` for the next one. Jobs left pending by an interrupted
run are already in the queues and are collected in wave 1 regardless of the budget.

## Analysis

The analysis (`analysis/`) works offline: it reads the stored CBOMs from the results volume and needs neither Redis
nor running workers. It uses only runs with status `ok` (failed, timed-out and `unsupported` runs are skipped) and,
by default, only algorithm assets and repetition 1.

```
make analysis ARGS="micro e0-micro"   # score each tool against the micro projects' ground truth
make analysis ARGS="sites e0-micro"   # match call sites across tools and count how many agree
make analysis ARGS="tables e0-micro"  # flat CSV tables of all of the above for pandas/R
```

**`micro <exp>`** scores every tool on the micro projects (`corpus/micro`, repos named `<owner>/micro-<language>`)
against `corpus/micro/ground_truth.yaml`, 11 known crypto assets per project. It prints one row per project and tool:

| Column | Meaning |
|---|---|
| strict | assets found at their ground-truth line with a compatible algorithm family, out of 11 |
| lenient | assets found at the right line, family compatible or unknown |
| precision | correct sites out of all sites the tool reported in the micro source file; a site is correct on an asset line or a line the ground truth marks as related or accepted |
| bar | `pass` when lenient recall is at least 50 % (the pilot's inclusion bar) |
| missed | ids of the assets the tool did not find |

**`sites <exp>`** extracts every reported location (file, line, algorithm family) and clusters sites of different
tools that lie in the same file within ±2 lines and share a family. Per repository it prints the number of sites and
clusters, the distribution of k (how many tools agree on a cluster) and per tool how many of its clusters are
confirmed by at least one other tool or solo. Options: `--window N` for the line distance, `--match-unknown` to let
sites of unknown family join clusters (off: it inflates weak tools), `--all-assets` to keep keys, certificates and
protocols, `--run K` for another repetition (also available for `micro`).

Both commands write to the experiment's folder on the volume:

```
<exp>/analysis/
├── micro-run1.json                       # micro: one object per (project, tool): recalls, hits, missed, precision, false sites, passes_bar
├── sites-run1.json                       # sites: settings, per repo extraction counts + agreement summary, overall k distribution
├── runs.csv                              # tables: one row per stored run (all repetitions)
├── sites-run1.csv                        # tables: one row per extracted call site (record)
├── groups-callsite-run1.csv              # tables: one row per call-site cluster
├── members-callsite-run1.csv             # tables: one row per (cluster, site) pair
├── micro-run1.csv                        # tables: one row per (micro project, tool); only with micro projects
├── tables-run1.json                      # tables: settings used (run, window, match_unknown, all_assets) and row counts
└── <owner>__<repo>@<sha12>/<variant>/
    └── clusters.jsonl                    # sites: one cluster per line: file, line range, k, tools, families, member sites
```

In `sites-run1.json`, `extraction` explains what was dropped per tool (components, occurrences, sites without a
location, out-of-scope files, unknown families with the 25 most frequent unknown names), and `agreement` holds the k
distribution, per-tool confirmed/solo counts and pairwise Jaccard similarity between tools.

**`tables <exp>`** writes the same results as flat, long-format CSV files (UTF-8, comma-separated, lists inside a
cell joined with `;`). It takes the same options as `sites` and records them in `tables-run<k>.json`. The tables come in
three layers, so later analysis steps (a normaliser producing simplified records per CBOM, further n-way matchers) only
add files and never change these:

| File | Layer | One row per | Main columns |
|---|---|---|---|
| `runs.csv` | runs | stored run, every repetition, incl. failed, timed-out and `unsupported` | repo, snapshot, commit, variant, tool, run, status, duration_sec, size_bytes, error, tool_version, image_ref, components, crypto_assets, algorithm_assets; for LLM workers requests, prompt_tokens, completion_tokens |
| `sites-run<k>.csv` | records | call site of one tool (file, line) | snapshot, variant, tool, run, record_id (`<tool>:<file>:<line>`), file, line, families, raw, asset_types, in_scope |
| `groups-callsite-run<k>.csv` | matches | cluster of the call-site matcher | matcher, snapshot, variant, run, group_id, k, tools, file, line_min, line_max, n_sites, families (`name:count`) |
| `members-callsite-run<k>.csv` | matches | (cluster, site) pair | matcher, snapshot, variant, run, group_id, record_id, tool |
| `micro-run<k>.csv` | ground truth | micro project and tool | project, language, tool, strict, lenient, recall_strict, recall_lenient, sites_in_file, sites_correct, precision, passes_bar, missed |

Join keys: records on `(snapshot, variant, run, record_id)`, groups on `(snapshot, variant, run, group_id)`. Group ids
(`C00000`, ...) restart per snapshot and variant. Out-of-scope sites (`in_scope=false`, e.g. config files) are listed
but never clustered, so they have no member row. `snapshot` is the result folder name `<owner>__<repo>@<sha12>`; `repo`
and `commit` carry the full name and SHA.

```python
import pandas as pd
sites = pd.read_csv("analysis/sites-run1.csv")
members = pd.read_csv("analysis/members-callsite-run1.csv")
groups = pd.read_csv("analysis/groups-callsite-run1.csv")
keys = ["snapshot", "variant", "run"]
df = sites.merge(members, on=keys + ["record_id", "tool"]).merge(groups, on=keys + ["group_id"], suffixes=("", "_group"))
groups["tools"].str.split(";")          # list cells
```

**Export.** The analysis files live inside the experiment folder, so the normal export takes them along together with
the spec, `lock.json`, every CBOM and its meta file:

```
make experiment-export NAME=e0-micro   # -> exports/e0-micro.zip on the host
```

