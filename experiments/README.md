# Experiments

Batch runs for the journal extension (see `CLAUDE.md`, Phase 1). Specs live here and are mounted
read-only into the `runner` container; everything they produce stays in Docker volumes.

```
make experiment EXP=experiments/e0-smoke.yaml      # pin commits, mirror to gitserver, run, persist
make experiment-status NAME=e0-smoke               # Redis progress + what is on disk
make experiment-export NAME=e0-smoke               # exports/e0-smoke.zip (only way data reaches the host)
make corpus ARGS="--per-language 100"              # stratified corpus -> results volume corpus/corpus.yaml
```

Spec format (`experiments/<name>.yaml`):

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

Offline analysis of a stored experiment (`analysis/`, no Redis), results under `<exp>/analysis/` on the volume:

```
make analysis ARGS="micro e0-micro"   # recall/precision per tool on the micro projects (corpus/micro/ground_truth.yaml)
make analysis ARGS="sites e0-micro"   # call-site clusters across tools: k distribution, confirmed/solo per tool
```
