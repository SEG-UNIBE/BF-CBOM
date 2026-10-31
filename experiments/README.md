# Experiments

How to run BF-CBOM's batch experiments and what they produce. Specs live here and are mounted read-only into the
`runner` container; everything a run produces stays in Docker volumes until it is exported. Setting up the stack: the [root README](../README.md#setup); how the analysis
reads the CBOMs (units, normalisation, the ladder, voter groups): [`analysis/README.md`](../analysis/README.md); the
repository lists and fixtures: [`corpus/README.md`](corpus/README.md).

```
experiments/
├── *.yaml       # experiment specs
├── corpus/      # pinned repository lists, exclusions and micro fixtures
└── results/     # exported ZIPs and extracted results (ignored by Git)
```

Every command runs inside the `runner` container through `make` (never plain `docker compose`):

```
make experiment EXP=experiments/bar-micro.yaml        # pin commits, mirror to the git server, enqueue, collect (resumable)
make experiment EXP=... ARGS="--overwrite-old"          # the whole spec as if it had never run (ARGS: --retry-failed, --rerun <tools>)
make experiment-status NAME=bar-micro                 # Redis progress + what is on disk
make experiment-attach                                  # join the terminal of the running `make experiment` (detach Ctrl-P Ctrl-Q)
make experiment-collect NAME=bar-micro                # collect results of jobs already queued (after ARGS=--no-wait)
make experiment-cancel NAME=bar-micro                 # remove its queued jobs from the worker queues
make experiment-list                                    # experiments stored on the volume
make experiment-delete NAME=bar-micro                 # remove one from the volume (asks first; ARGS=--yes skips)
make experiment-export NAME=bar-micro                 # -> experiments/results/bar-micro.zip (runs the analysis report first)
make experiment-export-all                              # every complete experiment (no run missing), one ZIP each, overwriting
make corpus ARGS="--per-cell 3"                         # stratified GitHub corpus -> results volume corpus/corpus.yaml + corpus-frame.yaml
make corpus ARGS="--frame /data/results/corpus/corpus-frame.yaml --per-cell 2 --out /data/results/corpus/corpus-pc2.yaml"   # redraw from a stored frame, no search (without --out it replaces corpus.yaml)
make corpus-prune ARGS="--in /data/results/corpus/corpus.yaml --from stability-pilot"   # drop repos a tool timed out on
make corpus-export                                      # corpus YAMLs (sample and frame) -> experiments/results/
make analysis ARGS="report bar-micro"                 # the analysis alone (below), into <exp>/_ANALYSIS/
make board                                              # live board of the stack: experiments, workers, queues (ARGS=--once: one snapshot)
```

Exports are the only way results reach the host. An unzipped export is analysed there without Docker:
`RESULTS_DIR=experiments/results uv run bf-cbom analysis report <exp>` ([Analysis](#analysis)).

## Specs in this folder

The name says the kind of spec, then what it runs on: `bar-` (the bar exam on ground truth), `stability-` (repeated
runs), `corpus-` (real repositories as they are), `mutation-` (real repositories with their mutants). Renamed from
`e0-`, `e1-` and `e4-` on 2026-10-22, so that no experiment number reads as a research question.

| Spec | Tools | Repos | Purpose |
|---|---|---|---|
| `bar-micro.yaml` | all 15 tools, once | the 3 micro projects (`experiments/corpus/micro`: Java, Python, Go) | the voter bar: the micro check against the generated ground truth (`make analysis ARGS="micro bar-micro"`), whose verdicts the user writes into the later specs by hand (`tools`, `tool_languages`; at least 7 of 14 in their functions in one paper language); 45 jobs |
| `stability-pilot.yaml` | the 10 tools run after the bar exam, 3 repetitions | 3 micro projects + the 10 fastest pilot repos | run-to-run variance (`stability.csv`) and consensus sets for the scores; 429 jobs at most |
| `corpus-pilot-small.yaml` | the 10 tools run after the bar exam | the 12 pilot projects in Java, Python and Go (`experiments/corpus/pilot-2026-10-08.yaml`) | first real workload: location-aware agreement on the pilot's pinned projects; large repos (Kafka, Keycloak) push CBOMkit towards its timeout |
| `corpus-pilot-medium.yaml` | the 10 tools run after the bar exam, once | 60 manually selected projects, 20 each in Java, Python and Go (`experiments/corpus/pilot-2026-10-22.yaml`) | second pilot, pinned to default-branch HEADs on 2026-10-22; 900 tool/repository pairs |
| `corpus-icpc.yaml` | the 10 tools run after the bar exam | 23 projects of the ICPC 2026 paper (`experiments/corpus/icpc-2026.yaml`, HEAD of 2026-10-18) | held-out counterpart of the pilot corpus: none of these repos was used to tune the vocabulary, the specs or CipherSift; six are marked `expect_crypto: low` (negative cases) |
| `corpus-main.yaml` | the 10 eligible tools, once | the stratified GitHub sample of 2026-10-30 (`experiments/corpus/corpus-pc6.yaml`): 54 repos each in Java, Python and Go, 6 per cell | the paper's corpus run (RQ1-RQ4); 1620 tool/repository pairs, 1242 supported scans |
| `mutation-smoke.yaml` | the 10 tools run after the bar exam | pyjwt, jjwt, golang-jwt (pilot), 3 mutants each | the mutation harness end to end: the RSA-2048 keygen inserted in each style; CBOMkit and CryptoScan should find the direct one at its line (`mutation.csv`) |
| `mutation-pilot.yaml` | the 10 tools run after the bar exam, once | 9 pinned pilot-medium repos, 3 per language | mutation pilot: every catalog snippet once per repo (15 calls, 5 per style) and 15 decoys; 4185 planned results, currently 3348 supported scans |

LLM providers are outside the current experiment scope; their implementation remains available for possible later use.
Every tool votes. The micro check reports the bar exam's verdicts but does not edit `common/tools.yaml` or the specs.
Job totals include tool/repository pairs recorded as `unsupported` without scanning. `stability-pilot` still includes
four historical pilot repositories outside the paper languages (TypeScript, JavaScript and C).

**Second pilot (60 repositories).** On the VM, after `make up-prod`:

```sh
make experiment EXP=experiments/corpus-pilot-medium.yaml EXPERIMENT_MEMORY_BUDGET_GB=0
make experiment-status NAME=corpus-pilot-medium
make experiment-export NAME=corpus-pilot-medium   # generates the report and experiments/results/corpus-pilot-medium.zip
```

The corpus records the full repository names and commits. It is a manual selection, separate from the
stratified corpus built by `make corpus`. Rerunning the command resumes this experiment at the pinned commits.

**Mutation pilot (`mutation-pilot.yaml`, selected 2026-10-23).** Nine repositories, three per language: three is the
floor for a bootstrap interval over repositories (`analysis/score.py` `MIN_REPOS`, which `mutation-scores.csv` and the
bridge use), so every per-language recall, precision and F1 of the pilot comes with an interval. The evidence is the
exported `corpus-pilot-medium/_ANALYSIS/` (`runs.csv`, `units-repo-run1.csv`, `items-run1.csv`, `records-run1.csv`;
re-rendered with vocabulary 2026-10-23.2). Candidates were screened for successful supported scans, no OOM kill,
several tools reporting algorithms at first-party locations, algorithm diversity, low runtime and a different context
from the other two repositories of the language. These are purposively selected, favourable pilot cases, not a random
or held-out sample. They do not replace the planned mutation-main corpus or support population-wide claims.

| Repository | Language | Successful / supported tools | Tools reporting algorithm items | Distinct algorithm identities | Summed tool seconds |
|---|---|---:|---:|---:|---:|
| `mwiede/jsch` | Java | 11/11 | 10 | 45 | 289 |
| `beemdevelopment/Aegis` | Java | 11/11 | 10 | 14 | 258 |
| `ESAPI/esapi-java-legacy` | Java | 11/11 | 9 | 16 | 190 |
| `Yubico/yubikey-manager` | Python | 13/13 | 13 | 34 | 135 |
| `ssh-mitm/ssh-mitm` | Python | 13/13 | 13 | 22 | 132 |
| `cowrie/cowrie` | Python | 13/13 | 13 | 24 | 285 |
| `pion/dtls` | Go | 12/12 | 12 | 30 | 106 |
| `go-webauthn/webauthn` | Go | 12/12 | 12 | 23 | 84 |
| `gopasspw/gopass` | Go | 12/12 | 12 | 24 | 123 |

Identities are the union of normalised L4 algorithm items reported by tools, not verified ground truth; repeated
occurrences and cross-tool duplicates are not counted as extra identities. Successful tools have `status=ok`;
unsupported tools are excluded from that denominator. No selected run recorded an OOM kill. The contexts: an SSH
library, an authenticator app and a web-security API (Java); hardware-key management, SSH interception and a honeypot
(Python); transport cryptography, WebAuthn and a password manager (Go). SSHJ and supertokens-core are reserves (SSHJ
would duplicate the SSH-library context; supertokens-core costs 491 s per scan). Very large or troubled cases such as
Vault, Keycloak, FairEmail and Scapy were not chosen: their cost or failures would be multiplied across variants. No
tool is removed from the experiment on the basis of its pilot findings.

`per_repo: 15` plants every catalog snippet once per repository (the generator takes the least-used snippet per
style, so 15 calls are 5 per style), and `decoys: equal` as many decoys; with three repositories per language every
(snippet, style) cell of a language is filled three times. Mutation preflight (2026-10-23, the first six repositories)
used source archives at the pinned commits and the generator with `per_repo: 12`, `decoys: equal`, all
snippets/styles, seed 1, catalog version `2026-10-22.1`, its commit-writing step replaced with in-memory capture: all
six produced 12 distinct snippet/style pairs and 12 decoys across 24 distinct source files per repo, with no
skipped-insertion notes; host counts were 1438 (jsch), 1227 (Aegis), 597 (yubikey-manager), 665 (ssh-mitm),
1288 (dtls) and 310 (webauthn). This checks dependency eligibility and insertion syntax/placement; it does not compile
the complete projects or measure how the tools detect the inserted calls. The Python projects admitted the pyca
snippets and the Go projects PBKDF2 (`golang.org/x/crypto`). The three repositories added for the interval (ESAPI,
cowrie, gopass) were not preflighted; a repository with too few hosts gets fewer mutants and a note in
`mutants.json`. The VM run records the full patches there.

Budget: 9 x 31 variants x 15 tools = 4185 planned results; current language support means 3348 actual scans and
837 unsupported results. Multiplying the pilot-medium durations (1'602 s summed over the nine originals) by 31 gives
about 14 summed tool-hours. The slowest worker lane (cdxgen, 519 s summed) extrapolates to about 4.5 hours with one
replica, 2.2 with two (`make up-prod REPLICAS="cdxgen=2"`), before mutation preparation and resource contention; these are planning estimates, not measured
mutant runtimes. Repetitions are 1, keeping the existing stability experiment responsible for repeatability checks.
`mutation-smoke` runs first.

```sh
make experiment EXP=experiments/mutation-pilot.yaml EXPERIMENT_MEMORY_BUDGET_GB=0
make experiment-status NAME=mutation-pilot
make experiment-export NAME=mutation-pilot
```

After a change to one worker, `make experiment EXP=experiments/mutation-smoke.yaml ARGS="--rerun <tool>"` redoes
its runs on three small repos and their mutants, one repo per paper language; its micro check, the same on
`bar-micro.yaml` (3 jobs). The corpus run on the full corpus (Java, Python and Go) and the mutation run on its
subset have no specs yet; the corpus spec needs the corpus from `make corpus`
first, pruned with
`make corpus-prune ARGS="--in /data/results/corpus/corpus.yaml --from stability-pilot"` to drop repos a tool
timed out on.

## Spec format

Each spec is one `experiments/<name>.yaml`. An example with every field:

```yaml
name: e1-corpus            # [A-Za-z0-9._-]
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
mutation:                  # optional: every repo also gets mutants as variants (RQ5, below)
  per_repo: 10             # mutants per repo (fewer when not enough (snippet, style) pairs fit a host)
  styles: [direct, wrapper, indirect]
  snippets: all            # or a list of catalog ids (mutation/catalog/<language>.yaml)
  seed: 1
  decoys: equal            # planted decoys per repo: `equal` (as many as calls, default), an integer, 0
```

Rerunning a spec is resumable: runs whose `run<i>.meta.json` exists are skipped, commits come from the
experiment's `_experiment.yaml` (below), and jobs still pending from an interrupted run are collected (`--requeue-pending` to reissue).
`ARGS=--retry-failed` redoes stored runs whose status is not `ok`; `--rerun <tool>[,<tool>]` redoes every stored run
of those tools, whatever its status (after a worker change, e.g. `--rerun cdxgen,cryptobomforge`).
`ARGS=--overwrite-old` runs the whole spec as if it had never run: it first cancels the jobs an earlier run left and
removes the experiment folder with everything in it (stored runs, the record, `_run-log.json`, `mutants.json`,
`_ANALYSIS/`, files of older layouts), then resolves the spec afresh (the new session records `runs_overwritten`).
`make experiment-delete NAME=<exp>` removes the same folder and cancels the experiment's jobs. Results are never kept
for their own sake: the spec pins every repo's commit (the corpus files and the corpus builder pin them; a spec
without a commit gets the branch head of the day) and the mutants are deterministic, so a rerun scans the same
snapshots and repeats the results.

**Mutants (`mutation:`).** For every repo of a spec with a `mutation:` block the runner makes `per_repo`
insertion mutants before anything is enqueued (`mutation/mutants.py`): one catalog snippet (`mutation/catalog/
{java,python,go}.yaml`, by the repo's `language`) in one style, inserted into a first-party method or function that
has a caller (or a `main`), committed on the pinned commit as the branch `bfcbom/ins-<snippet>-<style>-<hash>` of the
base mirror and scanned like any variant. A snippet only goes where its library is already declared (pyca
`cryptography` for Python's asymmetric and AES snippets, Go 1.24 or `golang.org/x/crypto` for Go's PBKDF2); dependency
files are never edited. `mutants.json` pins the mutants like `_experiment.yaml` pins the commits: a resumed run
keeps them (a changed `mutation:` block is ignored with a note) and rebuilds missing branches with the same SHA from
the recorded inserts; `--overwrite-old` makes them again, the same ones. Per mutant it records the snippet, style, catalog version, file, host
(name, line, number of callers by name), the line naming the algorithm (`call_line`), the related lines, the inserted
spans, the line shifts, the inserts, the expected record and the patch. Repos without a catalog language or a host get
none, with a note.

**Corpus sampling (`make corpus`, `common/corpus.py`; design of 2026-10-29).** The main corpus is a stratified sample
of GitHub: for each paper language the cells are star bin x size bin (defaults `--star-bins 50-300,300-2000,2000-`
and `--size-bins 300-3000,3000-30000,30000-` in KB of GitHub's packed `size`, bins `[lo, hi)`, the open last size bin
ends at the repo size cap: 1536 MB, `CORPUS_MAX_REPO_SIZE_MB`, a quarter of the git server's 6 GB when the paper's corpus was built, so that a full clone through it always fit), and every cell gets the same quota, `--per-cell` (default 2, so 3 x 3 cells x 2 = 18
repos per language, 54 in all; `--per-cell 3` gives 27 and 81). Equal allocation per cell is deliberate: the sample
covers the design space evenly for a comparison of tools across strata, it is no estimate of the GitHub population.
The builder first enumerates each cell's whole population (`pushed:` within 365 days, `archived:false fork:false`,
paginated to the API's limit of 1000 results; a cell above it is split by creation date until every part fits), then
draws from the name-sorted population with the seed (`--seed`), and keeps the first `per_cell` candidates that pass
the exclusions (`experiments/corpus/exclusions.yaml`, `tailored_on`, the name patterns), the size cap and the crypto
pre-filter (a shallow clone and a grep of first-party source for the crypto API families of the language), so the
draw is uniform over the eligible repos of the cell. No topic targeting: the crypto-use pre-filter is the one content
criterion. The corpus file records the frame (`params`: search date, bins, quota; `cells`: every cell with its
`population`, how many were enumerated, tried and sampled), every sampled repo with its `stratum` and two covariates
that are recorded but never selected on, `crypto_apis` (the API families the pre-filter matched: JCA, BouncyCastle,
JSSE; pyca, hashlib, ssl, pycryptodome, nacl, pyOpenSSL, other; Go stdlib, x/crypto) and `topics` (the repository's
GitHub topics), and every candidate dropped under `excluded` with its reason. Next to it, `<corpus>-frame.yaml`
holds the populations themselves (every enumerated repo of every cell, its query and the search date) and the crypto
checks by commit, so the draw can be verified offline (frame + seed -> sample) and a rebuild with `--frame` draws
another `--per-cell` or `--seed` from the same population without a search request (on one frame the smaller
quota's sample is inside the larger one's; a check is reused while the repo's default branch is at the same
commit; an interrupted build resumes from its frame on the same day). A redraw needs the frame's languages, bins and cap (anything else is a new search), pins every drawn repo at its current default-branch commit (a repo that moved since is checked again, and one that no longer passes gives way to the next candidate of its cell), and writes `--out` (default `corpus.yaml`, which it replaces) with a copy of the frame next to it, `<out>-frame.yaml`. The record `_experiment.yaml` carries
`stratum`, `crypto_apis` and `topics` per repo. After the stability run `make corpus-prune` drops the repos a tool
timed out on (listed under `pruned`, never replaced).

**Which repos an experiment used** is answered by one file, its record `_experiment.yaml`: the spec as run, every
repo with the branch and commit it was resolved to (a fixture with its `path` and published commit) and its size at
that commit (`files` and `source_files`, the latter of the repository's language, `checkout_bytes` and
`source_bytes`, the blobs without history; counted from the mirror, so an export carries the size the run-time
table scales by and the summary's "Repositories" table lists), the tools, the repetitions and the `mutation:` block. `experiment run` writes it once the snapshots
resolve, and a resumed run
(without `--overwrite-old`) keeps its commits for every repo the spec still names, so it never moves to a newer
upstream commit halfway; a repo the spec dropped leaves the record, and `mutants.json` holds the mutants of
exactly the record's repos. The runner's overview and the analysis read only the stored runs of the repos, tools and
repetitions the record names (`common/experiment.py:iter_results`), so a run stored under an earlier version of the
spec never reaches a table; the `_SUMMARY.md` header repeats the counts. The record is itself a fully pinned spec:
`experiment run` on it replays exactly those snapshots. The spec in `experiments/` and its corpus file are the input
that produced it; an export carries the record.

**What an experiment folder holds.** `_experiment.yaml` (the record, above), `_run-log.json` (one entry per
`experiment run` / `collect` session: start and end, the Docker VM's RAM and CPUs, the memory budget and waves, and the
peak RAM in use on the VM, sampled every 2 s), `_ANALYSIS/` (the derived tables and `_SUMMARY.md`, regenerated by
`analysis report`), `mutants.json` (with a `mutation:` block) and one folder per snapshot, with one
subfolder per variant (`original` and the mutants). The leading underscores keep the first three on top of a
file listing. Every `run<i>.meta.json` also records the worker container's peak working set during the job (sampled
every second, the figure `docker stats` shows), its memory limit, how often its usage reached that limit and the OOM
kills during the job (`peak_memory_mb`, `memory_limit_mb`, `limit_hits`, `oom_kills`; 0 limit hits: the limit never
constrained the job, and on a host without swap memory the kernel cannot reclaim ends in an OOM kill), the
container's CPU time during the job and that over the job's wall time, the mean number of cores it kept busy
(`cpu_seconds`, `cpu_cores`; read from the cgroup's `cpu.stat` at the job's start and end, no sampling), when the job was enqueued, started and finished (`enqueued_at`, `started_at`,
`finished_at`), the BF-CBOM commit the worker image was built from (`bfcbom_commit`, `-dirty` = uncommitted changes)
and, for LLM workers, the structured `usage` (requests, input / cached / cache-write / output / reasoning tokens, the
model ids the provider answered with; kept on failed runs). `_run-log.json` also records the runner's commit and the
image ids of the workers (by content) per session. There is no backward compatibility with experiments stored before
these fields existed: rerun them.

## Running an experiment

**Watching a run.** On a terminal `run` and `collect` keep a live board at the bottom (`misc/cli/progress.py`): one
line for all results (done/total, remaining time, elapsed time, the wave and the statuses so far), then one per tool
with its count and remaining time (`progress.forecast`: its jobs left times its mean duration so far; the batch ends
with its slowest tool), the job it runs now and for how long (the longest-running one first, from the start times the
workers write to Redis), how many wait and what failed; tools of a later wave wait with their clock still. Every
stored result still prints its own line above the board (status, tool, snapshot, run, duration, path). Pinning and
mirroring count the repos the same way (a line announces the two slow cases, a mirror cloned on first use and a fetch
for a pinned commit the mirror lacks; everything else is local), and so does `corpus build` per language (repos
picked out of the quota, the cell searched or the repo cloned for the crypto check). Off a terminal only the lines
print. The display belongs to the terminal `make experiment` runs in; `make experiment-attach` joins it from another
shell (tmux window, second ssh session) and shows the board and the result lines from then on. Detach with Ctrl-P
Ctrl-Q: Ctrl-C there interrupts the run exactly as in its own terminal, and `make experiment-collect` resumes it.
`--no-wait` enqueues only the first wave; after `make experiment-collect NAME=<exp>` rerun `run` for the next one.
Jobs left pending by an interrupted run are already in the queues and are collected in wave 1 regardless of the
budget.

`make board`, in a second terminal, shows the whole stack (an experiment's bar counts the results the runner has
stored, so after `ARGS=--no-wait` it moves once `make experiment-collect` runs):
- every running experiment with a bar, results stored out of expected, its wave, the failures so far and the time
  left (the same forecast as the runner's board; `≥` while a tool has no result yet);
- every worker: busy, idle or down, the commit its containers were built from (`build`, red when it is not the
  checkout's), the job it runs (repo, run, experiment) and for how long, the container's RAM against its limit, the
  jobs queued for it and its last result;
- the checkout's commit, whether Redis, the git server and the UI answer, and the host's RAM; when a running container
  (a worker, the git server, the runner) was built from another commit than the checkout, a red line names it, and
  `make up-prod` rebuilds it. Check it after every pull: the stored runs carry each image's commit
  (`bfcbom_commit`), and an experiment run on old images measures the old code.

`Ctrl+C` closes the board; the experiment and the stack go on (`make up-prod` opens the board by itself once the
stack is up; `make logs` shows the containers' raw logs, `make down` stops the stack).

When a run ends it prints an overview (`misc/cli/overview.py`): runs stored out of expected, per tool the counts per
status, median and longest duration, peak RAM against the limit and OOM kills, the LLM token usage with an estimated
cost, every run that is not ok with its most telling error line, and the commands to retry, resume or export.
`make experiment-status` shows the same plus the live progress from Redis, `make experiment-list` one line per
experiment.

**Parallelism and memory.** The laptop prepares code and analyses copied exports; the VM runs the batches with
`EXPERIMENT_MEMORY_BUDGET_GB=0`.
- *One job per worker container.* Each worker pops one job from its Redis queue, runs the tool in a subprocess,
  pushes the result and only then takes the next. A batch has as many jobs in flight as there are worker containers
  (with `make up-prod` one per generator, each on a different repository); queued jobs cost only a Redis entry.
- *The git server serves clones side by side.* Every worker clones its repository from the local git server (nginx,
  git's smart-HTTP backend through fcgiwrap). 16 fcgiwrap processes serve up to 16 requests at once, more than the
  workers that clone at once. Until 2026-10-30 one process served one request at a time, so a long full clone
  (CBOMkit) made every other worker's clone wait, and since a job's time includes its clone, that wait counted as
  the other tools' run time.
- *Largest first.* Every tool's queue gets the repositories largest first (`source_bytes` at the pinned commit), so
  the longest jobs do not start last and set the batch's end. Early on, the remaining-time forecast is therefore too
  high: its mean so far comes from the largest repositories.
- *Memory limits are caps, not reservations.* Every worker has a memory limit in `docker-compose.yml`, mirrored by
  `memory_gb` in `common/tools.yaml` (a test keeps them equal; adjust both). Idle workers use little, large jobs can
  reach their caps, and the VM also needs memory for the coordinator, Redis and the git server (`GITSERVER_MEMORY`,
  16g, which leaves room for the concurrent full clones of CBOMkit replicas). On the VM the caps of every container
  an experiment feeds, replicas included, plus those of the services stay within its RAM (corpus-main with
  `REPLICAS="cbomkit=2"`: 115 of 121.5 GiB): every job can then reach its cap at the same time, a tool's reported
  peak is its own and not our limit's, and the kernel never has to pick Redis or the git server for an OOM kill.
  CBOMkit's limit is set by its Java heap (`JAVA_OPTS` `-Xmx`, 4 GiB below the container's limit): a full heap
  ends the job as a failure, not an OOM kill, so the two rise together.
- *Memory budget (waves).* `EXPERIMENT_MEMORY_BUDGET_GB` (Makefile, default 14 for a laptop whose Docker Desktop VM
  has ~20 GiB) caps the summed limits of the tools fed at once: the runner packs the tools into waves (largest
  first), feeds one wave, collects it, then feeds the next; workers of later waves sit idle meanwhile. A tool above
  the budget runs alone with a warning, so the budget is not a hard cap. The plan is printed before anything is
  enqueued and stored in the experiment's Redis status (`waves`, `wave`). On the VM use 0 (one wave, everything in
  parallel), per call (`make experiment EXP=... EXPERIMENT_MEMORY_BUDGET_GB=0`) or once in the Makefile. The UI
  applies the same budget: jobs of later waves are created *held* (⏸️) and queued once the current wave is done.
- *Replicas.* Workers are stateless, so several containers can share a tool's queue: `make up-prod
  REPLICAS="cbomkit=3,cipherradar=3,scanoss=2"` starts that many (`--scale worker-<tool>=N`). Every container writes
  a heartbeat of its own (`worker:<tool>:replica:<host>`), and an experiment counts them when it starts and on every
  refresh, so `make experiment` needs nothing more: the budget counts each replica, the forecast shares a tool's
  remaining jobs among its containers, `_run-log.json` and the summary record the replicas, and the board shows a
  tool's containers in one row (`busy 2/3`, their summed RAM). A later `make up-prod` without `REPLICAS` scales
  back to one container each; on the VM, `export REPLICAS=...` in the shell profile keeps the choice. Run times
  measured with replicas include their contention for the CPUs.
- *The analysis report in parallel:* `--jobs` and `RUNNER_MEMORY`, see [Analysis](#analysis).

## Analysis

The commands, the files they write and their columns. What the analysis counts and why (units, normalisation, the
ladder, voter groups, family and group) is in [`analysis/README.md`](../analysis/README.md); plot recipes on these
tables in [`analysis/report/README.md`](../analysis/report/README.md).

The analysis (`analysis/`) works offline: it reads the stored CBOMs from the results volume and needs neither Redis
nor running workers. It uses only runs with status `ok` (failed, timed-out and `unsupported` runs are skipped) and,
by default, only algorithm assets and repetition 1. Every agreement analysis (RQ1-RQ4: conformity, ladder, matchers,
scores; stability, micro) reads the `original` variants only; mutants are normalised too and feed the RQ5 tables
alone, so a mutated repository is never counted once per mutant.

```
make analysis ARGS="report stability-pilot"     # all of the below in one pass, plus _ANALYSIS/_SUMMARY.md (Markdown tables)
make analysis ARGS="normalize stability-pilot"  # every CBOM -> normalised records (run<i>.norm.json next to it)
make analysis ARGS="schema stability-pilot"     # RQ2 (validity, meaningful shares) and RQ1 traceability (--fetch on a laptop)
make analysis ARGS="levels stability-pilot"     # how much agreement normalisation creates: L0 (raw) to L4 (tool specs)
make analysis ARGS="micro bar-micro"          # score each tool against the micro projects' ground truth, two levels
make analysis ARGS="tables stability-pilot"     # flat CSV tables (runs, records, exact-matcher groups, micro) for pandas/R
make analysis ARGS="score stability-pilot"      # RQ4: per-tool rates (confirmation, exclusivity, Dawid-Skene, leave-one-out, CIs); stability
```

**`report <exp>`** normalises every CBOM once and writes the CSV tables for the analyses below, then
`_ANALYSIS/_SUMMARY.md`: Markdown tables for a first look (runs per tool, the micro inclusion matrix and recall at two
levels, who finds each micro asset, fill rates and spec issues, the L0-L4 ladder, how many voter groups report each
algorithm per repository, LLM token counts, the list of files). Its Preamble ends with the repositories the record
names, each linked to GitHub at its pinned commit, folded into one column per language, or flat with the size when
the corpus builder recorded one. The summary is rendered from the CSV files and the record, so
it never shows anything they don't hold; `python -m analysis.summary <exp>/_ANALYSIS/` rebuilds it from an export.
Its numbers carry `'` between thousands (13'043); the CSVs keep plain numbers. Each report clears and rebuilds
`_ANALYSIS/`. The standalone `micro` command also writes `micro-run<i>.json`; `report` writes its micro results
as CSV tables instead.
`report --baseline <experiment>` reads another, already reported experiment's `stability.csv` (repeated runs on
unchanged code) and joins each tool's noise floor into `mutation-scores.csv` (`baseline_flux`, `baseline_changed`),
so RQ5's "Changes off the edit" table can be read against it; a baseline without that file is a clear error before
anything runs. `experiment export` runs `report` first (skip with `--no-report`), so every zip carries current tables. On a
terminal both show a live line per step (done/total, remaining time, elapsed time and the run being worked on), so a
long step reads as working, like a run's board ("Watching a run"); off a terminal each step prints one line when it
ends.

**`normalize <exp>`** reads every stored ok CBOM (all repetitions, including mutants) into normalised records with
its tool's adapter spec, as described in [Normalisation](../analysis/README.md#normalisation-from-a-cbom-to-records). It writes the normalised files and the
first two diagnostic tables below. `tables` (also part of `report`) writes the per-run coverage, units and
conformance tables. The diagnostic tables aggregate original variants only, so mutants do not multiply counts.

| File | Content |
|---|---|
| `<snapshot>/<variant>/<tool>/run<i>.norm.json` | next to `run<i>.json`: declared format/version, adapter spec and versions, the original's sha256 (detects a stale file), stats, the records |
| `_ANALYSIS/normalize.json` | per tool over all repetitions of original variants (the normaliser's diagnostics): formats declared, assets per asset type, where values came from (standard field, property, occurrence, name, API context), spec issues, conflicts, top unmapped values |
| `_ANALYSIS/vocab.csv` | one row per (tool, source, raw value, asset type of the record; empty when it has none, so a name read off a certificate file stands apart from the same name on an algorithm): status (`mapped`, `partial`, `unmapped`, `ambiguous`, `known`, or the entry's `kind`: `api`, `non-crypto`), what it mapped to, leftover tokens, via, interpretive, kind, count; the reviewable mapping table |
| `_ANALYSIS/coverage-run<i>.csv` | one row per (tool, dimension), run i, original variants (the same CBOMs as `units-run<i>.csv`): algorithm assets (CBOM components) with a value the tool reported vs only one we derived, as counts and shares of the assets (fill-rate heatmaps); every row also carries the tool's `assets` and the assets per resolution (`mapped`, `ambiguous`, `api`, `non-crypto`, `undefined`) |
| `_ANALYSIS/units-run<i>.csv` | one row per tool, run i, original variants: how it packages its findings: `packaging` (the hand label of `common/tools.yaml`: per hit, per rule, per algorithm, per family, no places, unknown; `analysis/README.md` "Packaging per tool"), `assets` (algorithm components; the summary's column reads `algorithm assets`, since certificates, protocols and key material never become items), `records` (their occurrences), `items` (distinct L4 identities of its mapped records per repository, summed), records_per_asset_median / _max, multi_occurrence_share (assets at more than one place), assets_per_item (above 1: one component per finding), unlocated_share (assets without a file), loc_line / loc_window / loc_import / loc_file / loc_none / loc_unread (algorithm records by the kind of place they name, `analysis/README.md` "Units"), outside_code (algorithm records whose file is not a source file of the repository's language: fixtures, test vectors, configuration, documentation, other languages; the summary's "Places per tool" shows it as a share of the located records) |
| `_ANALYSIS/units-repo-run<i>.csv` | one row per (tool, repository), run i, original variants: `assets`, `records` and `items` of that CBOM as in `units-run<i>.csv` (whose counts are these rows summed), with `language`; the summary shows them as a repository x tool matrix |
| `_ANALYSIS/sources-run<i>.csv` | one row per (tool, dimension, kind of source), run i, original variants: the algorithm assets whose value of that dimension came from a standard `field`, a tool `property`, the parsed `name`, an `occurrence`, an API identifier (`context`), a `ref`, a `dependency` or was `derived`, with the share of the tool's algorithm assets (RQ2, the summary's "Where the information lives") |
| `_ANALYSIS/registry-run<i>.csv` | the delta to the CycloneDX registry (RQ3): one row per addition of `algorithms.yaml` (`kind: addition`; name, `gap`: missing-family, scheme-less, generic-marker, non-crypto; primitive, `count` = items resting on it, repos, tools, the reason), one per unit under no family (`kind: unknown`), one per table of the identifier layer with its size (`kind: layer`) |
| `_ANALYSIS/conformance-run<i>.csv` | one row per (tool, scope, issue kind), run i, original variants: `asset` scope counts assets with a value read from standard fields that break the declared version's schema (field, enum, type, deprecated), `document` scope counts CBOMs (dialect, version); tools without issues have no row |

**`schema <exp>`** answers RQ2 (`analysis/schema.py`): every CBOM that declares a CycloneDX version with a vendored
schema (1.6, 1.7) is validated as a whole document with `jsonschema` (offline, `referencing` registry), reported per
CBOM in `schema-run<i>.csv` (valid, error count, error kinds `<keyword>:<path>`); tools whose CBOM our worker assembles
(`cbom_by: bf-cbom` in `common/tools.yaml`, the LLM workers) are reported apart. `meaningful-run<i>.csv` gives, per tool,
CycloneDX asset type and that type's own fields (`analysis/schema.py:MEANINGFUL_FIELDS`: an algorithm's primitive,
parameter = parameter set or curve, mode, padding, functions, OID, security levels; a key's type, size,
algorithm, format; a certificate's subject, issuer, validity, signature algorithm, public key, format; a protocol's
type, version, cipher suites; algorithm assets have no dedicated key-size field), the share of assets carrying a value in the standard
fields and, where the normaliser reads such a value, from any source (the standard fields included, so it is never
lower). `traceability-run<i>.csv` gives per (snapshot, tool, asset type) how
many records name a file (`located_file`), a file and a line (`located_line`), how many of those with a line exist at
the pinned commit with the line inside the file (weak) and how many also have a vocabulary token within ±2 lines
(strong); `related-crypto-material` (keys, secrets) is kept apart from algorithms. In `_SUMMARY.md`
every asset type has its own table under `#### Asset type`, CycloneDX's four in the standard's order (algorithm,
certificate, protocol, related-crypto-material), then `unknown` for values outside them. The traceability check needs
the snapshot's files: on the VM the git mirror; on a laptop with a copied export `--fetch` pulls each pinned commit once
into `SNAPSHOT_CACHE_DIR` (default `~/.cache/bf-cbom/snapshots`, one shallow `git fetch` per repo). `report` runs all
of this too (`report --fetch`). A fixture (`path:`, the micro projects) needs no `--fetch`: the report publishes it
from its local directory into the cache, when that directory still gives the recorded commit.

**Analysing a copied export on the host.** The analysis only reads files, so an export unzipped or copied into
`experiments/results/` can be analysed without Docker. Install the analysis dependencies with `uv sync --extra raqun`, then run
`RESULTS_DIR=experiments/results uv run bf-cbom analysis report <exp> --fetch`.
The VM runs the experiments; the laptop re-runs the analysis with other options.

**In parallel.** `report` (and `tables`, `normalize`, `schema`, `levels`, `score`, `micro`) normalises, validates and
matches in a process pool, one CBOM or one repository per task: `--jobs N` (env `BF_CBOM_JOBS`; `experiment export`
takes it too). `0`, the default, is auto: `min(CPUs, 8)` workers for a step with 16 or more items, else sequential;
`1` is sequential. The results do not depend on it: every table, the summary and the normalised CBOMs are byte-identical
to `--jobs 1` (the matchers' timing columns aside). In the runner the pool is bounded by `RUNNER_MEMORY` (Makefile, default 8g); both are Makefile variables
(on the VM, 64 CPUs and 122 GiB since 2026-10-29, with no experiment running: `make experiment-export-all
BF_CBOM_JOBS=32 RUNNER_MEMORY=96g`; both are `?=` in the Makefile, so exporting them in the VM's shell profile makes
them the default there). A worker the cap kills ends the report, so lower the jobs or raise the cap. Beyond some 16
workers the matching step gains little: it has one task per repository, and the largest repository bounds it.

**RaQuN** (matcher (c), `analysis/matchers/raqun.py`) runs next to the exact matcher, always in the runner image
(the `raqun` extra, PyQuN from the user's fork) and on the host after `uv sync --extra raqun` (`--raqun/--no-raqun`
forces it): tools are models, a tool's distinct canonical assets are elements, built from the exact matcher's
records (mapped, with an L4 identity), with `<dimension>=<value>` attributes (the identity at the level and the
standard's dimensions, not our `group`), RaQuN's Jaccard on them is the per-field agreement;
`groups-raqun`/`members-raqun`/`agreement-raqun-run<i>.csv` have the same columns as the exact tables (at the
scoring level), `matchers-run<i>.csv` compares the two partitions per repository and level (co-membership pair
Jaccard, share of groups that sit inside one group of the other matcher), and RaQuN's rows in
`matchlevels-run<i>.csv` put it next to the exact matcher level by level. Only `algorithm=<identity at the level>`
varies: `family`, `primitive`, `mode`, `padding`, `curve`, `key_size` and `digest` use their full normalised values
from L0 onward, with missing values omitted (controlled comparison, 2026-10-23). Each level is matched independently;
groups can rearrange and their count need not decrease. The parameter-agreement columns still use the ladder's
source-admission rules; these describe the resulting groups, not the attributes fed into RaQuN. Each matcher's wall
time is recorded next to its results: per repository and level in `matchers-run<i>.csv` (`exact_seconds`,
`raqun_seconds`), summed per level in `matchlevels-run<i>.csv` (`seconds`), and as the summary's "Matching time"
table (RaQuN's n-way matching next to the exact matcher's clustering; measured around the `match` call alone, PyQuN's
imports and the vocabulary load excluded, so the process pool does not change the sums).

For visual inspection, **`raqun-L0-run<i>.csv` through `raqun-L4-run<i>.csv`** show one match per row and one
column per tool. A cell contains the distinct reported names, the exact attributes fed into matching at that level,
and the number of underlying records, e.g. `["AES-256-GCM"] <algorithm=AES; family=AES; key_size=256; mode=GCM>
[3 records]`. An empty tool cell means that tool has no element in the match. Singleton matches are included;
filter `n_tools >= 2` for cross-tool matches. Repository, snapshot, variant, run, level, group ID and similarity
identify the match; group IDs are local to that snapshot, variant, run and level, not stable across levels.

**`score <exp>`** computes the per-tool rates without ground truth (RQ4, `analysis/score.py`; no ranking claim) on the exact
matcher's groups. Voter groups follow shared detection logic (the two CBOMkit packagings count once;
using the same pattern engine alone does not merge tools). `--merge-llm` merges the LLM workers.
Per tool, dimension (algorithm, key size, mode, padding, curve, digest, location) and level: the **confirmation rate**
(share of the tool's items another voter group reports too: the same algorithm per repository, the same parameter
value, a shared call site; an item is an L4 identity, a (identity, value) pair or a (identity, file:line) pair, read
off the matcher's groups so that a group is never counted twice) as the **mean of the per-repository rates** (`rate`,
the point estimate; `rate_pooled` is the share over all items) with a bootstrap CI of that mean over repositories
(`--samples`, default 200), the **exclusivity** (share no other voter group reports at all), the rank (only for tools
with items in at least 3 repositories and 10 items in total; others show their rate without a rank or CI), and on
the algorithm dimension a **Dawid-Skene** weighted
support (an EM without labels estimates every voter group's sensitivity and specificity; a tool's support is the mean
posterior of its items). `loo-run<i>.csv` recomputes the ranking without each voter group with Kendall's tau against
the full ranking. Pairs a tool was tuned on are excluded (`--include-tailored` keeps them). `stability.csv` covers
every (tool, repository) with repeated runs: Jaccard of the algorithm sets between runs, the record-count CV and the
consensus set (algorithms in more than half of the runs); with repetitions the scores use that consensus set
(`--no-consensus` scores run i alone). LLM costs come from the stored token usage and the dated price table
`analysis/data/llm_prices.yaml` (`cost_usd`, `priced_model`, `price_period`, `price_table_version` in `runs.csv`; the
summary adds cost per repository, per item and per confirmed item).

**`levels <exp>`** computes the normalisation ladder (L0-L4, see [the ladder](../analysis/README.md#normalisation-from-a-cbom-to-records)): per repository the
items each tool reports (`analysis/README.md` "Units"), and the share that a tool of another voter group
(`voter_group` in `common/tools.yaml`) also reports. It prints a tools x levels table and writes
`_ANALYSIS/levels-run<i>.csv` (dimension, level, tool, voter_group, items, confirmed, share; tool `all` pools
every tool; `dimension` is `algorithm` or a parameter: key_size, mode, padding,
curve, digest, which exist from L2 on) and `_ANALYSIS/items-run<i>.csv`: one row per (snapshot, variant, tool, item;
`unit` = its L4 identity) with the tool's voter group, whether the tool was tuned on the repo (`tailored`),
whether the row is only implied (`implied`, with `--closure`), whether its family is a CycloneDX registry entry
rather than an addition of `algorithms.yaml` (`registry`, RQ3), the repo's language, the number of records and
distinct spellings behind it, and per level the voter groups in the item's largest cluster (`k_L0` .. `k_L4`). That table is location-free, so every tool
takes part; it is the basis for UpSet plots and the tools-per-item distribution. Options: `--run <i>` for another repetition, `--merge-llm` (every LLM worker one
voter), `--include-tailored`, `--closure` (adds the algorithms a record implies).

**`micro <exp>`** scores every tool on the micro projects (`experiments/corpus/micro`, repos named `<owner>/micro-<language>`)
against `experiments/corpus/micro/ground_truth.yaml`, 14 expected items per project (a catalog snippet in a function of its own;
the file calls them `assets`) plus 8 decoys (code that names or resembles cryptography without using it: a comment and
a log message naming algorithms, a CRC32 checksum, a Base64 encoding, a variable named after an algorithm, a hex constant
that looks like a key, a URL ending in `.sha256`, a path naming a certificate file; what each one tests: `corpus/README.md`). Projects and
ground truth are generated from the snippet catalog by `uv run python -m mutation.micro` (never edited by hand; a test
fails when they drift; the ground truth's header states the rules). Scored from the normalised records
(`analysis/groundtruth.py:score_records`) at two levels, call site and repository, side by side and never pooled. It prints one row per project and
tool, including tools that did not run there (unsupported, failed):

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

After the rows the command lists each tool's verdict over all its projects: `bar passed` (the bar cleared in at
least one language), `bar failed` (scored and below the bar wherever scored; the candidates to leave out of the
further experiments) or `not judged` (no scored run). The same verdict is the `bar_verdict` column of
`micro-run<i>.csv` and the `bar exam` column of the summary's "Bar exam" table.

A location window (CryptobomForge's IBM-draft `detectionContext`, the finding ±2 lines) counts at every line it spans.

The former `sites` command is retired. `tables` and `report` write the exact matcher's repository and call-site
groups (`scope: repo` / `site`) into the same `groups-exact-run<i>.csv`; `--window` controls the line distance
(default 2). See the matcher description below.

The commands write to the experiment's folder on the volume:

```
<exp>/_ANALYSIS/
├── _SUMMARY.md                            # report: Markdown tables of everything below, for reading
├── micro-run1.json                       # micro: per (project, tool), status, verdict, recalls, precision, F1, hits, misses, false sites and decoys
├── runs.csv                              # tables: one row per stored run (all repetitions)
├── runtime.csv                           # tables: per tool and language, how long its scans take (RQ1)
├── tools.csv                             # tables: per tool that ran, vendor, project page, how it is shipped, engine and approach
├── groups-exact-run1.csv                 # tables: exact matcher, one row per group (repo scope per algorithm, site scope per call site)
├── members-exact-run1.csv                # tables: one row per (group, record)
├── agreement-exact-run1.csv              # tables: one row per (group, dimension): voters reporting / agreeing, majority value
├── groups-raqun-run1.csv                 # tables: RaQuN's groups (with members-/agreement-raqun-run1.csv), unless --no-raqun
├── matchers-run1.csv                     # tables: exact vs RaQuN per repository (co-membership pair Jaccard)
├── matchlevels-run1.csv                  # tables: RQ3, per matcher and level: items by voter groups, agreement per dimension
├── score-run1.csv                        # score (RQ4): per tool, dimension, level: items, confirmation [CI], exclusivity, rank, Dawid-Skene
├── loo-run1.csv                          # score (RQ4): ranking without each voter group, Kendall tau vs the full ranking
├── grouping-run1.csv                     # score: the ranking under other voter groupings, Kendall tau
├── stability.csv                         # score: per (tool, snapshot) with repeated runs: Jaccard across runs, records CV, consensus
├── micro-run1.csv                        # tables: one row per (micro project, tool); only with micro projects
├── micro-items-run1.csv                  # tables: one row per (micro project, tool, expected item)
├── records-run1.csv                      # tables: one row per normalised record
├── units-run1.csv                        # tables: per tool assets, records, items and how they relate (packaging)
├── units-repo-run1.csv                   # tables: per (tool, repo) assets, records, items
├── tables-run1.json                      # tables: settings used, row counts, versions, the voter map (group, engine per tool)
├── normalize.json                        # normalize: diagnostics per tool over every run (sources, spec issues, conflicts)
├── schema-run1.csv                       # schema (RQ2): one row per CBOM: declared version, valid, errors, error kinds
├── meaningful-run1.csv                   # schema (RQ2): per tool and dimension, assets with a value from standard fields / any
├── traceability-run1.csv                 # schema (RQ1): per (snapshot, tool): located, existing, in range, strong
├── levels-run1.csv                       # levels: confirmed share per tool at normalisation levels L0-L4
├── items-run1.csv                        # levels: which items each tool reports per repository, with the voter groups matching at each level
├── coverage-run1.csv                     # tables: fill rates per tool and dimension, and what the assets' names are
├── conformance-run1.csv                  # tables: spec issues per tool and kind
└── vocab.csv                             # normalize: what every raw value mapped to, per tool and source
```

**`tables <exp>`** writes flat, long-format CSV files (UTF-8, comma-separated, lists inside a cell joined with `;`)
and runs the **exact matcher** (`analysis/matchers/exact.py`, matcher (b)) at every ladder level: its groups are the
clusters of the ladder, one per key at the level (the records whose identity at that level or below is the key,
plus records attached by vocabulary knowledge: a generic report such as SHA-2 next to SHA-256, or with `--closure` a
record implying the algorithm), so tools without locations take part; clusters never merge through a record with
several keys, and a group whose members span several L4 identities (`units`, `n_units`) shows what the level declares
the same; records without an L4 identity (`resolution` undefined, api, non-crypto, ambiguous) are not matched. The
full group, member and agreement tables are written at the scoring level (`--level`, default L4), the
per-level summary `matchlevels-run<i>.csv` for all five levels (L0-L4). Inside a group the located records form call-site groups
(same file, lines within `--window`, default 2; a window record spans `line` .. `line_end`). Per group and dimension
(key size, mode, padding, curve, digest, location) the agreement table says how many voters report a value admitted
at the level and how many carry the majority value. A member whose own identity differs from the group's key joined
through a lower level (`matched`); `--closure` lets a record join the groups of the algorithms it implies (`implied`
member rows). The settings are recorded in `tables-run<i>.json`. The tables come in three layers, so further matchers only add
files and never change these:

| File | Layer | One row per | Main columns |
|---|---|---|---|
| `runs.csv` | runs | stored run, every repetition, incl. failed, timed-out and `unsupported` | repo, snapshot, commit, variant, variant_commit, language, repo_files, repo_source_files, repo_bytes and repo_source_bytes (the snapshot's files and bytes, all and those of its language, at the pinned commit: from the record, else the two counts from the snapshot files; empty only for an older record on a copied export without `--fetch`), tool, run, tailored (the tool was tuned on this repo), status (`ok`, `error`, `timeout`, `unsupported`, `oom`: stored ok but the container was OOM-killed meanwhile, so the analysis reads no CBOM from it), duration_sec, size_bytes, error (the most telling line), peak_memory_mb, memory_limit_mb, limit_hits, oom_kills, memory_failure (for a run that failed, error, timeout or oom, whether it may have failed for memory and why: `oom kill`, `out of memory` (its error says so: Java's OutOfMemoryError, CBOMkit's backend_oom, a runtime's "out of memory"), `at limit` (it reached the container's limit); else empty), cpu_seconds, cpu_cores, enqueued_at, started_at, finished_at, queue_sec, received_at, tool_version, image_ref, bfcbom_commit, bom_format, spec_version (as the CBOM declares), components, crypto_assets and, by the declared `assetType`, algorithm_assets, certificate_assets, protocol_assets, material_assets, other_assets, plus located_file_assets and located_line_assets (crypto assets with a file, and with a file and a line, in the CBOM), expect_crypto (from the spec), workaround (recorded setup workarounds, such as CipherRadar `.cradarignore`); for LLM workers requests, requests_failed, chunks_total, chunks_sent, chunks_skipped (over `LLM_MAX_CHUNKS`, never sent), input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, reasoning_tokens, models_answered, cost_usd, priced_model, price_period, price_table_version |
| `runtime.csv` | runs | tool and language, plus `all` | repos, runs, timeouts, timeout_share, median_sec with median_ci_low / median_ci_high (bootstrap over repositories, 1'000 resamples, from 3 repositories), p90_sec, max_sec (slowest single ok run), spread_cv (CV of the repositories' times), rerun_cv and rerun_pairs (median CV between repeated runs of one snapshot), hours (every run that ran), sec_per_1k_source_files and size_rho (Spearman between time and source files), both only with 3 or more repositories whose sizes differ; a repository's time is the median of its ok runs, all variants and repetitions, so it counts once; peak_median_mb with peak_ci_low / peak_ci_high (bootstrap, as for time), peak_p90_mb and peak_spread_cv over the repositories (a repository's peak is the largest working set of its runs), peak_max_mb; cpu_sec_median and cores_median, each with _ci_low / _ci_high, _p90 and _max (over the ok runs, a repository's value the median `cpu_seconds` or `cpu_cores` of its runs: the CPU time summed over the cores, and that over the wall time; empty where the workers did not record them); for checking the limits: limit_mb, at_limit (runs with limit_hits > 0; empty where the workers did not record it), oom (runs with an OOM kill), memory_failures (runs with a `memory_failure`); unsupported runs are out. The summary shows time, memory and CPU in RQ1's "Run time, memory and CPU" and the limits apart, in the preamble's "Memory limits", with every run that may have failed for memory listed |
| `tools.csv` | `common/tools.yaml` | tool (those that ran) | vendor, url, shipped (CLI, server with an API, GitHub Action, HTTP API), approach (a third-party engine named first, else `own engine`, then how it detects); the summary's "Tools" |
| `records-run<i>.csv` | records | normalised record: one (component, occurrence) of one tool | snapshot, variant, tool, run, location_kind (`line`, `window`, `import`, `file`, `none`, `unread`), record_id (`<tool>:<component>:<occurrence>`), site_id (`<tool>:<file>:<line>` when located), every record dimension, resolution (`mapped`, `ambiguous`, `api`, `non-crypto`, `undefined`; only `mapped` records become items), `raw_*`, `src_*`, unmapped, conflicts, spec_issues |
| `groups-exact-run<i>.csv` | matches | group of the exact matcher: `repo` scope = one cluster per key at the level (its members plus attached records), `site` scope = its located records at one call site | matcher, repo, snapshot, commit, variant, run, group_id (`E00001`, clusters sorted by key; sites `E00001.001`), n_tools (tools; k counts voter groups: `voter_groups`), tools, language, level, scope, key (the level's key: a raw name at L0, an identity at L4), voters, voter_groups, member_voter_groups (voter groups with a record of their own in the group, attached records aside; 0 = the group is no item, the rule of `matchlevels-run<i>.csv` and the summary's "Per language"), n_records, n_sites, units (L4 identities of the members), n_units, n_attached, located_voters, colocated_voters (repo scope), file, line_min, line_max, parent (site scope) |
| `members-exact-run<i>.csv` | matches | (group, record) pair | matcher, snapshot, variant, run, group_id, record_id, tool, unit (the record's L4 identity), implied (attached through `--closure`), matched (its own identity differs from the group's key: joined through a lower level), attached (a generic or implying record, no key of its own in the group) |
| `groups-raqun-run<i>.csv`, `members-raqun-run<i>.csv`, `agreement-raqun-run<i>.csv` | matches | RaQuN's groups (repo scope only; `n_elements`, `similarity` = Jaccard of the attribute sets) | as the exact tables |
| `matchers-run<i>.csv` | matches | (repository, level): exact vs RaQuN | repo, snapshot, variant, run, level, matcher_a, matcher_b, records_common, groups_a, groups_b, pairs_a, pairs_b, pairs_both, pair_jaccard, a_in_one_b, b_in_one_a |
| `score-run<i>.csv` | scores | (dimension, tool) at the matcher's level | dimension, level, tool, voter_group, repos (with items), items, confirmed, rate (mean of the per-repository confirmation rates), rate_pooled, ci_low, ci_high (bootstrap of the mean over repositories; only with enough support), exclusive, exclusivity, majority_items, majority_covered, coverage (of the repository's majority items, those at least half of the voter groups that ran there report, the share the tool reports, mean over repositories: a recall proxy), rank (only with items in ≥ 3 repos and ≥ 10 items), ds_support, ds_sensitivity, ds_specificity (algorithm dimension only) |
| `loo-run<i>.csv` | scores | (dimension, left-out voter group) | dimension, level, left_out, kendall_tau, ranking |
| `grouping-run<i>.csv` | scores | (dimension, voter grouping) | dimension, level, grouping (`declared`, `per-tool`, `llm-merged`, `engine-merged`), voter_groups, ranked, kendall_tau (against `declared`), ranking: the sensitivity checks of the voter groups (`analysis/README.md` "Voter groups") |
| `stability.csv` | scores | (tool, snapshot, variant) with >= 2 runs | repo, snapshot, variant, tool, level, runs, records_mean, records_cv, items_mean, jaccard_mean, jaccard_min, flux (the share of the tool's items on that snapshot not in every run: the noise floor of RQ5's changes off the edit), consensus, unstable |
| `mutants.csv` | RQ5 | mutant (mutants.json) | repo, snapshot, commit, language, variant (the mutant id), variant_commit, snippet, style, catalog_version, unit (expected L4 identity, `protocol:TLS` for the TLS context), family, expected, also, file, call_line, related_lines, spans (`kind:start-end`), host, host_line, host_callers, host_entry, imports_added, qualified (Java types written fully qualified), patch |
| `mutation.csv` | RQ5 | (mutant, tool) | repo, snapshot, language, variant, snippet, style, tool, voter_group, status (`ok`, or why it is not judged: the mutant's or the original's run missing or not ok), runs_mutant, runs_original, unit, masked (the original already has the unit: no repository-level value), found_repo, found_file, found_span (inserted lines ±2), found_site (call line ±2), found_group (lenient: same group at the site), key_size, mode, padding, curve, digest, iterations, protocol_version (the expected value on a new record of the unit on the inserted lines; empty = not judged), extra_units, lost_units, extra_sites, lost_sites (other changes; majority sets), new_records (per run: records absent from the original at the same place, the original's lines shifted into the mutant; a record is matched by its unit, or by its raw name when it has none, so a tool's identity-less import markers are not new on every mutant). Only new records count: an original finding moved by the insertion is not new; with repetitions a value holds in most of the mutant's runs |
| `mutation-rates.csv` | RQ5 | (tool, language, style or `all`, dimension) | tool, voter_group, language, style, dimension, repos, mutants (judged), detected, rate (mean of per-repository rates), pooled, ci_low, ci_high (bootstrap over repositories, only when supported: score.py's MIN_REPOS and MIN_ITEMS), supported |
| `mutation-scores.csv` | RQ5 | (tool, language, style or `all`) | tool, voter_group, language, style, repos, calls, found_site, found_group, decoys, decoys_hit, recall_site, recall_low, recall_high, recall_group, precision, f1, f1_low, f1_high, supported, offedit_mutants and offedit_share (judged mutants, calls and decoys together, with a new unit or a new located site outside the inserted lines; never folded into recall, precision or F1), extra_units_median and extra_sites_median (over those mutants), baseline_flux and baseline_changed (the tool's noise floor from `report --baseline <stability experiment>`: its mean `flux` there and the share of its snapshots whose repeated runs differ; empty without one) |
| `bridge-run<i>.csv` | RQ5 -> RQ4 | (language, tool) cells and per language `all` | language, tool, confirmation, f1, n, spearman, kendall_tau |
| `agreement-exact-run<i>.csv` | matches | (group, dimension) | matcher, repo, snapshot, variant, run, group_id, scope, key, dimension (key_size, mode, padding, curve, digest; location for repo groups), reporting (voters with a value), agreeing (voters carrying the majority value), majority, values |
| `matchlevels-run<i>.csv` | matches | (matcher, level) | matcher, level, run, repos, items (algorithms by L4 identity), records, k1, k2, k3, k4plus (items by the voter groups of their largest group at the level), share_k2plus, `<dim>_groups` and `<dim>_agree` for key_size, mode, padding, curve, digest (groups with 2+ voters reporting a value admitted at the level; share where all agree), location_groups, location_agree (groups with 2+ located voters; share with 2+ at one call site) |
| `micro-run<i>.csv` | ground truth | micro project and tool of the spec, also unsupported and failed runs | project, language, tool, run, tailored, status (of the run, `missing` without one), verdict (`pass`, `fail`, `unsupported`, `error`, `missing`), n_items, strict, lenient, repo, recall_strict, recall_lenient, recall_repo, sites_in_file, sites_correct, precision, f1, bar_items, passes_bar, bar_verdict (`bar passed`, `bar failed`, `not judged`, over all scored projects of the tool), missed, repo_missed, repo_extra (families reported that the project does not contain), decoys (count), decoys_hit (ids) |
| `micro-items-run<i>.csv` | ground truth | (micro project, tool, expected item) of every scored run | project, language, tool, run, item, families, insertion_style (`direct`, `indirect`), callsite_strict, callsite_lenient, repo |

Join keys: records on `(snapshot, variant, run, record_id)`, groups on `(snapshot, variant, run, group_id)`. Group ids
restart per snapshot and variant. `snapshot` is the result folder name `<owner>__<repo>@<sha12>`; `repo` and `commit`
carry the full name and SHA.

```python
import pandas as pd
records = pd.read_csv("_ANALYSIS/records-run1.csv")
members = pd.read_csv("_ANALYSIS/members-exact-run1.csv")
groups = pd.read_csv("_ANALYSIS/groups-exact-run1.csv")
keys = ["snapshot", "variant", "run"]
df = records.merge(members, on=keys + ["record_id", "tool"]).merge(groups, on=keys + ["group_id"], suffixes=("", "_group"))
groups.query("scope == 'repo'")["voter_groups"].value_counts()   # voter groups per item
groups["tools"].str.split(";")          # list cells

micro = pd.read_csv("_ANALYSIS/micro-run1.csv")
micro.pivot(index="tool", columns="language", values="lenient")      # the inclusion matrix
items = pd.read_csv("_ANALYSIS/items-run1.csv")
items.drop_duplicates(["snapshot", "variant", "unit"])["k_L4"].value_counts()   # voter groups per item at L4
```

**Export.** The analysis files live inside the experiment folder, so the normal export takes them along together with
the record, every CBOM and its meta file. The export runs `analysis report` first, so the tables and
`_ANALYSIS/_SUMMARY.md` in the zip match the stored runs (`ARGS=--no-report` skips it):

```
make experiment-export NAME=stability-pilot   # -> experiments/results/stability-pilot.zip on the host, open stability-pilot/_ANALYSIS/_SUMMARY.md
```
