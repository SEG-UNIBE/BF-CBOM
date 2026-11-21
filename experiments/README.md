# Experiments

How to run BF-CBOM's batch experiments and what they produce.

- Specs live in this folder and are mounted read-only into the `runner` container.
- Everything a run produces stays in Docker volumes until it is exported. Exports are the only way results reach
  the host.
- Every command runs inside the `runner` container through `make`, never through plain `docker compose`.

```
experiments/
├── *.yaml       # experiment specs
├── corpus/      # pinned repository lists, exclusions and micro fixtures
└── results/     # exported ZIPs and extracted results (ignored by Git)
```

| To read about | Go to |
|---|---|
| Setting up the stack | [root README](../README.md#setup) |
| How the analysis reads the CBOMs: units, normalization, the ladder, voter groups | [`analysis/README.md`](../analysis/README.md) |
| The repository lists and fixtures | [`corpus/README.md`](corpus/README.md) |
| Plot recipes on the analysis tables | [`analysis/report/README.md`](../analysis/report/README.md) |

**On this page**

- [Commands](#commands)
- [Specs in this folder](#specs-in-this-folder)
- [Spec format](#spec-format)
  - [Rerunning a spec](#rerunning-a-spec)
  - [Mutants](#mutants-mutation)
  - [Corpus sampling](#corpus-sampling)
  - [The record](#the-record-which-repos-an-experiment-used)
  - [What an experiment folder holds](#what-an-experiment-folder-holds)
- [Running an experiment](#running-an-experiment)
  - [Watching a run](#watching-a-run)
  - [The stack board](#the-stack-board)
  - [Parallelism and memory](#parallelism-and-memory)
- [Analysis](#analysis)
  - [Analysis commands](#analysis-commands)
  - [Output files](#output-files)
  - [Table reference](#table-reference)
  - [Joining the tables](#joining-the-tables)
  - [Export](#export)

## Commands

### Running and managing experiments

| Command | What it does |
|---|---|
| `make experiment EXP=experiments/bar-micro.yaml` | pins the commits, mirrors them to the git server, enqueues the jobs and collects the results (resumable) |
| `make experiment EXP=... ARGS="--overwrite-old"` | runs the whole spec as if it had never run (other `ARGS`: `--retry-failed`, `--rerun <tools>`) |
| `make experiment-status NAME=bar-micro` | shows the Redis progress and what is on disk |
| `make experiment-attach` | joins the terminal of the running `make experiment` (detach with Ctrl-P Ctrl-Q) |
| `make experiment-collect NAME=bar-micro` | collects the results of jobs already queued (after `ARGS=--no-wait`) |
| `make experiment-cancel NAME=bar-micro` | removes its queued jobs from the worker queues |
| `make experiment-list` | lists the experiments stored on the volume |
| `make experiment-delete NAME=bar-micro` | removes one from the volume (asks first; `ARGS=--yes` skips the question) |
| `make experiment-export NAME=bar-micro` | writes `experiments/results/bar-micro.zip` (runs the analysis report first) |
| `make experiment-export-all` | exports every complete experiment (no run missing), one ZIP each, overwriting |

### Corpus

```sh
# a stratified GitHub corpus: corpus/corpus.yaml and corpus-frame.yaml on the results volume
make corpus ARGS="--per-cell 3"

# redraw from a stored frame, without a search (without --out it replaces corpus.yaml)
make corpus ARGS="--frame /data/results/corpus/corpus-frame.yaml --per-cell 2 --out /data/results/corpus/corpus-pc2.yaml"

# repeat the paper's draw exactly (a frozen redraw, see "Corpus sampling")
make corpus ARGS="--frame /app/experiments/corpus/corpus-pc6-frame.yaml.zip --as-of 2026-10-30T15:05:23Z --per-cell 6 --out /data/results/corpus/corpus-pc6.yaml"

# drop the repos a tool timed out on
make corpus-prune ARGS="--in /data/results/corpus/corpus.yaml --from stability-pilot"

# mutation-main's corpus: the 30 smallest clean repos per language, pinned
make corpus-smallest ARGS="--from corpus-main --out /data/results/corpus/mutation-main.yaml"

# copy the corpus YAMLs (sample and frame) to experiments/results/
make corpus-export
```

### Analysis and the stack

| Command | What it does |
|---|---|
| `make analysis ARGS="report bar-micro"` | runs the analysis alone, into `<exp>/_ANALYSIS/` ([Analysis](#analysis)) |
| `make board` | opens the live board of the stack: experiments, workers, queues (`ARGS=--once`: one snapshot) |

An unzipped export is analyzed on the host without Docker:
`RESULTS_DIR=experiments/results uv run bf-cbom analysis report <exp>` ([Analysis](#analysis)).

## Specs in this folder

The name says the kind of spec, then what it runs on.

| Prefix | Kind of spec |
|---|---|
| `bar-` | the bar exam on ground truth |
| `stability-` | repeated runs |
| `corpus-` | real repositories as they are |
| `mutation-` | real repositories with their mutants |

### The specs

| Spec | Tools | Repos | Purpose |
|---|---|---|---|
| `bar-micro.yaml` | all 14 workers, once | the 3 micro projects (`experiments/corpus/micro`: Java, Python, Go) | the bar exam: the micro check against the generated ground truth (`make analysis ARGS="micro bar-micro"`), whose verdicts are written into the later specs by hand (`tools`, `tool_languages`; at least 7 of 14 in their functions in one paper language); 42 jobs |
| `stability-pilot.yaml` | the 10 eligible tools (cdxgen in Go only), 3 repetitions | 3 micro projects + the 10 fastest pilot repos | run-to-run variance (`stability.csv`) and consensus sets for the scores; 390 jobs at most |
| `corpus-pilot-small.yaml` | the 10 eligible tools (cdxgen in Go only) | the 12 pilot projects in Java, Python and Go (`experiments/corpus/pilot-2026-10-08.yaml`) | first real workload: location-aware agreement on the pilot's pinned projects; large repos (Kafka, Keycloak) push CBOMkit towards its timeout |
| `corpus-pilot-medium.yaml` | the 10 eligible tools (cdxgen in Go only), once | 60 manually selected projects, 20 each in Java, Python and Go (`experiments/corpus/pilot-2026-10-22.yaml`) | second pilot, pinned to default-branch HEADs on 2026-10-22; 900 tool/repository pairs |
| `corpus-icpc.yaml` | the 10 eligible tools (cdxgen in Go only) | 23 projects of the ICPC 2026 paper (`experiments/corpus/icpc-2026.yaml`, HEAD of 2026-10-18) | a pilot set: no tool or spec was tuned on these repos, but their review of 2026-10-19 prompted a few vocabulary entries; six are marked `expect_crypto: low` (negative cases) |
| `corpus-main.yaml` | the 10 eligible tools (cdxgen in Go only), once | the stratified GitHub sample of 2026-10-30 (`experiments/corpus/corpus-pc6.yaml`): 54 repos each in Java, Python and Go, 6 per cell | the paper's corpus run (Content, Normalization, Agreement); 1620 tool/repository pairs, 1134 supported scans |
| `mutation-smoke.yaml` | the 10 eligible tools (cdxgen in Go only) | pyjwt, jjwt, golang-jwt (pilot), 3 mutants each | the mutation harness end to end: the RSA-2048 keygen inserted in each style; CBOMkit and CryptoScan should find the direct one at its line (`mutation.csv`) |
| `mutation-main.yaml` | the 10 eligible tools (cdxgen in Go only), once | per language the 30 repos of corpus-main with the smallest checkout where every supported tool ran ok, at its pinned commits (`make corpus-smallest` after corpus-main) | Detection: every snippet in every style and as many decoys (15 + 15 per repo, 31 variants) |
| `mutation-pilot.yaml` | the 10 eligible tools (cdxgen in Go only), once | 9 pinned pilot-medium repos, 3 per language | mutation pilot: every catalog snippet once per repo (15 calls, 5 per style) and 15 decoys; 2790 results, 1953 of them supported scans |

Notes on the table:

- LLM providers are outside the current experiment scope; their implementation remains available for possible later
  use.
- Every tool votes. The micro check reports the bar exam's verdicts but does not edit `common/tools.yaml` or the
  specs.
- Job totals include tool/repository pairs recorded as `unsupported` without scanning.
- `stability-pilot` includes four pilot repositories outside the paper languages (TypeScript, JavaScript and C).

### From the corpus run to the mutation run

The corpus run is `corpus-main.yaml` (the corpus `experiments/corpus/corpus-pc6.yaml`). The mutation run,
`mutation-main.yaml`, takes its corpus from corpus-main's record:

1. After corpus-main, run
   `make corpus-smallest ARGS="--from corpus-main --out /data/results/corpus/mutation-main.yaml"`.
2. It writes, per language, the 30 repositories with the smallest checkout among those where every supported tool's
   run on the original was ok. The checkout is all files at the pinned commit, which every tool scans, so it is
   what the run costs.
3. The file holds their commits, the excluded repositories and the projected length of the mutation run, over every
   repository and over the selection (31 runs per repository, one container per tool).
4. A language with fewer than 30 clean repositories keeps all of them, with a warning and its count under `short`
   in the file.
5. `make corpus-export` copies the file to `experiments/results/`, and it is committed as
   `experiments/corpus/mutation-main.yaml`.

### After a change to one worker

- `make experiment EXP=experiments/mutation-smoke.yaml ARGS="--rerun <tool>"` redoes its runs on three small repos
  and their mutants, one repo per paper language.
- The same on `bar-micro.yaml` redoes its micro check (3 jobs).

### Second pilot

60 repositories. On the VM, after `make up-prod`:

```sh
make experiment EXP=experiments/corpus-pilot-medium.yaml EXPERIMENT_MEMORY_BUDGET_GB=0
make experiment-status NAME=corpus-pilot-medium
make experiment-export NAME=corpus-pilot-medium   # generates the report and experiments/results/corpus-pilot-medium.zip
```

- The corpus records the full repository names and commits.
- It is a manual selection, separate from the stratified corpus built by `make corpus`.
- Rerunning the command resumes this experiment at the pinned commits.

### Mutation pilot

`mutation-pilot.yaml` runs on nine repositories of corpus-pilot-medium, three per language. It is a purposive
development cohort, not a sample: it validates the harness, and Detection's numbers come from mutation-main.

- **Why three per language.** Three is the floor for a bootstrap interval over repositories (`analysis/score.py`
  `MIN_REPOS`, which `mutation-scores.csv` and the bridge use), so every per-language recall, precision and F1 of the
  pilot comes with an interval.
- **How they were selected**, by hand: clean scans (every supported tool ok, no OOM kill at the pinned commits),
  several tools reporting algorithms at first-party locations, algorithm diversity, low runtime, and a context
  different from the other two repositories of the language.

| Repository | Language | Context |
|---|---|---|
| `mwiede/jsch` | Java | SSH library |
| `beemdevelopment/Aegis` | Java | authenticator app |
| `ESAPI/esapi-java-legacy` | Java | web-security API |
| `Yubico/yubikey-manager` | Python | hardware-key management |
| `ssh-mitm/ssh-mitm` | Python | SSH interception |
| `cowrie/cowrie` | Python | honeypot |
| `pion/dtls` | Go | transport cryptography |
| `go-webauthn/webauthn` | Go | WebAuthn |
| `gopasspw/gopass` | Go | password manager |

- `per_repo: 15` injects every catalog snippet once per repository. The generator takes the least-used snippet per
  style, so 15 calls are 5 per style.
- `decoys: equal` injects as many decoys.
- With three repositories per language every (snippet, style) cell of a language is filled three times.
- With the 10 eligible tools that is 9 x 31 variants x 10 tools = 2790 results, 1953 of them supported scans (Java
  7, Python 8, Go 9 tools).
- `mutation-smoke` runs first.

```sh
make experiment EXP=experiments/mutation-pilot.yaml EXPERIMENT_MEMORY_BUDGET_GB=0
make experiment-status NAME=mutation-pilot
make experiment-export NAME=mutation-pilot
```

## Spec format

Each spec is one `experiments/<name>.yaml`. An example with every field:

```yaml
name: corpus-example       # [A-Za-z0-9._-]
tools: [cbomkit, cdxgen, cryptobomforge, cryptoscan]
repetitions: 3             # run_index 1..n per (repo, variant, tool)
tool_languages:            # optional: narrow a tool to these languages in this experiment, by hand (e.g. after
  cdxgen: [go]             # reading the bar-micro report); tools not named run wherever common/tools.yaml declares support
corpus: corpus.yaml        # optional: repos from a corpus file (relative to this spec)
repos:                     # and/or explicit repos
  - full_name: authlib/authlib
    git_url: https://github.com/authlib/authlib.git   # default: GitHub
    branch: main           # default: main (resolved once, then frozen in the experiment's _experiment.yaml)
    commit: <40-hex sha>   # optional pin; otherwise resolved with git ls-remote on first run
    language: Python       # helps language-specific tools
    expect_crypto: low     # optional corpus metadata for the analysis (a negative case: little crypto expected)
    variants: [original, my-branch]   # optional: variants are branches bfcbom/<variant> in the mirror (not with mutation:)
  - full_name: bfcbom/micro-java
    path: corpus/micro/java   # a plain directory (relative to this file) published to gitserver as a
    language: Java               # deterministic fixture commit; no git_url/commit (see experiments/corpus/README.md)
mutation:                  # optional: every repo also gets mutants as variants (Detection, below)
  per_repo: 10             # mutants per repo (fewer when not enough (snippet, style) pairs fit a host)
  styles: [direct, wrapper, indirect]
  snippets: all            # or a list of catalog ids (mutation/catalog/<language>.yaml)
  seed: 1
  decoys: equal            # injected decoys per repo: `equal` (as many as calls, default), an integer, 0
```


### Rerunning a spec

Rerunning a spec is resumable:

- Runs whose `run<i>.meta.json` exists are skipped.
- Commits come from the experiment's `_experiment.yaml` ([the record](#the-record-which-repos-an-experiment-used)).
- Jobs still pending from an interrupted run are collected (`--requeue-pending` reissues them).

| Option or command | What it redoes |
|---|---|
| `ARGS=--retry-failed` | stored runs whose status is not `ok` |
| `ARGS="--rerun <tool>[,<tool>]"` | every stored run of those tools, whatever its status (after a worker change, e.g. `--rerun cdxgen,cryptobomforge`) |
| `ARGS=--overwrite-old` | the whole spec as if it had never run |
| `make experiment-delete NAME=<exp>` | nothing: it removes the experiment folder and cancels the experiment's jobs |

`--overwrite-old` first cancels the jobs an earlier run left and removes the experiment folder with everything in
it: the stored runs, the record, `_run-log.json`, `mutants.json`, `_ANALYSIS/` and files of older layouts. Then it
resolves the spec afresh, and the new session records `runs_overwritten`.

Results are never kept for their own sake. The spec pins every repo's commit (the corpus files and the corpus
builder pin them; a spec without a commit gets the branch head of the day) and the mutants are deterministic, so a
rerun scans the same snapshots and repeats the results.

### Mutants (`mutation:`)

For every repo of a spec with a `mutation:` block the runner makes `per_repo` insertion mutants before anything is
enqueued (`mutation/mutants.py`).

- **What a mutant is.** One catalog snippet (`mutation/catalog/{java,python,go}.yaml`, by the repo's `language`) in
  one style, inserted into a first-party method or function that has a caller (or a `main`). It is committed on the
  pinned commit as the branch `bfcbom/ins-<snippet>-<style>-<hash>` of the base mirror and scanned like any variant.
- **Where a snippet goes.** Only where its library is already declared: pyca `cryptography` for Python's asymmetric
  and AES snippets, Go 1.24 or `golang.org/x/crypto` for Go's PBKDF2. Dependency files are never edited.
- **Pinned like the commits.** `mutants.json` pins the mutants like `_experiment.yaml` pins the commits. A resumed
  run keeps them (a changed `mutation:` block is ignored with a note) and rebuilds missing branches with the same
  SHA from the recorded inserts. `--overwrite-old` makes them again, the same ones.
- **What is recorded per mutant.** The snippet, style, catalog version, file, host (name, line, number of callers
  by name), the line naming the algorithm (`call_line`), the related lines, the inserted spans, the line shifts, the
  inserts, the expected record and the patch.
- **Repos without mutants.** A repo without a catalog language or a host gets none, with a note.

### Corpus sampling

`make corpus` (`common/corpus.py`; design of 2026-10-29) builds the main corpus as a stratified sample of GitHub.

**The design**

- For each paper language the cells are star bin x size bin.
- Defaults: `--star-bins 50-300,300-2000,2000-` and `--size-bins 300-3000,3000-30000,30000-`, in KB of GitHub's
  packed `size`, bins `[lo, hi)`.
- The open last size bin ends at the repo size cap: 1536 MB (`CORPUS_MAX_REPO_SIZE_MB`), a quarter of the git
  server's 6 GB when the paper's corpus was built, so that a full clone through it always fit.
- Every cell gets the same quota, `--per-cell`. The default 2 gives 3 x 3 cells x 2 = 18 repos per language, 54 in
  all; `--per-cell 3` gives 27 and 81.
- Equal allocation per cell is deliberate: the sample covers the design space evenly for a comparison of tools
  across strata. It is no estimate of the GitHub population.

**The draw**

1. The builder enumerates each cell's whole population: `pushed:` within 365 days, `archived:false fork:false`,
   paginated to the API's limit of 1000 results. A cell above that limit is split by creation date until every part
   fits.
2. It draws from the name-sorted population with the seed (`--seed`).
3. It keeps the first `per_cell` candidates that pass three checks, so the draw is uniform over the eligible repos
   of the cell:
   - the exclusions (`experiments/corpus/exclusions.yaml`): the generators' own repositories, `tailored_on`, the
     name patterns and, as its `pilot_sets`, every repository scanned in a pilot run before the draw, so the corpus
     is held out from what the instrument was developed on;
   - the size cap;
   - the crypto pre-filter: a shallow clone and a grep of first-party source for the crypto API families of the
     language.

There is no topic targeting: the crypto-use pre-filter is the one content criterion.

**What the corpus file records**

| Entry | Content |
|---|---|
| `params` | the frame's parameters: search date, bins, quota |
| `cells` | every cell with its `population` and how many were enumerated, tried and sampled |
| each sampled repo | its `stratum` and two covariates that are recorded but never selected on: `crypto_apis` and `topics` |
| `crypto_apis` | the API families the pre-filter matched: JCA, BouncyCastle, JSSE; pyca, hashlib, ssl, pycryptodome, nacl, pyOpenSSL, other; Go stdlib, x/crypto |
| `topics` | the repository's GitHub topics |
| `excluded` | every candidate dropped, with its reason |

The record `_experiment.yaml` carries `stratum`, `crypto_apis` and `topics` per repo.

**The frame file**

Next to the corpus file, `<corpus>-frame.yaml` holds the populations themselves (every enumerated repo of every
cell, its query and the search date) and the crypto checks by commit.

- The draw can be verified offline: frame + seed -> sample.
- A rebuild with `--frame` draws another `--per-cell` or `--seed` from the same population without a search
  request. On one frame the smaller quota's sample is inside the larger one's.
- A check is reused while the repo's default branch is at the same commit.
- An interrupted build resumes from its frame on the same day.
- `--frame` also reads the zipped frame the repository keeps.

**A redraw** (`--frame <frame>`)

- It needs the frame's languages, bins and cap. Anything else is a new search.
- It pins every drawn repo at its current default-branch commit. A repo that moved since is checked again, and one
  that no longer passes gives way to the next candidate of its cell.
- It writes `--out` (default `corpus.yaml`, which it replaces) with a copy of the frame next to it,
  `<out>-frame.yaml`.

**A frozen redraw** (`--frame <frame> --as-of <moment>`, 2026-11-18) repeats a draw instead of moving it.

- Every repository the frame holds a check for keeps that commit and that result, with no request at all.
- A candidate the first draw never reached is pinned at the last commit of its default branch at or before the
  moment (by committer date) and checked at that commit.
- Frame, seed and exclusions then give the same corpus on any day, which a test checks for the paper's
  (`tests/test_corpus.py`).
- A changed exclusion list replaces only what it excludes, by the next candidate of the cell.
- The paper's corpus was redrawn this way when the pilot sets joined the exclusions. `--as-of` is the
  `generated_at` of the first draw, and the corpus file records it (`params.as_of`).

**Where to run it.** The builder belongs in the runner for one more reason: the pre-filter's Python patterns use
`\s` and `\b`, which git's grep reads on Linux and not on a Mac, where they match nothing. There the check stops
with that message instead of dropping every Python repository.

**Pruning.** After the stability run `make corpus-prune` drops the repos a tool timed out on (listed under
`pruned`, never replaced).

### The record: which repos an experiment used

One file answers which repos an experiment used: its record, `_experiment.yaml`.

- **What it holds.** The spec as run, the tools, the repetitions, the `mutation:` block, and every repo with the
  branch and commit it was resolved to (a fixture with its `path` and published commit) and its size at that commit.
- **The sizes.** `files` and `source_files` (the latter of the repository's language), `checkout_bytes` and
  `source_bytes` (the blobs without history). They are counted from the mirror, so an export carries the size the
  run-time table scales by and the summary's "Repositories" table lists.
- **When it is written.** `experiment run` writes it once the snapshots resolve. A resumed run (without
  `--overwrite-old`) keeps its commits for every repo the spec still names, so it never moves to a newer upstream
  commit halfway. A repo the spec dropped leaves the record, and `mutants.json` holds the mutants of exactly the
  record's repos.
- **What reads it.** The runner's overview and the analysis read only the stored runs of the repos, tools and
  repetitions the record names (`common/experiment.py:iter_results`), so a run stored under an earlier version of the
  spec never reaches a table. The `_SUMMARY.md` header repeats the counts.
- **It is itself a fully pinned spec.** `experiment run` on it replays exactly those snapshots. The spec in
  `experiments/` and its corpus file are the input that produced it; an export carries the record.

### What an experiment folder holds

| Entry | Content |
|---|---|
| `_experiment.yaml` | the record (above) |
| `_run-log.json` | one entry per `experiment run` / `collect` session |
| `_ANALYSIS/` | the derived tables and `_SUMMARY.md`, regenerated by `analysis report` |
| `mutants.json` | the mutants, with a `mutation:` block |
| one folder per snapshot | one subfolder per variant (`original` and the mutants), with the runs of each tool |

The leading underscores keep the first three on top of a file listing.

**Every `run<i>.meta.json` records:**

| Fields | Meaning |
|---|---|
| `peak_memory_mb` | the worker container's peak working set during the job, sampled every second (the figure `docker stats` shows) |
| `memory_limit_mb` | the container's memory limit |
| `limit_hits` | how often its usage reached that limit. 0: the limit never constrained the job |
| `oom_kills` | the OOM kills during the job. On a host without swap, memory the kernel cannot reclaim ends in an OOM kill |
| `cpu_seconds`, `cpu_cores` | the container's CPU time during the job, and that over the job's wall time: the mean number of cores it kept busy. Read from the cgroup's `cpu.stat` at the job's start and end, no sampling |
| `enqueued_at`, `started_at`, `finished_at` | when the job was enqueued, started and finished |
| `bfcbom_commit` | the BF-CBOM commit the worker image was built from (`-dirty` = uncommitted changes) |
| `usage` | for LLM workers: requests, input / cached / cache-write / output / reasoning tokens, the model ids the provider answered with; kept on failed runs |

**Every session in `_run-log.json` records:**

- its start and end, the Docker VM's RAM and CPUs, the memory budget and waves, and the peak RAM in use on the VM,
  sampled every 2 s;
- the runner's image and commit, and the image ids of the workers and of the git server (by content: the images
  tagged on the host);
- the commit every running container was built from (`builds`, read by make from each container's
  `BF_CBOM_COMMIT`);
- the runs on disk before it, apart from the `unsupported` markers.

`make experiment` refuses to start when a worker of the spec or the git server was built from another commit than
the runner, since its runs would measure other code. `make up-prod` rebuilds them, and `make board` shows each
container's build.

## Running an experiment

### Watching a run

On a terminal `run` and `collect` keep a live board at the bottom (`misc/cli/progress.py`).

| Line | Shows |
|---|---|
| all results | done/total, remaining time, elapsed time, the wave and the statuses so far |
| one per tool | its count and remaining time, the job it runs now and for how long (the longest-running one first, from the start times the workers write to Redis), how many wait and what failed |

- **The forecast** (`progress.forecast`): a tool's jobs left times its mean duration so far. The batch ends with
  its slowest tool. Tools of a later wave wait with their clock still.
- **Result lines.** Every stored result still prints its own line above the board: status, tool, snapshot, run,
  duration, path.
- **Pinning and mirroring** count the repos the same way. A line announces the two slow cases, a mirror cloned on
  first use and a fetch for a pinned commit the mirror lacks; everything else is local.
- **`corpus build`** counts per language: repos picked out of the quota, the cell searched or the repo cloned for
  the crypto check.
- **Off a terminal** only the lines print.

Joining and leaving a run:

- The display belongs to the terminal `make experiment` runs in. `make experiment-attach` joins it from another
  shell (tmux window, second ssh session) and shows the board and the result lines from then on.
- Detach with Ctrl-P Ctrl-Q. Ctrl-C there interrupts the run exactly as in its own terminal, and
  `make experiment-collect` resumes it.
- `--no-wait` enqueues only the first wave; after `make experiment-collect NAME=<exp>` rerun `run` for the next
  one.
- Jobs left pending by an interrupted run are already in the queues and are collected in wave 1 regardless of the
  budget.

### The stack board

`make board`, in a second terminal, shows the whole stack:

- **Every running experiment**: a bar, results stored out of expected, its wave, the failures so far and the time
  left (the same forecast as the runner's board; `≥` while a tool has no result yet). The bar counts the results the
  runner has stored, so after `ARGS=--no-wait` it moves once `make experiment-collect` runs.
- **Every worker**: busy, idle or down, the commit its containers were built from (`build`, red when it is not the
  checkout's), the job it runs (repo, run, experiment) and for how long, the container's RAM against its limit, the
  jobs queued for it and its last result.
- **The stack**: the checkout's commit, whether Redis, the git server and the UI answer, and the host's RAM.
- **A red line** names a running container (a worker, the git server, the runner) built from another commit than
  the checkout; `make up-prod` rebuilds it. Check it after every pull: the stored runs carry each image's commit
  (`bfcbom_commit`), and an experiment run on old images measures the old code.

`Ctrl+C` closes the board; the experiment and the stack go on. `make up-prod` opens the board by itself once the
stack is up, `make logs` shows the containers' raw logs, and `make down` stops the stack.

### When a run ends

The run prints an overview (`misc/cli/overview.py`):

- runs stored out of expected;
- per tool the counts per status, median and longest duration, peak RAM against the limit and OOM kills;
- the LLM token usage with an estimated cost;
- every run that is not ok with its most telling error line;
- the commands to retry, resume or export.

`make experiment-status` shows the same plus the live progress from Redis, `make experiment-list` one line per
experiment.

### Parallelism and memory

The laptop prepares code and analyzes copied exports; the VM runs the batches with
`EXPERIMENT_MEMORY_BUDGET_GB=0`.

**One job per worker container**

- Each worker pops one job from its Redis queue, runs the tool in a subprocess, pushes the result and only then
  takes the next.
- A batch has as many jobs in flight as there are worker containers (with `make up-prod` one per generator, each on
  a different repository). Queued jobs cost only a Redis entry.

**The git server serves clones side by side**

- Every worker clones its repository from the local git server (nginx, git's smart-HTTP backend through fcgiwrap).
- 16 fcgiwrap processes serve up to 16 requests at once, more than the workers that clone at once.

**Largest first**

- Every tool's queue gets the repositories largest first (`checkout_bytes`, all files at the pinned commit, which
  every tool scans), so the longest jobs do not start last and set the batch's end.
- Early on, the remaining-time forecast is therefore too high: its mean so far comes from the largest repositories.

**Memory limits are caps, not reservations**

- Every worker has a memory limit in `docker-compose.yml`, mirrored by `memory_gb` in `common/tools.yaml`. A test
  keeps them equal; adjust both.
- Idle workers use little, large jobs can reach their caps, and the VM also needs memory for the coordinator, Redis
  and the git server (`GITSERVER_MEMORY`, 16g, which leaves room for the concurrent full clones of CBOMkit
  replicas).
- On the VM the caps of every container an experiment feeds, replicas included, plus those of the services stay
  within its RAM (corpus-main, without replicas: 121 of 121.5 GiB). Every job can then reach its cap at the same
  time, a tool's reported peak is its own and not our limit's, and the kernel never has to pick Redis or the git
  server for an OOM kill.
- CBOMkit's limit is set by its Java heap (`JAVA_OPTS` `-Xmx`, 4 GiB below the container's limit). A full heap ends
  the job as a failure, not an OOM kill, so the two rise together.

**Memory budget (waves)**

- `EXPERIMENT_MEMORY_BUDGET_GB` (Makefile, default 14 for a laptop whose Docker Desktop VM has ~20 GiB) caps the
  summed limits of the tools fed at once.
- The runner packs the tools into waves (largest first), feeds one wave, collects it, then feeds the next. Workers
  of later waves sit idle meanwhile.
- A tool above the budget runs alone with a warning, so the budget is not a hard cap.
- The plan is printed before anything is enqueued and stored in the experiment's Redis status (`waves`, `wave`).
- On the VM use 0 (one wave, everything in parallel), per call
  (`make experiment EXP=... EXPERIMENT_MEMORY_BUDGET_GB=0`) or once in the Makefile.
- The UI applies the same budget: jobs of later waves are created *held* (⏸️) and queued once the current wave is
  done.

**Replicas**

- Workers are stateless, so several containers can share a tool's queue:
  `make up-prod REPLICAS="cbomkit=3,cipherradar=3,scanoss=2"` starts that many (`--scale worker-<tool>=N`).
- Every container writes a heartbeat of its own (`worker:<tool>:replica:<host>`), and an experiment counts them when
  it starts and on every refresh, so `make experiment` needs nothing more.
- The budget counts each replica, the forecast shares a tool's remaining jobs among its containers, `_run-log.json`
  and the summary record the replicas, and the board shows a tool's containers in one row (`busy 2/3`, their summed
  RAM).
- A later `make up-prod` without `REPLICAS` scales back to one container each; on the VM, `export REPLICAS=...` in
  the shell profile keeps the choice.
- Run times measured with replicas include their contention for the CPUs.

**The analysis report in parallel:** `--jobs` and `RUNNER_MEMORY`, see [In parallel](#in-parallel).

## Analysis

This section lists the commands, the files they write and their columns.

- What the analysis counts and why (units, normalization, the ladder, voter groups, family and group):
  [`analysis/README.md`](../analysis/README.md).
- Plot recipes on these tables: [`analysis/report/README.md`](../analysis/report/README.md).

### What the analysis reads

- The analysis (`analysis/`) works offline. It reads the stored CBOMs from the results volume and needs neither
  Redis nor running workers.
- It uses only runs with status `ok` (failed, timed-out and `unsupported` runs are skipped) and, by default, only
  algorithm assets and repetition 1.
- Every agreement analysis (Content to Agreement: conformance, ladder, matcher, scores; stability, micro) reads the
  `original` variants only. Mutants are normalized too and feed the Detection tables alone, so a mutated repository
  is never counted once per mutant.

### Analysis commands

Run one as `make analysis ARGS="<command> <exp>"`, for example `make analysis ARGS="report stability-pilot"`.

| Command | What it does |
|---|---|
| [`report`](#report) | all of the below in one pass, plus `_ANALYSIS/_SUMMARY.md` (Markdown tables) |
| [`normalize`](#normalize) | every CBOM -> normalized records (`run<i>.norm.json` next to it) |
| [`schema`](#schema) | Content: validity, meaningful shares and traceability (`--fetch` on a laptop) |
| [`levels`](#levels) | how much agreement normalization creates: L0 (raw) to L4 (tool specs) |
| [`micro`](#micro) | scores each tool against the micro projects' ground truth, at two levels (`make analysis ARGS="micro bar-micro"`) |
| [`tables`](#tables-and-the-exact-matcher) | flat CSV tables (runs, records, exact-matcher groups, micro) for pandas/R |
| [`score`](#score) | Agreement: per-tool rates (confirmation, exclusivity, Dawid-Skene, leave-one-out, CIs); stability |

### `report`

`report <exp>` normalizes every CBOM once, writes the CSV tables for the analyses below, then
`_ANALYSIS/_SUMMARY.md`.

- Each report clears and rebuilds `_ANALYSIS/`.
- `experiment export` runs `report` first (skip with `--no-report`), so every zip carries current tables.
- On a terminal both show a live line per step (done/total, remaining time, elapsed time and the run being worked
  on), so a long step reads as working, like a run's board ([Watching a run](#watching-a-run)). Off a terminal each
  step prints one line when it ends.
- The standalone `micro` command also writes `micro-run<i>.json`; `report` writes its micro results as CSV tables
  instead.

**The summary, `_SUMMARY.md`**

- Markdown tables for a first look: runs per tool, the micro inclusion matrix and recall at two levels, who finds
  each micro asset, fill rates and spec issues, the L0-L4 ladder, how many tools report each algorithm per
  repository, the list of files. LLM token counts and costs appear only where an LLM worker ran.
- Its Preamble ends with the repositories the record names, each linked to GitHub at its pinned commit, folded into
  one column per language, or flat with the size when the corpus builder recorded one.
- It is rendered from the CSV files and the record, so it never shows anything they don't hold.
  `python -m analysis.summary <exp>/_ANALYSIS/` rebuilds it from an export.
- Its numbers carry `'` between thousands (13'043); the CSVs keep plain numbers.

**The paper's tables, `_ANALYSIS/latex/tab_*.tex`**

- File names: `candidate_tools`, `decoy_types` and `bar_exam_results` under the paper's own file names, then
  `design_corpus` and the questions by their titles (`content_`, `normalization_`, `agreement_` and `detection_`),
  so a renumbering renames no file.
- They are written from the same files and the registry.
- Each file is the content of a table only, as the paper's `assets/` hold it (booktabs rules, bold column titles,
  the paper's tool badges, sized to the text width of its single-column format), so a file is copied there as it is.
- `python -m analysis.latex <exp>/_ANALYSIS/` rebuilds them.

**The key numbers as macros, `val_<experiment>.tex`**

Written next to the tables: the paper's key numbers as LaTeX macros, named by the first part of the experiment's
name (`\BarPassed`, `\CorpusScans`, `\MutationMutants`).

| Macro | Prints | Use |
|---|---|---|
| `\CorpusScans` | the number through siunitx's `\num`, so the thousands separator is the paper's everywhere | running text |
| `\CorpusScansRaw` | the bare value | arithmetic |
| `\BarPassedWord` | the count spelled out (eleven) | a whole number below 100 |
| `\BarPassedWordCap` | the same with a capital (Eleven) | at the start of a sentence |

- One macro per line, each number a block under a comment naming the table it was read from.
- Few on purpose: the design's counts, the bar exam, the items, the documents, the two ends of the ladder, the
  mutants and the range of F1.

**A noise floor from another experiment, `--baseline`**

`report --baseline <experiment>` reads another, already reported experiment's `stability.csv` (repeated runs on
unchanged code) and joins each tool's noise floor into `mutation-scores.csv` (`baseline_flux`, `baseline_changed`),
so Detection's "Changes off the edit" table can be read against it. A baseline without that file is a clear error
before anything runs.

### `normalize`

`normalize <exp>` reads every stored ok CBOM (all repetitions, including mutants) into normalized records with its
tool's adapter spec, as described in
[Normalization](../analysis/README.md#normalization-from-a-cbom-to-records).

- It writes the normalized files, `normalize.json` and `vocab.csv`.
- `tables` (also part of `report`) writes the per-run coverage, units and conformance tables.
- The diagnostic tables aggregate original variants only, so mutants do not multiply counts.

| File | Content |
|---|---|
| `<snapshot>/<variant>/<tool>/run<i>.norm.json` | next to `run<i>.json`: declared format/version, adapter spec and versions, the original's sha256 (detects a stale file), stats, the records |
| `_ANALYSIS/normalize.json` | the normalizer's diagnostics per tool, over all repetitions of original variants |
| `_ANALYSIS/vocab.csv` | the reviewable mapping table: what every raw value mapped to |
| `_ANALYSIS/coverage-run<i>.csv` | fill rates per tool and dimension |
| `_ANALYSIS/units-run<i>.csv`, `units-repo-run<i>.csv` | how a tool packages its findings: assets, records, items |
| `_ANALYSIS/sources-run<i>.csv` | where each value came from |
| `_ANALYSIS/registry-run<i>.csv` | the delta to the CycloneDX registry |
| `_ANALYSIS/conformance-run<i>.csv` | spec issues per tool and kind |

Their columns: [Table reference](#table-reference).

### `schema`

`schema <exp>` answers Content's conformance part (`analysis/schema.py`). `report` runs all of it too
(`report --fetch`).

| File | Content |
|---|---|
| `schema-run<i>.csv` | whole-document validity, one row per CBOM: valid, error count, error kinds `<keyword>:<path>` |
| `meaningful-run<i>.csv` | per tool and CycloneDX asset type, the share of assets carrying a value in that type's own fields |
| `traceability-run<i>.csv` | per (snapshot, tool, asset type), how many records are located and hold at the pinned commit |

**Validity**

- Every CBOM that declares a CycloneDX version with a vendored schema (1.6, 1.7) is validated as a whole document
  with `jsonschema` (offline, `referencing` registry).
- Tools whose CBOM our worker assembles (`cbom_by: bf-cbom` in `common/tools.yaml`, the LLM workers) are reported
  apart.

**Meaningful shares**

- The fields per asset type are `analysis/schema.py:MEANINGFUL_FIELDS`:

  | Asset type | Its own fields |
  |---|---|
  | algorithm | primitive, parameter (parameter set or curve), mode, padding, functions, OID, security levels |
  | key | type, size, algorithm, format |
  | certificate | subject, issuer, validity, signature algorithm, public key, format |
  | protocol | type, version, cipher suites |

- Algorithm assets have no dedicated key-size field.
- The share is given for the standard fields and, where the normalizer reads such a value, for any source. Any
  source includes the standard fields, so it is never lower.

**Traceability**

- `located_file`: records that name a file. `located_line`: records that name a file and a line.
- Weak: of those with a line, how many exist at the pinned commit with the line inside the file.
- Strong: how many also have a vocabulary token within ±2 lines.
- `related-crypto-material` (keys, secrets) is kept apart from algorithms.
- In `_SUMMARY.md` every asset type has its own table under `#### Asset type`: CycloneDX's four in the standard's
  order (algorithm, certificate, protocol, related-crypto-material), then `unknown` for values outside them.

**The snapshot's files.** The traceability check needs them.

- On the VM it reads the git mirror.
- On a laptop with a copied export, `--fetch` pulls each pinned commit once into `SNAPSHOT_CACHE_DIR` (default
  `~/.cache/bf-cbom/snapshots`, one shallow `git fetch` per repo).
- A fixture (`path:`, the micro projects) needs no `--fetch`: the report publishes it from its local directory into
  the cache, when that directory still gives the recorded commit.

### `levels`

`levels <exp>` computes the normalization ladder (L0-L4, see
[the ladder](../analysis/README.md#normalization-from-a-cbom-to-records)): per repository the items each tool
reports (`analysis/README.md` "Units"), and the share that another tool also reports. Tools that share their
detection logic count as one (`voter_group` in `common/tools.yaml`). It prints a tools x levels table and writes two
files.

| File | Content |
|---|---|
| `_ANALYSIS/levels-run<i>.csv` | the confirmed share per tool and level |
| `_ANALYSIS/items-run<i>.csv` | one row per (snapshot, variant, tool, item): the basis for UpSet plots and the tools-per-item distribution. It is location-free, so every tool takes part |

| Option | Effect |
|---|---|
| `--run <i>` | another repetition |
| `--merge-llm` | the LLM workers count as one tool |
| `--include-tailored` | keeps the pairs a tool was tuned on |
| `--closure` | adds the algorithms a record implies |

### `score`

`score <exp>` computes the per-tool rates without ground truth (Agreement, `analysis/score.py`; no ranking claim) on
the exact matcher's groups, per tool, dimension (algorithm, key size, mode, padding, curve, digest, location) and
level.

**Independent tools.** Tools that share their detection logic count as one, since they cannot confirm each other
(`voter_group` in `common/tools.yaml`): the two CBOMkit packagings count once, and using the same pattern engine
alone does not merge tools. `--merge-llm` merges the LLM workers.

**What an item is.** An L4 identity, a (identity, value) pair or a (identity, file:line) pair, read off the
matcher's groups so that a group is never counted twice.

| Measure | Meaning |
|---|---|
| confirmation rate | the share of the tool's items another tool reports too: the same algorithm per repository, the same parameter value, a shared call site |
| `rate` | the point estimate: the mean of the per-repository rates, with a bootstrap CI of that mean over repositories (`--samples`, default 200) |
| `rate_pooled` | the share over all items |
| exclusivity | the share no other tool reports at all |
| rank | only for tools with items in at least 3 repositories and 10 items in total; others show their rate without a rank or CI |
| Dawid-Skene | on the algorithm dimension, a weighted support: an EM without labels estimates every tool's sensitivity and specificity, and a tool's support is the mean posterior of its items |

- **Leave one out.** `loo-run<i>.csv` recomputes the ranking without each tool, with Kendall's tau against the full
  ranking.
- **Merged tools.** `grouping-run<i>.csv` ranks the tools under the declared grouping and again under each other
  one that groups the tools that ran differently: every tool on its own (`per-tool`), the LLM workers as one
  (`llm-merged`), the tools of one pattern engine family as one (`engine-merged`). A grouping that changes nothing
  for the experiment is not written.
- **Tailored pairs.** Pairs a tool was tuned on are excluded (`--include-tailored` keeps them).
- **Stability.** `stability.csv` covers every (tool, repository) with repeated runs: Jaccard of the algorithm sets
  between runs, the record-count CV and the consensus set (algorithms in more than half of the runs). With
  repetitions the scores use that consensus set (`--no-consensus` scores run i alone). An experiment without
  repeated runs has no such file.
- **LLM costs** come from the stored token usage and the dated price table `analysis/data/llm_prices.yaml`
  (`cost_usd`, `priced_model`, `price_period`, `price_table_version` in `runs.csv`). The summary adds cost per
  repository, per item and per confirmed item.

### `micro`

`micro <exp>` scores every tool on the micro projects (`experiments/corpus/micro`, repos named
`<owner>/micro-<language>`) against `experiments/corpus/micro/ground_truth.yaml`.

- **Expected items.** 14 per project, each a catalog snippet in a function of its own (the file calls them
  `assets`).
- **Decoys.** 8 per project: code that names or resembles cryptography without using it. A comment and a log
  message naming algorithms, a CRC32 checksum, a Base64 encoding, a variable named after an algorithm, a hex
  constant that looks like a key, a URL ending in `.sha256`, a path naming a certificate file. What each one tests:
  `corpus/README.md`.
- **Generated, never edited by hand.** Projects and ground truth come from the snippet catalog by
  `uv run python -m mutation.micro`. A test fails when they drift, and the ground truth's header states the rules.
- **Two levels.** Scored from the normalized records (`analysis/groundtruth.py:score_records`) at call site and
  repository, side by side and never pooled.
- **Windows.** A location window (CryptobomForge's IBM-draft `detectionContext`, the finding ±2 lines) counts at
  every line it spans.

It prints one row per project and tool, including tools that did not run there (unsupported, failed):

| Column | Meaning |
|---|---|
| strict | call site: expected items with a record of their group on one of their lines (the body of their function), out of 14; for a key generation a key record of the group counts too |
| lenient | call site: also a record of unknown family there |
| repo | repository level: expected items whose group the tool reports anywhere in the project, location ignored; items sharing a group are found together. Needs a known family, so it can be lower than lenient |
| precision | correct locations out of all the tool reported in the micro source file; one is correct on an expected item's line or a line the ground truth marks as related or accepted (a sub-use such as a digest, an import of the item's API) |
| decoys | the decoys with a crypto record on their lines: false positives with ground truth |
| F1 | harmonic mean of the lenient recall (over the expected calls) and the precision (over the reported places); 0 when no call was found |
| bar | `pass` when the tool found at least half of the calls, lenient (the bar, 7 of 14) |
| missed | ids of the expected items the tool did not find (lenient) |

After the rows the command lists each tool's verdict over all its projects:

| Verdict | Meaning |
|---|---|
| `bar passed` | the bar cleared in at least one language |
| `bar failed` | scored and below the bar wherever scored; the candidates to leave out of the further experiments |
| `not judged` | no scored run |

The same verdict is the `bar_verdict` column of `micro-run<i>.csv` and the `bar exam` column of the summary's "Bar
exam" table.

### `tables` and the exact matcher

`tables <exp>` writes flat, long-format CSV files (UTF-8, comma-separated, lists inside a cell joined with `;`) and
runs the **exact matcher** (`analysis/matchers/exact.py`, matcher (b)) at every ladder level.

**How the exact matcher groups**

- Its groups are the clusters of the ladder, one per key at the level: the records whose identity at that level or
  below is the key, plus records attached by vocabulary knowledge (a generic report such as SHA-2 next to SHA-256,
  or with `--closure` a record implying the algorithm). So tools without locations take part.
- Clusters never merge through a record with several keys.
- A group whose members span several L4 identities (`units`, `n_units`) shows what the level declares the same.
- Records without an L4 identity (`resolution` undefined, api, non-crypto, ambiguous) are not matched.
- A member whose own identity differs from the group's key joined through a lower level (`matched`).
- `--closure` lets a record join the groups of the algorithms it implies (`implied` member rows).

**Call sites.** Inside a group the located records form call-site groups: same file, lines within `--window`
(default 2; a window record spans `line` .. `line_end`). `tables` and `report` write the repository and the
call-site groups (`scope: repo` / `site`) into the same `groups-exact-run<i>.csv`.

**Agreement per dimension.** Per group and dimension (key size, mode, padding, curve, digest, location) the
agreement table says how many tools report a value admitted at the level and how many carry the majority value.

**Levels.** The full group, member and agreement tables are written at the scoring level (`--level`, default L4),
the per-level summary `matchlevels-run<i>.csv` for all five levels (L0-L4).

**Settings** are recorded in `tables-run<i>.json`.

The tables come in three layers (runs, records, matches), so further matchers only add files and never change
these. Every file and its columns: [Table reference](#table-reference).

### RaQuN (opt-in)

RaQuN (matcher (c), `analysis/matchers/raqun.py`) is not part of the paper's analysis. It runs next to the exact
matcher as a cross-check.

- **How to run it.** `report --raqun`, also `tables --raqun` and `experiment export --raqun`, so
  `make experiment-export NAME=<exp> ARGS=--raqun`.
- **What it needs.** The `raqun` extra (PyQuN from our fork: in the runner image, on the host after
  `uv sync --extra raqun`). The command fails before anything runs when it is missing.
- **What it adds.** A plain report holds nothing of RaQuN. With `--raqun`, everything it adds carries `raqun` in
  its name and nothing else changes: the files below, the LaTeX tables `tab_agreement_k_raqun.tex` and
  `tab_agreement_dimensions_raqun.tex`, and the summary's last section, "RaQuN cross-check" (both matchers per
  level, "Matching time", the two groupings compared).

**How it matches**

- Tools are models, and a tool's distinct canonical assets are elements, built from the exact matcher's records
  (mapped, with an L4 identity).
- Elements carry `<dimension>=<value>` attributes: the identity at the level and the standard's dimensions, not our
  `group`. RaQuN's Jaccard on them is the per-field agreement.
- Only `algorithm=<identity at the level>` varies across levels. `family`, `primitive`, `mode`, `padding`, `curve`,
  `key_size` and `digest` use their full normalized values from L0 onward, with missing values omitted (controlled
  comparison, 2026-10-23).
- Each level is matched independently; groups can rearrange and their count need not decrease.
- The parameter-agreement columns still use the ladder's source-admission rules; these describe the resulting
  groups, not the attributes fed into RaQuN.

**Its files**

| File | Content |
|---|---|
| `groups-raqun`, `members-raqun`, `agreement-raqun-run<i>.csv` | the same columns as the exact tables (at the scoring level) |
| `matchers-raqun-run<i>.csv` | the two partitions compared per repository and level: co-membership pair Jaccard, the share of groups that sit inside one group of the other matcher |
| `matchlevels-raqun-run<i>.csv` | RaQuN's rows level by level, with the columns of the exact matcher's `matchlevels-run<i>.csv` |
| `raqun-L0-run<i>.csv` through `raqun-L4-run<i>.csv` | for visual inspection: one match per row and one column per tool |

**Matching time.** Each matcher's wall time is recorded next to its results: per repository and level in
`matchers-raqun-run<i>.csv` (`exact_seconds`, `raqun_seconds`), summed per level in the `seconds` column of the two
matchlevels files, and as the summary's "Matching time" table (RaQuN's n-way matching next to the exact matcher's
clustering). It is measured around the `match` call alone, PyQuN's imports and the vocabulary load excluded, so the
process pool does not change the sums.

**Reading the `raqun-L<n>` files**

- A cell contains the distinct reported names, the exact attributes fed into matching at that level, and the number
  of underlying records, e.g. `["AES-256-GCM"] <algorithm=AES; family=AES; key_size=256; mode=GCM> [3 records]`.
- An empty tool cell means that tool has no element in the match.
- Singleton matches are included; filter `n_tools >= 2` for cross-tool matches.
- Repository, snapshot, variant, run, level, group ID and similarity identify the match. Group IDs are local to
  that snapshot, variant, run and level, not stable across levels.

### Analyzing a copied export on the host

The analysis only reads files, so an export unzipped or copied into `experiments/results/` can be analyzed without
Docker. The VM runs the experiments; the laptop re-runs the analysis with other options.

1. Install the analysis dependencies with `uv sync` (`--extra raqun` for a report with `--raqun`).
2. Run `RESULTS_DIR=experiments/results uv run bf-cbom analysis report <exp> --fetch`.

### In parallel

`report` (and `tables`, `normalize`, `schema`, `levels`, `score`, `micro`) normalizes, validates and matches in a
process pool, one CBOM or one repository per task.

| Setting | Meaning |
|---|---|
| `--jobs N` (env `BF_CBOM_JOBS`; `experiment export` takes it too) | the number of pool workers |
| `0`, the default | auto: `min(CPUs, 8)` workers for a step with 16 or more items, else sequential |
| `1` | sequential |
| `RUNNER_MEMORY` (Makefile, default 8g) | the runner's memory cap, which bounds the pool |

- **The results do not depend on it.** Every table, the summary and the normalized CBOMs are byte-identical to
  `--jobs 1` (the matchers' timing columns aside).
- **Both are Makefile variables** (`?=`), so exporting them in the VM's shell profile makes them the default there.
  On the VM (64 CPUs and 122 GiB since 2026-10-29), with no experiment running:
  `make experiment-export-all BF_CBOM_JOBS=32 RUNNER_MEMORY=96g`.
- **A worker the cap kills ends the report**, so lower the jobs or raise the cap.
- **Beyond some 16 workers the matching step gains little**: it has one task per repository, and the largest
  repository bounds it.

### Output files

The commands write to the experiment's folder on the volume. `run1` is repetition 1.

```
<exp>/_ANALYSIS/
├── _SUMMARY.md                  # report: Markdown tables of everything below, for reading
├── latex/                       # report: the paper's tables (tab_*.tex) and key numbers (val_<experiment>.tex)
├── tables-run1.json             # tables: settings used, row counts, the analysis code's commit, versions, per tool the group it counts in and its engine
│
├── runs.csv                     # tables: one row per stored run (all repetitions)
├── runtime.csv                  # tables: per tool and language, how long its scans take (Content)
├── tools.csv                    # tables: per tool that ran, vendor, project page, how it is shipped, engine and approach, version and its page
│
├── records-run1.csv             # tables: one row per normalized record
├── normalize.json               # normalize: diagnostics per tool over every run (sources, spec issues, conflicts)
├── vocab.csv                    # normalize: what every raw value mapped to, per tool and source
├── coverage-run1.csv            # tables: fill rates per tool and dimension, and what the assets' names are
├── units-run1.csv               # tables: per tool assets, records, items and how they relate (packaging)
├── units-repo-run1.csv          # tables: per (tool, repo) assets, records, items
├── sources-run1.csv             # tables: per tool and dimension, where the value came from
├── registry-run1.csv            # tables: the delta to the CycloneDX registry (Normalization)
│
├── schema-run1.csv              # schema (Content): one row per CBOM: declared version, valid, errors, error kinds
├── conformance-run1.csv         # tables: spec issues per tool and kind
├── meaningful-run1.csv          # schema (Content): per tool and dimension, assets with a value from standard fields / any
├── traceability-run1.csv        # schema (Content): per (snapshot, tool): located, existing, in range, strong
│
├── levels-run1.csv              # levels: confirmed share per tool at normalization levels L0-L4
├── items-run1.csv               # levels: which items each tool reports per repository, with the tools matching at each level
├── groups-exact-run1.csv        # tables: exact matcher, one row per group (repo scope per algorithm, site scope per call site)
├── members-exact-run1.csv       # tables: one row per (group, record)
├── agreement-exact-run1.csv     # tables: one row per (group, dimension): tools reporting / agreeing, majority value
├── matchlevels-run1.csv         # tables: the exact matcher per level: items by number of tools, agreement per dimension
├── score-run1.csv               # score (Agreement): per tool, dimension, level: items, confirmation [CI], exclusivity, rank, Dawid-Skene
├── loo-run1.csv                 # score (Agreement): ranking without each tool, Kendall tau vs the full ranking
├── grouping-run1.csv            # score: the ranking under the declared grouping of the tools and each that differs, Kendall tau
├── stability.csv                # score, only with repeated runs: per (tool, snapshot) Jaccard across runs, records CV, consensus
│
├── micro-run1.csv               # tables: one row per (micro project, tool); only with micro projects
├── micro-items-run1.csv         # tables: one row per (micro project, tool, expected item)
├── micro-run1.json              # micro (the standalone command): per (project, tool), status, verdict, recalls, precision, F1, hits, misses, false sites and decoys
│
├── mutants.csv                  # tables: one row per mutant; only with a mutation: block
├── mutation.csv                 # tables: one row per (mutant, tool)
├── mutation-rates.csv           # tables: detection rates per tool, language, style and dimension
├── mutation-scores.csv          # tables: recall, precision and F1 per tool, language and style
├── bridge-run1.csv              # tables: Detection against Agreement
│
├── groups-raqun-run1.csv        # tables, only with --raqun: RaQuN's groups (with members-/agreement-raqun-run1.csv)
├── matchlevels-raqun-run1.csv   # tables, only with --raqun: RaQuN per level, the columns of matchlevels-run1.csv
└── matchers-raqun-run1.csv      # tables, only with --raqun: exact vs RaQuN per repository (co-membership pair Jaccard)
```

### Table reference

Every table is a flat CSV file. The overview says what one row is; the columns are listed below it, one block per
file.

| File | Layer | One row per |
|---|---|---|
| `runs.csv` | runs | stored run, every repetition, incl. failed, timed-out and `unsupported` |
| `runtime.csv` | runs | tool and language, plus `all` |
| `tools.csv` | `common/tools.yaml` | tool (those that ran) |
| `records-run<i>.csv` | records | normalized record: one (component, occurrence) of one tool |
| `vocab.csv` | per tool, from the records | (tool, source, raw value, asset type of the record) |
| `coverage-run<i>.csv` | per tool, from the records | (tool, dimension) |
| `units-run<i>.csv` | per tool, from the records | tool |
| `units-repo-run<i>.csv` | per tool, from the records | (tool, repository) |
| `sources-run<i>.csv` | per tool, from the records | (tool, dimension, kind of source) |
| `registry-run<i>.csv` | per tool, from the records | addition of `algorithms.yaml`, unit under no family, table of the identifier layer |
| `conformance-run<i>.csv` | per tool, from the records | (tool, scope, issue kind) |
| `schema-run<i>.csv` | Content, see [`schema`](#schema) | CBOM |
| `meaningful-run<i>.csv` | Content, see [`schema`](#schema) | (tool, asset type, field) |
| `traceability-run<i>.csv` | Content, see [`schema`](#schema) | (snapshot, tool, asset type) |
| `levels-run<i>.csv` | the ladder | (dimension, level, tool) |
| `items-run<i>.csv` | the ladder | (snapshot, variant, tool, item) |
| `groups-exact-run<i>.csv` | matches | group of the exact matcher |
| `members-exact-run<i>.csv` | matches | (group, record) pair |
| `agreement-exact-run<i>.csv` | matches | (group, dimension) |
| `matchlevels-run<i>.csv`, with `--raqun` also `matchlevels-raqun-run<i>.csv` | matches | (matcher, level): the exact matcher's rows in the first file, RaQuN's in the second |
| `groups-raqun-run<i>.csv`, `members-raqun-run<i>.csv`, `agreement-raqun-run<i>.csv` | matches, only with `--raqun` | RaQuN's groups |
| `matchers-raqun-run<i>.csv` | matches, only with `--raqun` | (repository, level): exact vs RaQuN |
| `score-run<i>.csv` | scores | (dimension, tool) at the matcher's level |
| `loo-run<i>.csv` | scores | (dimension, left-out tool) |
| `grouping-run<i>.csv` | scores | (dimension, grouping of the tools) |
| `stability.csv` | scores, only with repeated runs | (tool, snapshot, variant) with >= 2 runs |
| `micro-run<i>.csv` | ground truth | micro project and tool of the spec, also unsupported and failed runs |
| `micro-items-run<i>.csv` | ground truth | (micro project, tool, expected item) of every scored run |
| `mutants.csv` | Detection | mutant (`mutants.json`) |
| `mutation.csv` | Detection | (mutant, tool) |
| `mutation-rates.csv` | Detection | (tool, language, style or `all`, dimension) |
| `mutation-scores.csv` | Detection | (tool, language, style or `all`) |
| `bridge-run<i>.csv` | Detection against Agreement | (language, tool) cells and per language `all` |

The tables of run i (`-run<i>`) read the original variants of that repetition, unless noted.

#### Runs

<details>
<summary><code>runs.csv</code></summary>

- **The run:** repo, snapshot, commit, variant, variant_commit, language, tool, run.
- **The repository's size:** repo_files, repo_source_files, repo_bytes and repo_source_bytes. The snapshot's files
  and bytes, all and those of its language, at the pinned commit: from the record, else the two counts from the
  snapshot files. Empty only for an older record on a copied export without `--fetch`.
- **tailored:** the tool was tuned on this repo.
- **status:** `ok`, `error`, `timeout`, `unsupported`, `oom` (stored ok but the container was OOM-killed meanwhile,
  so the analysis reads no CBOM from it).
- **How it went:** duration_sec, size_bytes, error (the most telling line).
- **Memory:** peak_memory_mb, memory_limit_mb, limit_hits, oom_kills.
- **memory_failure:** for a run that failed (error, timeout or oom), whether it may have failed for memory and why:
  `oom kill`, `out of memory` (its error says so: Java's OutOfMemoryError, CBOMkit's backend_oom, a runtime's "out
  of memory"), `at limit` (it reached the container's limit); else empty.
- **CPU:** cpu_seconds, cpu_cores.
- **Times:** enqueued_at, started_at, finished_at, queue_sec, received_at.
- **Provenance:** tool_version, image_ref, bfcbom_commit.
- **The CBOM:** bom_format, spec_version (as the CBOM declares), components, crypto_assets and, by the declared
  `assetType`, algorithm_assets, certificate_assets, protocol_assets, material_assets, other_assets, plus
  located_file_assets and located_line_assets (crypto assets with a file, and with a file and a line, in the CBOM).
- **expect_crypto:** from the spec.
- **workaround:** recorded setup workarounds, such as CipherRadar `.cradarignore`.
- **tool_warning:** the tool's own words when it limited its analysis, such as xgrep skipping its cross-file
  analysis above its graph budget. The run is ok; the summary lists these runs under "Runs".
- **For LLM workers:** requests, requests_failed, chunks_total, chunks_sent, chunks_skipped (over `LLM_MAX_CHUNKS`,
  never sent), input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, reasoning_tokens,
  models_answered, cost_usd, priced_model, price_period, price_table_version.

</details>

<details>
<summary><code>runtime.csv</code></summary>

A repository's time is the median of its ok runs, all variants and repetitions, so it counts once. Unsupported runs
are out.

- **Counts:** repos, runs, timeouts, timeout_share.
- **Time:** median_sec with median_ci_low / median_ci_high (bootstrap over repositories, 1'000 resamples, from 3
  repositories), p90_sec, max_sec (slowest single ok run), spread_cv (CV of the repositories' times), hours (every
  run that ran).
- **Between reruns:** rerun_cv and rerun_pairs (median CV between repeated runs of one snapshot).
- **Scaling:** sec_per_1k_source_files and size_rho (Spearman between time and source files), both only with 3 or
  more repositories whose sizes differ.
- **Memory:** peak_median_mb with peak_ci_low / peak_ci_high (bootstrap, as for time), peak_p90_mb and
  peak_spread_cv over the repositories (a repository's peak is the largest working set of its runs), peak_max_mb.
- **CPU:** cpu_sec_median and cores_median, each with _ci_low / _ci_high, _p90 and _max. Over the ok runs, a
  repository's value is the median `cpu_seconds` or `cpu_cores` of its runs: the CPU time summed over the cores, and
  that over the wall time. Empty where the workers did not record them.
- **For checking the limits:** limit_mb, at_limit (runs with limit_hits > 0; empty where the workers did not record
  it), oom (runs with an OOM kill), memory_failures (runs with a `memory_failure`).

The summary shows time, memory and CPU in Content's "Run time, memory and CPU" and the limits apart, in the
preamble's "Memory limits", with every run that may have failed for memory listed.

</details>

<details>
<summary><code>tools.csv</code></summary>

- vendor, url.
- **shipped:** CLI, server with an API, GitHub Action, HTTP API.
- **approach:** a third-party engine named first, else `own engine`, then how it detects. The summary's "Tools".
- **version:** the version the paper reports, the tool's own: its release, or 7 characters of the pinned commit
  without one.
- **version_url:** the page that shows it: the release, else the tag, the commit or the package version; the
  paper's citation. Linked in the summary's "Tool versions", which also checks every run recorded it.

</details>

#### Records

<details>
<summary><code>records-run&lt;i&gt;.csv</code></summary>

- snapshot, variant, tool, run.
- **location_kind:** `line`, `window`, `import`, `file`, `none`, `unread`.
- **record_id:** `<tool>:<component>:<occurrence>`.
- **site_id:** `<tool>:<file>:<line>` when located.
- every record dimension, with `raw_*` and `src_*`.
- **resolution:** `mapped`, `ambiguous`, `api`, `non-crypto`, `undefined`; only `mapped` records become items.
- unmapped, conflicts, spec_issues.

</details>

<details>
<summary><code>normalize.json</code> and <code>vocab.csv</code></summary>

`normalize.json`, per tool over all repetitions of original variants (the normalizer's diagnostics):

- formats declared, assets per asset type;
- where values came from (standard field, property, occurrence, name, API context);
- spec issues, conflicts, top unmapped values.

`vocab.csv`, the reviewable mapping table:

- **One row per** (tool, source, raw value, asset type of the record). The asset type is empty when the record has
  none, so a name read off a certificate file stands apart from the same name on an algorithm.
- **status:** `mapped`, `partial`, `unmapped`, `ambiguous`, `known`, or the entry's `kind`: `api`, `non-crypto`.
- what it mapped to, leftover tokens, via, interpretive, kind, count.

</details>

<details>
<summary><code>coverage-run&lt;i&gt;.csv</code></summary>

The same CBOMs as `units-run<i>.csv`.

- Algorithm assets (CBOM components) with a value the tool reported, against only one we derived, as counts and
  shares of the assets (fill-rate heatmaps).
- Every row also carries the tool's `assets` and the assets per resolution (`mapped`, `ambiguous`, `api`,
  `non-crypto`, `undefined`).

</details>

<details>
<summary><code>units-run&lt;i&gt;.csv</code> and <code>units-repo-run&lt;i&gt;.csv</code></summary>

`units-run<i>.csv`: how a tool packages its findings.

- **`packaging`:** the hand label of `common/tools.yaml`: per hit, per rule, per algorithm, per family, no places,
  unknown (`analysis/README.md` "Packaging per tool").
- **`assets`:** algorithm components. The summary's column reads `algorithm assets`, since certificates, protocols
  and key material never become items.
- **`records`:** their occurrences.
- **`items`:** distinct L4 identities of its mapped records per repository, summed.
- records_per_asset_median / _max.
- **multi_occurrence_share:** assets at more than one place.
- **assets_per_item:** above 1 means one component per finding.
- **unlocated_share:** assets without a file.
- **loc_line / loc_window / loc_import / loc_file / loc_none / loc_unread:** algorithm records by the kind of place
  they name (`analysis/README.md` "Units").
- **outside_code:** algorithm records whose file is not a source file of the repository's language: fixtures, test
  vectors, configuration, documentation, other languages. The summary's "Places per tool" shows it as a share of the
  located records.

`units-repo-run<i>.csv`: `assets`, `records` and `items` of one CBOM as in `units-run<i>.csv` (whose counts are
these rows summed), with `language`. The summary shows them as a repository x tool matrix.

</details>

<details>
<summary><code>sources-run&lt;i&gt;.csv</code></summary>

The algorithm assets whose value of that dimension came from a standard `field`, a tool `property`, the parsed
`name`, an `occurrence`, an API identifier (`context`), a `ref`, a `dependency` or was `derived`, with the share of
the tool's algorithm assets. Content's conformance part, the summary's "Where the information lives".

</details>

<details>
<summary><code>registry-run&lt;i&gt;.csv</code></summary>

The delta to the CycloneDX registry (Normalization). Three kinds of rows:

- **`kind: addition`:** one per addition of `algorithms.yaml`: name, `gap` (missing-family, scheme-less,
  generic-marker, non-crypto), primitive, `count` (items resting on it), repos, tools, the reason.
- **`kind: unknown`:** one per unit under no family.
- **`kind: layer`:** one per table of the identifier layer, with its size.

</details>

<details>
<summary><code>conformance-run&lt;i&gt;.csv</code></summary>

- **`asset` scope:** counts assets with a value read from standard fields that break the declared version's schema
  (field, enum, type, deprecated).
- **`document` scope:** counts CBOMs (dialect, version).
- Tools without issues have no row.

</details>

#### Matches and scores

<details>
<summary><code>levels-run&lt;i&gt;.csv</code> and <code>items-run&lt;i&gt;.csv</code></summary>

`levels-run<i>.csv`:

- dimension, level, tool, voter_group, items, confirmed, share.
- **voter_group:** the tool it counts as. Tools that share their detection logic count as one (`voter_group` in
  `common/tools.yaml`), and every other tool is its own. Every column named after it counts tools that way.
- **confirmed_strict:** confirmed without the attached generic reports.
- **multi_unit_clusters:** clusters whose members span several L4 identities.
- Tool `all` pools every tool.
- `dimension` is `algorithm` or a parameter: key_size, mode, padding, curve, digest, which exist from L2 on.

`items-run<i>.csv`:

- **`unit`:** the item's L4 identity.
- the tool's `voter_group`, and the repo's language.
- **`tailored`:** the tool was tuned on the repo.
- **`implied`:** the row is only implied (with `--closure`).
- **`registry`:** its family is a CycloneDX registry entry rather than an addition of `algorithms.yaml`
  (Normalization).
- the number of records and distinct spellings behind it.
- **`k_L0` .. `k_L4`:** per level, the number of tools in the item's largest cluster.

</details>

<details>
<summary><code>groups-exact-run&lt;i&gt;.csv</code></summary>

`repo` scope: one cluster per key at the level (its members plus attached records). `site` scope: its located
records at one call site.

- matcher, repo, snapshot, commit, variant, run, language, level, scope.
- **group_id:** `E00001`, clusters sorted by key; sites `E00001.001`.
- **n_tools, tools:** the tools. k counts those that share their detection logic once: `voter_groups`.
- **key:** the level's key: a raw name at L0, an identity at L4.
- voters, voter_groups.
- **member_voter_groups:** those of them with a record of their own in the group, attached records aside. 0 = the
  group is no item, the rule of `matchlevels-run<i>.csv` and the summary's "Per language".
- n_records, n_sites, n_attached.
- **units, n_units:** L4 identities of the members.
- **Repo scope:** located_voters, colocated_voters.
- **Site scope:** file, line_min, line_max, parent.

</details>

<details>
<summary><code>members-exact-run&lt;i&gt;.csv</code></summary>

- matcher, snapshot, variant, run, group_id, record_id, tool.
- **unit:** the record's L4 identity.
- **implied:** attached through `--closure`.
- **matched:** its own identity differs from the group's key: joined through a lower level.
- **attached:** a generic or implying record, no key of its own in the group.

</details>

<details>
<summary><code>agreement-exact-run&lt;i&gt;.csv</code></summary>

- matcher, repo, snapshot, variant, run, group_id, scope, key.
- **dimension:** key_size, mode, padding, curve, digest; location for repo groups.
- **reporting:** tools with a value.
- **agreeing:** tools carrying the majority value.
- majority, values.

</details>

<details>
<summary><code>matchlevels-run&lt;i&gt;.csv</code></summary>

- matcher, level, run, repos, records.
- **items:** algorithms by L4 identity.
- **k1, k2, k3, k4plus:** items by the number of tools in their largest group at the level.
- share_k2plus.
- **`<dim>_groups` and `<dim>_agree`** for key_size, mode, padding, curve, digest: groups with 2+ tools reporting a
  value admitted at the level, and the share where all agree.
- **location_groups, location_agree:** groups with 2+ tools that give a location, and the share with 2+ at one call
  site.

</details>

<details>
<summary>The RaQuN tables (only with <code>--raqun</code>)</summary>

- `groups-raqun-run<i>.csv`, `members-raqun-run<i>.csv`, `agreement-raqun-run<i>.csv`: as the exact tables, repo
  scope only, with `n_elements` and `similarity` (Jaccard of the attribute sets).
- `matchers-raqun-run<i>.csv`: repo, snapshot, variant, run, level, matcher_a, matcher_b, records_common, groups_a,
  groups_b, pairs_a, pairs_b, pairs_both, pair_jaccard, a_in_one_b, b_in_one_a.

</details>

<details>
<summary><code>score-run&lt;i&gt;.csv</code>, <code>loo-run&lt;i&gt;.csv</code>, <code>grouping-run&lt;i&gt;.csv</code></summary>

`score-run<i>.csv`:

- dimension, level, tool, voter_group, items, confirmed.
- **repos:** those with items.
- **rate:** the mean of the per-repository confirmation rates. rate_pooled.
- **ci_low, ci_high:** bootstrap of the mean over repositories; only with enough support.
- exclusive, exclusivity.
- **majority_items, majority_covered, coverage:** of the repository's majority items (those at least half of the
  tools that ran there report), the share the tool reports, mean over repositories: a recall proxy.
- **rank:** only with items in ≥ 3 repos and ≥ 10 items.
- **ds_support, ds_sensitivity, ds_specificity:** algorithm dimension only.

`loo-run<i>.csv`: dimension, level, left_out, kendall_tau, ranking.

`grouping-run<i>.csv`: how far the ranking depends on which tools count as one (the evidence for the declared
grouping is in `analysis/README.md`).

- dimension, level, voter_groups, ranked, ranking.
- **grouping:** `declared`, then each of `per-tool` (every tool on its own), `llm-merged` (the LLM workers as one)
  and `engine-merged` (the tools of one pattern engine family as one) that groups the tools that ran differently
  from `declared`. A grouping that changes nothing for the experiment is not written.
- **kendall_tau:** against `declared`.

</details>

<details>
<summary><code>stability.csv</code></summary>

Written only when a tool ran more than once on a snapshot, so an experiment without repeated runs has no such file.

- repo, snapshot, variant, tool, level, runs, records_mean, records_cv, items_mean, jaccard_mean, jaccard_min,
  consensus, unstable.
- **flux:** the share of the tool's items on that snapshot not in every run: the noise floor of Detection's changes
  off the edit.

</details>

#### Ground truth

<details>
<summary><code>micro-run&lt;i&gt;.csv</code> and <code>micro-items-run&lt;i&gt;.csv</code></summary>

`micro-run<i>.csv`:

- project, language, tool, run, tailored.
- **status:** of the run, `missing` without one.
- **verdict:** `pass`, `fail`, `unsupported`, `error`, `missing`.
- n_items, strict, lenient, repo, recall_strict, recall_lenient, recall_repo.
- sites_in_file, sites_correct, precision, f1.
- bar_items, passes_bar.
- **bar_verdict:** `bar passed`, `bar failed`, `not judged`, over all scored projects of the tool.
- missed, repo_missed.
- **repo_extra:** families reported that the project does not contain.
- **decoys:** count. **decoys_hit:** ids.

`micro-items-run<i>.csv`:

- project, language, tool, run, item, families.
- **insertion_style:** `direct`, `indirect`.
- callsite_strict, callsite_lenient, repo.

</details>

#### Detection

<details>
<summary><code>mutants.csv</code></summary>

- repo, snapshot, commit, language, variant_commit, snippet, style, catalog_version, family, expected, also, file.
- **variant:** the mutant id.
- **kind:** `call` or `decoy`.
- **unit:** the expected L4 identity, `protocol:TLS` for the TLS context.
- call_line, related_lines.
- **spans:** `kind:start-end`.
- host, host_line, host_callers, host_entry, imports_added.
- **qualified:** Java types written fully qualified.
- patch.

</details>

<details>
<summary><code>mutation.csv</code></summary>

Only new records count: an original finding moved by the insertion is not new. With repetitions a value holds in
most of the mutant's runs.

- repo, snapshot, language, variant, snippet, style, tool, voter_group, runs_mutant, runs_original, unit.
- **kind:** `call` or `decoy`.
- **status:** `ok`, or why it is not judged: the mutant's or the original's run missing or not ok.
- **masked:** the original already has the unit: no repository-level value.
- found_repo, found_file.
- **found_lines:** on one of the inserted lines, imports excluded, no window: the bar exam's rule, Detection's
  headline.
- **found_site:** call line ±2: stricter.
- **found_group:** lenient: same group on the inserted lines.
- **false_positive:** for a decoy, a new record on its inserted lines.
- **key_size, mode, padding, curve, digest, iterations, protocol_version:** the expected value on a new record of
  the unit on the inserted lines; empty = not judged.
- **extra_units, lost_units, extra_sites, lost_sites:** other changes outside the edit, its import lines included
  and widened by ±2. A new unit without a location counts; majority sets.
- **new_records:** per run, records absent from the original at the same place, the original's lines shifted into
  the mutant. A record is matched by its unit, or by its raw name when it has none, so a tool's identity-less import
  markers are not new on every mutant.

</details>

<details>
<summary><code>mutation-rates.csv</code> and <code>mutation-scores.csv</code></summary>

`mutation-rates.csv`:

- tool, voter_group, language, style, dimension, repos, detected, pooled, supported.
- **mutants:** the judged ones.
- **rate:** the mean of per-repository rates.
- **ci_low, ci_high:** bootstrap over repositories, only when supported: score.py's MIN_REPOS and MIN_ITEMS.

`mutation-scores.csv`:

- tool, voter_group, language, style, repos, calls, found_lines, found_site, found_group, decoys, decoys_hit,
  supported.
- **recall, recall_low, recall_high:** on the inserted lines, the headline.
- **recall_site:** call line ±2. recall_group.
- **precision:** calls found on the lines / (those + decoys hit).
- f1, f1_low, f1_high.
- **offedit_mutants and offedit_share:** judged mutants, calls and decoys together, with a new unit or a new
  located site outside the inserted lines; never folded into recall, precision or F1.
- **extra_units_median and extra_sites_median:** over those mutants.
- **baseline_flux and baseline_changed:** the tool's noise floor from `report --baseline <stability experiment>`:
  its mean `flux` there and the share of its snapshots whose repeated runs differ; empty without one.

</details>

<details>
<summary><code>bridge-run&lt;i&gt;.csv</code></summary>

language, tool, confirmation, f1, n, spearman, kendall_tau.

</details>

### Joining the tables

- Records join on `(snapshot, variant, run, record_id)`, groups on `(snapshot, variant, run, group_id)`.
- Group ids restart per snapshot and variant.
- `snapshot` is the result folder name `<owner>__<repo>@<sha12>`; `repo` and `commit` carry the full name and SHA.

```python
import pandas as pd
records = pd.read_csv("_ANALYSIS/records-run1.csv")
members = pd.read_csv("_ANALYSIS/members-exact-run1.csv")
groups = pd.read_csv("_ANALYSIS/groups-exact-run1.csv")
keys = ["snapshot", "variant", "run"]
df = records.merge(members, on=keys + ["record_id", "tool"]).merge(groups, on=keys + ["group_id"], suffixes=("", "_group"))
groups.query("scope == 'repo'")["voter_groups"].value_counts()   # tools per item
groups["tools"].str.split(";")          # list cells

micro = pd.read_csv("_ANALYSIS/micro-run1.csv")
micro.pivot(index="tool", columns="language", values="lenient")      # the inclusion matrix
items = pd.read_csv("_ANALYSIS/items-run1.csv")
items.drop_duplicates(["snapshot", "variant", "unit"])["k_L4"].value_counts()   # tools per item at L4
```

### Export

The analysis files live inside the experiment folder, so the normal export takes them along together with the
record, every CBOM and its meta file. The export runs `analysis report` first, so the tables and
`_ANALYSIS/_SUMMARY.md` in the zip match the stored runs (`ARGS=--no-report` skips it):

```
make experiment-export NAME=stability-pilot   # -> experiments/results/stability-pilot.zip on the host, open stability-pilot/_ANALYSIS/_SUMMARY.md
```
