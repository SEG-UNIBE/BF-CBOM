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
```

Rerunning a spec is resumable: runs whose `run<k>.meta.json` exists are skipped, commits come from
`lock.json`, and jobs still pending from an interrupted run are collected (`--requeue-pending` to reissue).
`ARGS=--retry-failed` redoes stored runs whose status is not `ok`.
