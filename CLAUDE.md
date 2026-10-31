# CLAUDE.md in BF-CBOM git repo

Shared context for coworkers and for Claude Code (which loads this file automatically). Details live elsewhere:
`README.md` (for newcomers: what BF-CBOM is, the tools, setup, a map of the other READMEs), `docs/findings.md`
(results so far), `experiments/README.md` (running experiments: every make command, specs and their format, corpus
sampling, mutants, memory and parallelism, the analysis commands and their tables),
`analysis/README.md` (how the analysis reads CBOMs: units, normalisation, what a spec may contain, the ladder by
example, voter groups, how family and group are argued),
`analysis/report/README.md` (plot recipes), `analysis/data/CHANGELOG.md` (vocabulary versions),
`docs/pilot-2026-10-08/` (the colleague's pilot).

## Rules
- **Current scope and wording (2026-10-22, bar 2026-10-23, CBOMkit 2026-10-30):** describe BF-CBOM publicly as
  integrating **14 CBOM generator tools**, CBOMkit in both of its official distributions (server and GitHub Action,
  15 workers), of which **10 are eligible** after the bar exam (the specs name them by hand). Both CBOMkit
  distributions passed with identical scores; the later specs run the server only (`cbomkit`: one failed run fewer
  on corpus-pilot-medium, 58 vs 57 of 60, otherwise identical results, although at about twice the RAM, peak 13 vs
  6.8 GB; decided 2026-10-30). LLM providers are
  excluded for now and may be considered later; their existing implementation is retained. Do not define the tools
  by contrast with LLMs. Research roles stay in the research docs.
- **Start with make, never plain compose.** `make up-prod` starts the stack detached and opens the live board
  (`misc/cli/stack_cmd.py`; Ctrl-C closes it, the stack keeps running; `make board` reopens it, `make logs` shows the
  raw logs, `make down` stops the stack); clean rebuild `make clean-docker && make up-prod
  NO_CACHE=1`. Never run plain `docker compose up` or `docker compose run`: the Makefile exports the image tag
  (`VERSION`) and `BASE_IMAGE`; without them compose looks for `:latest` images and tries to build from a nonexistent
  `base`. Every runner command has a target: `experiment`, `experiment-list` / `-status` / `-collect` / `-cancel` /
  `-delete` / `-export` / `-export-all` (every complete experiment), `analysis`, `corpus`, `corpus-prune`, `board`; `experiment-attach` joins the terminal of
  the running `experiment run` from another shell (detach with Ctrl-P Ctrl-Q, never Ctrl-C). After every pull run `make up-prod` first: the runner
  bakes the CLI and the workers bake `common/`; the board's header names the checkout's commit, its `build` column
  each worker's (read from the containers, `BOARD_BUILDS`), and a red line every container built from another commit;
  `make experiment` refuses to start when a worker of the spec or the git server was built from another commit than
  the runner (`EXPERIMENT_BUILDS`, recorded per session as `builds`; `foreign_builds`). Until 2026-10-31
  `SERIAL_BUILD=0` built no prod worker (its `--profile` flags replaced `COMPOSE_PROFILES`).
  corpus-pilot-medium of 2026-10-30 ran the workers of 4c87e32 under the runner of 5efece4 (no CPU recorded).
- **Nothing lands on the host unless exported.** Results and git mirrors stay in the named volumes `results-data` and
  `git-data`; `make experiment-export` exports experiments and `make corpus-export` exports sampled corpus YAMLs,
  both into `experiments/results/` on the host. These CLI commands run inside the `runner` container.
  An unzipped export can be analysed on the host:
  `RESULTS_DIR=experiments/results uv run bf-cbom analysis report <exp>`.
- **The laptop prepares, the VM runs.** Batch runs happen on the VM (64 CPUs, 122 GiB RAM since 2026-10-29, per the run
  logs; 60.5 GiB before) with `EXPERIMENT_MEMORY_BUDGET_GB=0` for now (one wave). Limits are caps, not reservations; the measured peak with all 15 tools at once was 44.8 of
  60.5 GiB (corpus-pilot-medium, 2026-10-23). Current limits are in `common/tools.yaml` and `docker-compose.yml`;
  since 2026-10-30 every worker reaches its peak comfortably, because the paper reports peaks and never limits
  (CBOMkit 20 GiB with `-Xmx16g`, the heap being its binding limit; CipherRadar 12; xgrep 8; the trials' highest
  peaks were 12.8, 7.3 and 3.0). corpus-main is to run with `REPLICAS="cbomkit=2"`: with the services, the summed caps
  are 115 of 121.5 GiB, so every job can reach its cap at once (decided 2026-10-30).
  The laptop is for code, configs and analysing copied exports.
- **Results are disposable until the paper's runs.** One code path per feature, no compatibility with older result
  layouts; old experiments are rerun, not migrated.
- **Benchmark every tool as shipped, in its best fair run** (refinement 15). Fix our setup, never the tool.
- **Tool versions (decided 2026-10-30, the paper's rule):** for each tool we used the latest stable release published
  by 30 October 2026, excluding pre-releases and nightly builds; for tools without any stable release, the default
  branch at its last commit on that date; engines bundled by a tool's own build stay at the version the tool pins
  (crypto-finder's OpenGrep 1.22.0), engines a tool pins none of follow the rule too. The table is
  `docs/tool-versions.md` (the paper's version table); a newer version means moving the cutoff for every tool and
  rerunning bar-micro. Every pin is exact: a release version plus the sums of its checksums file (an npm package: its
  `dist.integrity` as hex), an image digest, a full commit SHA.
- **Pinned builds:** base images, uv, git sources and CodeQL packs are pinned. Never pin an old dependency to dodge a
  bug; fix our code. Bumping: an image digest from `docker buildx imagetools inspect <image:tag> --format
  '{{json .Manifest.Digest}}'`, a git ref (`*_REF` ARG) from `git ls-remote <url> HEAD`, `CODEQL_VERSION` together
  with its packs (versions in `github/codeql` at tag `codeql-cli/v<version>`, `<lang>/ql/src/qlpack.yml`), pinned
  requirements (`workers/cryptobomforge/`) by editing `requirements.in` and rerunning the `uv pip compile` line
  atop `requirements.txt`, PyQuN by its tag in `pyproject.toml` (`[tool.uv.sources]`, then `uv lock`); rebuild and
  test after every bump.
- **The vocabulary is frozen** (refinement 14): `analysis/data/algorithms.yaml` and `analysis/adapters/*.yaml` change
  only with a version bump and a reason in `analysis/data/CHANGELOG.md` (`tests/test_vocab_frozen.py`). The pilot
  corpus is the development set; ICPC and corpus-main are held out.
- **Never force a tool's output into the "right" class** (decided 2026-10-19). The tools' heterogeneity and their
  oddities are the findings; the L0-L4 ladder keeps the reality (L0/L1) next to the best-effort normalisation (L4),
  and a record's `resolution` only says what a name is (an algorithm, a list, a code identifier, a non-crypto rule,
  unreadable), never what it "really" meant. A spec holds the tool's output structure and the tool's own labels read
  as they say (with their source); what the tool's metadata classifies is read structurally (securin-xbom's rule
  path); code identifiers are recognised by shape; no list of one tool's output strings, no judgements (the
  `interpretive` mechanism exists in the engine, no spec uses it, a test enforces this; the former level L4lit went
  with the last judgement entry). A tool's field that contradicts its own name is a recorded conflict, not an error
  to repair. Full policy and a worked ladder example: `analysis/README.md`.
- **Lint, format, tests:** `uv run ruff check --fix`, `uv run ruff format`, `uv run pytest` (CI runs all three).
  `uv add` / `uv remove` re-sync the venv exactly and drop optional extras installed there (`uv sync --extra raqun`
  restores RaQuN's); `uv run` does not.

## How it works
- **Coordinator and workers.** Streamlit UI on :8501 (`coordinator/`); Redis is the job queue (`jobs:<worker>` /
  `results:<worker>`); one Docker image per generator (`workers/<tool>/main.py`) on `common/worker.py:run_worker`. A
  worker runs each job in a killable subprocess (hard timeout), publishes `worker:<name>:alive` heartbeats (state,
  the job with its start, the container's memory and limit, the last result; read by the UI and the board), marks the
  job running via the routing hash `jobs:routing` (`common/jobs.py:mark_job_running`; a job the hash no longer lists,
  cancelled or deleted while queued, is skipped) and pushes the `JobResult` to
  `results:<name>`; the coordinator ingests via `coordinator/redis_io.py:ingest_worker_results`; experiment jobs are
  routed to `results:exp:<name>`.
- **Job states:** held, queued, running, completed, failed, timeout, cancelled, unsupported; one table
  (`common/utils.py:_STATUS_META`) feeds every page. A stored `ok` run whose container was OOM-killed meanwhile reads
  as `oom` for the runner and the analysis alike (`common/experiment.py:stored_status`; `--retry-failed` redoes it,
  the report lists it and reads no CBOM from it). `unsupported` = the repo's language is outside the tool's
  `languages`, or outside the spec's `tool_languages` for it (`common/jobs.py:tool_supports`), decided at issue time in the UI or recorded as a meta file by the
  runner (`common/experiment.py:mark_unsupported`; `--run-unsupported` disables it).
- **Batch experiments.** `make experiment EXP=experiments/<x>.yaml` runs `bf-cbom experiment run` in the runner
  (`misc/cli/experiment_cmd.py`, logic in `common/experiment.py`): it pins every repo's commit, mirrors it to the
  `gitserver` service (one served repo `<owner>/<repo>--<sha12>.git` per snapshot, because CBOMkit only checks out a
  clone's default branch; a mutant is a branch `bfcbom/<id>`; the base mirror in `git-data` is cloned on first use
  and fetched when the pinned commit is missing, each announced by a line, and nothing else goes over the network,
  so a rerun pins 60 repos in seconds; a git call that stalls for 15 min fails the step with that reason,
  `_git`; nginx with 16 fcgiwrap processes, so clones run side by side), enqueues through the normal Redis protocol,
  every tool's queue largest repositories first (`source_bytes`, `plan_jobs`), and stores the results on
  `results-data`. A spec's `mutation:` block makes every repo's variants the original plus
  generated mutants, planted calls and planted decoys (`mutation/mutants.py`, Phase 4), pinned in `mutants.json` like
  the commits in `_experiment.yaml`; `tool_languages:` narrows a tool to some languages in this experiment, a hand edit (after reading the
  bar-micro report, say; no code reads the verdicts). Resumable: a run with a stored meta file is skipped, `--retry-failed` redoes the runs that are not ok,
  `--rerun <tools>` every run of those tools, `--overwrite-old` the whole spec as if it had never run (the
  experiment folder goes with everything in it, record, run log and `mutants.json` included, and its jobs are
  cancelled, like `experiment delete`; `common/experiment.py:remove_experiment`). Results are regenerated, never kept:
  the spec pins the commits and the mutants are deterministic, so a rerun repeats them (decided 2026-10-20). A spec entry with `path:` publishes a
  directory as a deterministic commit (`publish_fixture`). Spec format and the list of specs: `experiments/README.md`.
  On a terminal `run`, `collect`, `report`, `export` and `corpus build` show live progress (`misc/cli/progress.py`,
  rich; a run's board has a line per tool with its count, the job it runs and for how long, the queue, the
  failures and its remaining time, `progress.forecast`: jobs left x mean duration, the slowest tool ends the batch;
  the stack board shows the same from `exp:<name>:tools`), with the per-result lines scrolling above; off a terminal
  only plain lines. A table of 50k rows or more is written on a counted line of its own. A step that fails gets a
  red ✗ and a line naming the step, its item and the reason (a pool worker that died: most likely out of memory,
  raise `RUNNER_MEMORY` or lower `--jobs`).
- **Experiment folder:** `_experiment.yaml` (the record: the spec as run, every repo with its resolved branch and
  commit and its size at it (`files`, `source_files`, `checkout_bytes`, `source_bytes`, counted from the mirror, so
  an export carries the run-time table's size), written once the snapshots resolve; a resumed run keeps those commits, so it replaces a lock file;
  the one answer to which repos, tools and repetitions an experiment covers, and the overview and the analysis read
  only the stored runs it names, `iter_results`), `_run-log.json` (runner sessions: times, VM RAM and CPUs, budget, waves,
  peak VM RAM, worker image IDs), `_ANALYSIS/` (with `_SUMMARY.md`; emptied and rebuilt by every `report`),
  `mutants.json` (with a `mutation:` block: every mutant's snippet, style, host, lines, inserts and
  patch), then `<owner>__<repo>@<sha12>/<variant>/<tool>/run<i>.json` plus `run<i>.meta.json`. A folder without
  `_experiment.yaml` is ignored by every command (`experiment list` names it for deletion).
- **Provenance.** `RepoInfo.commit` is resolved at inspection start (`coordinator/redis_io.py:enrich_repo`) and checked
  out exactly by `clone_repo`. `JobResult` carries `tool_version`, `image_ref` (env `BF_CBOM_IMAGE`), `bfcbom_commit`
  (env `BF_CBOM_COMMIT`, stamped into every image as its last layer by make and bake; `-dirty` = uncommitted changes),
  `run_index`, `started_at` / `finished_at`, `notes`, the container's `peak_memory_mb` / `memory_limit_mb` /
  `limit_hits` / `oom_kills` during the job (`common/memory.py`, cgroup v2, sampled every second; `limit_hits` from
  `memory.events` `max`, added 2026-10-30: 0 = the limit never constrained the job; the VM has no swap, so memory
  beyond the limit ends in an OOM kill), its `cpu_seconds` / `cpu_cores` (`cpu.stat` read at the job's start and end;
  cores = CPU time over wall time; added 2026-10-30) and, for LLM workers, `usage`
  (requests, input / cached / cache-write / output / reasoning tokens, the model ids answered with; kept on failed
  runs).
- **Tool registry:** `common/tools.yaml` (loader `common/tools_registry.py`): engine family, `voter_group` (tools sharing
  their detection logic, an engine or a rule set, count as one voter in RQ4; default the tool itself; evidence in
  `analysis/README.md` "Voter groups"), `merge_group`, `engine_family` (a shared pattern engine only, merged in the
  `engine-merged` sensitivity ranking), `languages` (the declared support, as shipped; a spec narrows a tool further by hand with
  `tool_languages`),
  `cbom_by` (who writes the CBOM document: the tool, or BF-CBOM for the LLM workers), status, `tailored_on`,
  `memory_gb` (= the worker's compose memory limit; a test keeps them equal), `vendor`, `url`, `shipped` and
  `approach` (the summary's "Tools" table, `tools.csv`) and `packaging` (how the tool packages
  its findings into components, a hand label read from its CBOMs, decided 2026-10-30: `per hit`, `per rule`,
  `per algorithm`, `per family`, `no places`, default `unknown`; printed in the summary's "Units per tool" next
  to the measured signature, never derived from it; `analysis/README.md` "Packaging per tool"), `version` and
  `version_url` (decided 2026-10-31: the version the paper reports, the tool's own, its release or 7 characters of
  the pinned commit without one, and the page that shows it, the paper's citation; a test checks the Dockerfile pins
  it; the summary's "Tool versions" links it and checks every run recorded it). No verdict is
  recorded there: the bar's record is the bar-micro export, and nothing in code reads a verdict.
- **Memory budget (waves):** `EXPERIMENT_MEMORY_BUDGET_GB` (laptop default 14, 0 = unlimited) caps the summed
  `memory_gb` of the tools fed at once; the rest run in later waves. Runner and UI plan waves with
  `common/tools_registry.py:plan_waves` (UI: held jobs, `release_due_waves`, `coordinator/scheduler.py`).
  Replicas (built 2026-10-30): `make up-prod REPLICAS="cbomkit=3,..."` starts them (`--scale worker-<tool>=N`);
  every container also writes its own heartbeat `worker:<tool>:replica:<host>` (`common/worker.py:replica_key`), and
  the runner counts them (`live_replicas`) at session start (budget, `_run-log.json` `replicas`, the summary's
  overview) and on every refresh (`progress.forecast` shares a tool's remaining work among its live containers); the
  stack board shows one row per tool (`busy 2/3`, summed RAM). Run times measured with replicas include their CPU
  contention; the replica test (corpus-pilot-medium without and with cbomkit 3, cipherradar 3, scanoss 2, 2026-10-30;
  `docs/findings.md` "Replica test") found CBOMkit's times within the unreplicated tools' noise (median ratio 1.03),
  so corpus-main runs with `cbomkit=2`. It also found the git server serving one clone at a time, so a CBOMkit full
  clone held up every other worker's clone, inside their run times (fixed: 16 fcgiwrap processes), and the corpus
  order starting the longest jobs late (fixed: largest first).
- **Later: memory-gated scheduling** (noted 2026-10-19, not built; the user may move the stack to Kubernetes
  instead). Waves cap the *sum of limits*, so the former 64 GB VM ran at ~21 GiB: each worker runs one job and most jobs use
  a fraction of their cap. The design to turn RAM into throughput: replace waves by an admission loop that dispatches
  a job when host used + the expected memory of the running jobs + the new job's cap fits the budget (expected =
  p95 of the stored `peak_memory_mb` per tool x1.25, else the cap); concurrency from replicas (`experiment plan`
  suggesting `REPLICAS` from stored peaks and serial times); `oom_score_adj` so a host OOM kill hits a
  worker job (recorded as `oom`, redone by `--retry-failed`), never redis or the git server. The corpus sampler
  would then also stratify by number of developers (GitHub contributors) next to language, stars, last push and
  size (size cap, below).
- **Corpus and fixtures** (`experiments/corpus/`, mounted read-only into the runner): `micro/<language>/` the micro projects of
  Java, Python and Go with `micro/ground_truth.yaml`, both generated from the snippet catalog (`mutation/micro.py`,
  checked in, a test keeps them equal; 14 expected calls, each in a function of its own, and 8 decoys per project (4 kinds
  added 2026-10-29); its header states the hit rule, decided 2026-10-22); `pilot-2026-10-08.yaml` (12 pinned real
  projects in Java, Python and Go, the development set; all 16 pilot projects in `pilot-2026-10-08-all.yaml`, read
  by CipherSift's `tailored_on`); `pilot-2026-10-22.yaml` (second pilot: 60 manually selected repos, 20 per paper
  language, default-branch HEADs pinned on 2026-10-22; `experiments/corpus-pilot-medium.yaml`);
  `icpc-2026.yaml` (the 23 ICPC paper repos at HEAD of 2026-10-18, held out);
  `corpus-pc1.yaml` ... `corpus-pc6.yaml` (the stratified corpus built on 2026-10-30, nested draws of 1 to 6 repos
  per cell, 27 to 162 repos; `corpus-main.yaml` runs pc6, decided 2026-10-30) with its frame zipped
  (`corpus-pc6-frame.yaml.zip`); the pilot sets are smoke tests the paper does not mention, so corpus-main's
  overlaps with them stay (skill-scanner is in pilot-2026-10-22, Sa-Token in the ICPC set; decided 2026-10-30);
  `exclusions.yaml` (repos the corpus builder never samples, with reasons). `make corpus` builds a stratified GitHub
  corpus of Java, Python and Go repos (`common/corpus.py`; needs `GITHUB_TOKEN` in `docker/env/coordinator.env`):
  cells of star bin x size bin, each cell's population enumerated completely (paginated, split by creation date above
  the API's 1000) and `--per-cell` repos drawn with the seed (default 3 x 3 cells x 2 = 18 per language, 54 in all;
  `--star-bins`, `--size-bins`; design of 2026-10-29), the frame recorded in the file (`cells` with populations), the
  covariates `crypto_apis` and `topics` recorded per repo, never selected on; the populations and crypto checks in
  `<corpus>-frame.yaml`, reused with `--frame` (no search; 2026-10-30); protocol in `experiments/README.md`
  "Corpus sampling".
  Repos above `CORPUS_MAX_REPO_SIZE_MB` (default 1536, `common/corpus.py:DEFAULT_MAX_REPO_SIZE_MB`: a quarter of the
  git server's 6 GB when the paper's corpus was built, so that CBOMkit's full clone always fit; the git server has
  16 GB since 2026-10-30, for the concurrent clones of CBOMkit replicas, and the cap stays) are never sampled and
  listed as excluded with the reason.
- **Analysis** (`analysis/`, offline, reads the results volume only; `make analysis ARGS="<command> <exp>"` writes
  `<exp>/_ANALYSIS/`; `experiment export` runs `report` first):
  - **Units** (`analysis/README.md` "Units"): an *asset* is a CBOM component as the tool wrote it, a *record* one
    occurrence of an asset (the normalised form), an *item* one algorithm (a distinct L4 identity, the *unit*) in one
    repository, per dimension (unit, value) and (unit, file:line). Asset and record counts depend on how a tool
    packages its findings (CipherRadar: one component per finding; CBOMkit: one per algorithm with its
    occurrences); the ladder and the scores count items.
  - `normalize/`: CBOM -> normalised records (below); `normalize` also writes `run<i>.norm.json` next to each CBOM.
    Our reading of a name is bounded (vocabulary 2026-10-23.1): shapes first (a UUID carries nothing, an OID is read
    through `oids:`, a URI through its fragment), a bare number is a key size only next to its family and within the
    family's standard sizes (`key_sizes:`), a parsed or derived parameter the primitive does not have is dropped
    (`admits:`; `engine.py:_admit`), a cipher-suite name yields its bulk cipher with `protocol: TLS`
    (`cipher_suites:`). A tool's own field is never checked or dropped.
  - `schema.py`: RQ2 (whole-document validation, meaningful shares) and RQ1's traceability; `snapshots.py` reads a pinned
    commit's files from the git mirror or a cache fetched with `--fetch` (a fixture: published from its local directory),
    as a checkout shows them, which is what the tools scanned: a symlink inside the tree as its target
    (`--follow-symlinks`; until 2026-10-31 read as the link's one line, so Aegis's linked test files had lines past the
    end) and lines numbered at `\n` only, as git and the tools number them (`split_lines`; `str.splitlines` also broke
    at form feeds).
  - `levels.py`: the L0-L4 ladder on a fixed unit (refinement 14): `level_matches` clusters a repo's records level
    by level; the exact matcher, the ladder and the tools-per-item distribution share it.
  - `matchers/`: n-way matchers on the records, `exact.py` and `raqun.py`, compared by `compare.py` (Phase 3).
    RaQuN (2026-10-21): PyQuN v1.0.1 (MIT) from the user's fork (`RaQuN_Lab`), the `raqun` extra pinned by its tag in
    `[tool.uv.sources]` (commit in `uv.lock`), installed by the runner image and CI; the misc-pyqun image (the UI's
    similarity worker, no `analysis/` code) is built from `base` like every worker, with the `raqun` and
    `similarity` (sentence-transformers) extras. Release 1.0.0 fixed
    PyQuN's Stopwatch (our copy went), `MatchCandidates.sort` (it dropped the result of `sorted(...)`, so greedy
    matching took the candidates in set order, not by similarity), the shared default objects and the iteration
    order, so the groups are deterministic; matcher (c) numbers from before 2026-10-21 came from the unsorted
    candidates and are rerun, not compared. RaQuN per level costs five matchings per repository (`--no-raqun`
    skips it; running it at the scoring level only is not built). Each matcher's seconds per repository and level
    are recorded (`matchers-`, `matchlevels-run<i>.csv`; the summary's "Matching time" table), measured around the
    `match` call alone, so RaQuN's n-way matching and the exact matcher's clustering are compared on cost too.
  - `score.py`, `stability.py`, `costs.py`: RQ4's per-tool rates (no ranking), run-to-run stability, LLM costs from
    `analysis/data/llm_prices.yaml`.
  - `mutation.py`: RQ5, each tool's recall on the planted calls, precision on the planted decoys and F1, and the
    bridge to RQ4 (Phase 4); every agreement analysis (RQ1-RQ4, stability) reads the original variants only, so a
    mutated repo is never counted twice.
  - `rqs.py`: the five research questions (RQ1 volume to RQ5 detection), the bar exam
    as a preparatory step, defined once (id, title, question,
    the tables that answer each); the
    summary's headings and its "Research questions" section come from it, and a test keeps this file's RQ section
    equal to it.
  - `groundtruth.py`: micro recall at call sites and at repo level, precision (over the places a tool reports, decoys
    included) and F1, the bar (`BAR`: half the calls, lenient, in one language) and each tool's verdict
    (`bar passed`, `bar failed`, `not judged`: `bar_verdict`), field-by-field comparison with `expected`.
  - `tables.py` (flat CSV tables), `summary.py` (`_SUMMARY.md`, rendered from the CSVs only). RQ1's "Places per
    tool" ends with `outside code`: the share of a tool's located algorithm records whose file is not a source file
    of the repo's language (`in_scope`; fixtures, test vectors, configuration); the analyses count those places like
    any other (decided 2026-10-23: the tools are benchmarked on the repositories as they are, no scope filter), the
    share is for the discussion.
  - **Summary layout** (decided 2026-10-31): every subsection is its heading, one to three sentences, the table,
    its visible checks (✅ ⛔ a verdict, ⚠️ a caveat), then collapsibles below in this order: `📋 Columns` (one
    bullet per column, a homogeneous series such as `L0` … `L4` in one), `💡 Example`, `📖 <topic>` (background),
    `🗂️ <list> (N)` (run lists with the count visible above, long per-repository tables, mutant patches). The
    Markdown source has one sentence per line and no semicolons in prose or cell joins (data keeps its own, in
    code), no e.g./i.e., links instead of quoted section names; backticks for names, fields, statuses and every
    value a tool wrote, bold for defined terms. Build blocks with the helpers (`_text`, `_columns`, `_example`,
    `_background`, `_check`, `_run_list`, `_link`); `summary.style_issues` checks the rules and the tests run it.
  - **In parallel** (2026-10-23): `report` normalises, validates and matches in a process pool, one CBOM or one
    repository per task (`misc/cli/parallel.py:pmap`, the tasks in `analysis/tasks.py`; `--jobs`, env `BF_CBOM_JOBS`;
    0 = auto, min(CPUs, 8) for a step with 16 or more items, else sequential). The workers are spawned, never forked
    (rich's thread, the store's `git cat-file` pipes), load the vocabulary themselves and return plain dataclasses in
    input order, so every file is byte-identical to `--jobs 1`, the matchers' timings aside (a test checks it). Memory bounds the pool, not CPUs:
    the runner's cap is `RUNNER_MEMORY` (Makefile, 8g; 2g was OOM-killed on corpus-pilot-medium; on the VM
    `make experiment-export-all BF_CBOM_JOBS=32 RUNNER_MEMORY=96g` with no experiment running), and the snapshot store keeps at most 64 MB
    of file lines.
  - Commands: `report` (everything, one pass), `normalize`, `schema`, `levels`, `micro`, `tables`, `score`. The UI's
    Experiments page shows stored experiments read-only.
- **Normalisation, three kinds of files.** One **adapter spec per tool** `analysis/adapters/<tool>.yaml` (declared
  format, which components are findings, where each dimension sits in order of precedence, the tool's own dictionary,
  named extractors; inherits `_cyclonedx.yaml`, `_ibm-cbom-draft.yaml`, `_cbomkit.yaml`, `_llm.yaml`); one **shared
  vocabulary** for spellings traceable to standards or official APIs (`analysis/data/algorithms.yaml` plus the
  vendored CycloneDX 1.7 registry and 1.6/1.7 schemas in `analysis/data/cyclonedx/`), regardless of how many tools use
  them; labels invented by a tool belong in its spec. One **target format**: `analysis/data/cbom-norm.schema.json`.
  A new tool needs a spec (a test fails otherwise). *Worker adapters* (`common/cbom_adapters.py`, `workers/*`) make
  raw output usable CycloneDX at scan time; *adapter specs* read CycloneDX into records, offline.

## Generators
New worker: `workers/<tool>/main.py` with `run_worker`, `docker/Dockerfile.worker-<tool>`, a compose service,
`AVAILABLE_WORKERS` in the Makefile, a `docker-bake.hcl` target, a `tools.yaml` entry, an adapter spec, and a worker
adapter if the native output isn't CycloneDX. Tools that only orchestrate other scanners are not integrated (OQS
Scanner, decided 2026-10-17; pqc-mat, whose code scanner is CodeQL's crypto queries through cryptobom-forge, the
CryptobomForge engine repackaged, decided 2026-10-19). Versions are pinned in the Dockerfiles and recorded per run;
each tool's invocation and caveats are in the table below, the details in its `workers/<tool>/main.py` and
`docker/Dockerfile.worker-<tool>`. **The bar exam decides who plays, by hand** (decided 2026-10-22): a tool is
eligible in the languages where it passed the bar, and a tool below the bar in every language is `bar failed`; the
user reads the bar-micro report and writes that into the specs (`tool_languages:`, the `tools` list). No code
couples the verdicts to the specs and nothing records them but the export. Bar of 2026-10-23: 11 eligible in every
language they were scored in; cdxgen, cryptarium, qelens and qscan failed it. The transitional observer role
(2026-10-19) was removed on 2026-10-30: every tool votes.

| Worker | Voter group | Languages | Invocation and caveats |
|---|---|---|---|
| `cbomkit` | cbomkit | Java, Python, Go | CBOMkit server image, cloned through the git server over TLS; Python covers pyca only; modules need a build file and `src/`; large repos need ~45 min and a 16 GB heap |
| `cbomkit-jar` | cbomkit | Java, Python, Go | cbomkit-action, source only, the same engine; runs on bar-micro only since 2026-10-30 (the server's results were the same, with one failed run fewer); the indexing risk (build file without `src/`) is noted, `CBOMKIT_ENSURE_SRC=1` stays off |
| `cdxgen` | cdxgen | Java, Python, JS, Go | failed the bar (2026-10-23: 0 / 0 / 6 of 14); `--include-crypto --evidence`, `-t python` / `-t go` for those repos (the `cbom` command finds nothing in Go, `--profile research` adds nothing; checked 2026-10-22); algorithms carry locations only for JS/TS (occurrences) and Go (property `cdx:golem:crypto:sourceLocation`, one per asset); Go analysis type-checks, so a module's imports resolve only with its `go.sum`, network and a writable module cache (a GOPATH per job: under `cap_drop` root cannot create the image's; until 2026-10-22 every Go repo with a dependency gave nothing); Java code yields none (atom has no crypto sinks); 24 GiB (OOM-killed at 8 on Chat2DB, bisq, bisq2, kafka; at 16 on 8 of 60 pilot-medium repos, 2026-10-23) |
| `cryptobomforge` | cryptobomforge | Python, C | CodeQL's crypto inventory queries, C with `--build-mode none`; writes IBM's draft `1.4-cbom-1.0`, not CycloneDX, also when CodeQL finds nothing (every stored CBOM is the tool's; the worker's placeholder for that case went 2026-10-20) |
| `deepseek`, `chatgpt`, `claude` | one each, `merge_group: llm` | all five | `common/llm_cbom.py` (below), CycloneDX 1.7 assembled by us; `chatgpt` / `claude` need `make up-prod EXTRA_PROFILES=llm` and a key in `docker/env/worker-<provider>.env`; a request that fails after the provider's retries fails the job (no partial CBOMs), `LLM_MAX_CHUNKS` 1000, `LLM_CONCURRENCY` 4 |
| `cryptoscan` | cryptoscan | eleven | always `--include-quantum-safe`; skips folders named demo, example, sample; 1.4.0 panicked on specific files and the run failed (its `--exclude` could not name one exact file, so no workaround; `docs/findings.md` "Interventions"); 1.5.0 (pinned 2026-10-30) says such lines no longer end the scan and skips vendor, venv and target folders by default; on corpus-pilot-medium 1.5.0 failed on no repo (1.4.0: 9) |
| `pqcheck` | pqcheck | Python, Go | |
| `qdarpan` | qdarpan | Java, Go, C | the worker adapter moves `qdarpan:location.N` into `evidence.occurrences` |
| `securin-xbom` | securin-xbom | Java, Python | the registry rule sets it fetches online, frozen unfiltered at a semgrep-rules commit |
| `cipherradar` | cipherradar | Java, Python, Go, JS | runs outside the clone; Java files with its crash trigger pre-excluded via `.cradarignore` (recorded); 12 GiB since 2026-10-30 (killed at 4 on pycryptodome, bunkerweb, pyca/cryptography, 2026-10-23; highest trial peak 7.3 at 8) |
| `scanoss` | scanoss | Go, C, JS, Rust | crypto-finder with the public open rules; Java/Python rules need an API key (research key requested) |
| `ciphersift` | ciphersift | Java, Python, Go | benchmark-informed: `tailored_on` the micro projects and the pilot corpus (refinement 13) |
| `xgrep` | xgrep | Java, Python, Go, JS | 0.101.0 (pinned 2026-10-30) also reports key and certificate files (`.pem`, `.crt`, `.p12`, ...): on corpus-pilot-medium its records went from 5.7k to 38k, half of them outside code (0.86.0: 0.03), at 3.3 times the time. Two limits of its own, read from the binary (2026-10-31), defaults kept as shipped: a 500 ms wall-clock limit per regex match that counts as "no match" (`XGREP_REGEX_MATCH_TIMEOUT`), so a loaded host can drop findings in large files (FairEmail: 11 places in its two largest files missing in the replica test's busier run, same image); and a graph budget above which it skips its cross-file analysis (`XGREP_MAX_GRAPH_FILES`, `XGREP_MAX_CROSSFILE_NODES`, 0 forces it), which the worker records from its stderr (`tool warning:` notes, runs.csv `tool_warning`) |
| `qelens` | qelens | Python, JS | failed the bar (5 of 14 in Python); crate `cbom` 0.1.2, CycloneDX on stdout; many findings on import lines; category names such as `HASHING (see usage)` stay `undefined` |
| `cryptarium` | cryptarium | Java, Python, Go, JS, C | failed the bar (0 everywhere: no locations); v0.3.0, `-deterministic`; its CBOM has no locations (only its JSON format has them; benchmarked as shipped) |
| `qscan` | qscan | Java, Python, Go, JS, C, Rust, C#, Ruby, PHP | failed the bar (5 of 14 in every language); quantakrypto npm 0.12.0, offline (no `--triage`/`--audit`); quantum-vulnerable crypto only; exit 1 = findings |
| `cryptoscope` | – | Java | blocked: the implementation is not public; artifact requested from Moffie et al. |

---

# BF-CBOM 2.0: ground-truth-free comparison of CBOM generators (journal extension)

## Research questions
The ICPC 2026 paper introduced BF-CBOM and showed that CBOM generators disagree strongly. The journal extension asks
five questions (settled 2026-10-29, the paper's order and wording; the former RQ1, tool selection, became a
preparatory step on 2026-10-30, because it asks about our procedure rather than the tools and its answer rests on our
own bar; defined once in `analysis/rqs.py`, printed into
every `_SUMMARY.md`, and a test keeps this section's ids and titles equal to them). A *finding* is a cryptographic
use a CBOM reports, counted as records and items, never as assets, which depend on each tool's packaging
(`analysis/README.md` "Units").
- **Preparatory step, subject selection (not an RQ):** which CBOM generators for source code exist as of October
  2026, and which of them meet the minimum requirements of this study? In the paper a "Subject selection" part of the
  study design, with the census (the candidates found) claimed as a contribution in the introduction. Two parts: the **tool search** (where and when the candidates were looked
  for, the inclusion requirements, every exclusion with its reason; `docs/tool-selection.md`, to write) and the
  **bar exam** on the generated micro projects (14 planted calls and 8 decoys per language; the bar is 7 of 14 in
  a language, leniently, which needs a located finding; the one filter on the tools, and it names the languages a
  tool is eligible in, which the user writes into the later specs by hand). The result is the verdict table and the
  decoy hits; the micro F1 appears only next to RQ5's. The **eligible** tools (not "winners") generate the CBOMs of
  the **corpus run** (the stratified GitHub sample, `make corpus`), and the remaining questions are asked on those
  CBOMs plus a **mutation run** on a subset (`micro-`, `micro-items-run<i>.csv`).
- **RQ1, volume:** what do the generators' CBOMs contain: how many assets, records and items of which kinds, in what
  detail, and with what traceability to source code? Assets, records and items per tool and repo, the asset types,
  the fill rate per dimension (reported vs derived), locations and whether they hold at the commit (weak and strong
  traceability), names nobody can read (`resolution`); descriptive, more is not better (`runs.csv`, `units-`,
  `coverage-`, `traceability-run<i>.csv`), and what a scan costs: per tool and language the median time over
  repositories with a bootstrap CI, p90, max, the spread across repositories and between reruns, timeouts and the
  scaling with source files, and the peak memory, CPU seconds and mean cores per tool with the same statistics
  (`runtime.csv`, the summary's "Run time, memory and CPU", one table). The paper reports times, peaks and cores,
  never our limits; those are checked in the preamble's "Memory limits" (limit next to peak, runs at the limit, OOM
  kills, every run that may have failed for memory listed: runs.csv `memory_failure`).
- **RQ2, conformity:** which CBOM standard and version do the generators follow, and how far do their documents
  comply with it in letter and in substance? The declared `bomFormat` / `specVersion` (CryptobomForge: IBM's draft),
  whole-document validity and the error kinds, per-field spec issues, the standard's own fields filled per asset type
  (meaningful), and **where the information lives**: per tool and dimension the share of algorithm assets carrying the
  value in a standard field against a property, the name or an API identifier (`schema-`, `conformance-`,
  `meaningful-`, `sources-run<i>.csv`).
- **RQ3, normalisation:** how far does CycloneDX's own registry carry the normalisation of real CBOMs, what must be
  added beyond it, and how much of the generators' disagreement is representational rather than substantive? The
  registry (1.7 `algorithmFamily`) is the canonical level (L2); every item records whether its family is the
  registry's, and the delta is a table: every addition of `algorithms.yaml` with its kind of gap (missing family,
  scheme-less key type, generic marker, non-crypto), the items resting on it and the identifier layer the registry
  lacks (`registry-run<i>.csv`). Then the deltas L0 -> L4 on fixed items, shown for both matchers: the gap is
  representational heterogeneity (naming, packaging, placement, spellings per identity), the residual at L4 is
  substantive (`levels-`, `matchlevels-run<i>.csv`, `vocab.csv`).
- **RQ4, agreement:** to what extent do generators agree on cryptographic findings? The agreement at L4: the exact
  matcher carries the headline (the share of items another voter group reports, details aside), decomposes the gap
  per dimension and supplies the call sites, the per-tool rates (confirmation, exclusivity, majority coverage; no
  ranking claim) and the rater sample (items stratified by k, two raters, Cohen's kappa: precision per k on real
  findings). RaQuN, a published external n-way matcher under its paper's configuration, is the cross-check in the
  background, one sentence in the summary and the paper: the share that still holds when a group must be the same
  finding with the same details (roles swapped 2026-10-23). k of n counts the eligible voters that ran on the repo
  (`items-`, `agreement-`, `score-`, `grouping-run<i>.csv`).
- **RQ5, detection:** given cryptographic calls and decoys planted into real projects, how well does each generator
  detect the calls and ignore the decoys? Recall at the site, precision on the decoys, F1 per tool, language and
  style with CIs over repos, the micro F1 next to it, never pooled; the bridge to RQ4: Spearman between confirmation
  rate and F1 over tool x language cells, Kendall tau between the two rankings. The tools ranked by F1 is one line
  here. **Changes off the edit** are measured and reported as a finding, never folded into F1: the share of judged
  mutants where the tool reports something new outside the inserted lines, with the medians of extra units and sites,
  read against the tool's run-to-run noise floor from a stability experiment (`report --baseline <exp>`). Caveats the
  paper states: planted calls are plain uses of the catalog's APIs, precision covers the decoy kinds, insertions are
  easier than real findings (`mutation-`, `mutation-scores`, `bridge-run<i>.csv`).

Decisions: a spec is a spec (repos, tools, repetitions, mutation) and its name says the kind (`bar-`, `stability-`,
`corpus-`, `mutation-`; no numbers that could read as an RQ), the analysis is one code path on any spec, ground truth
exists in the micro projects and the mutants only; no funnel: tools are never dropped after RQ1 (volume is not
quality) or RQ2 (a format property), findings carry forward as covariates and every analysis scopes per dimension by
what a tool reports; mutants are hosted on the local git server; mutation-main is ~30 repos per language among the
smaller ones of corpus-main, ~10 calls and ~10 decoys each, sized after the corpus run's timings. Dropped 2026-10-29:
a "quorum" question and a meta-question on the method.

**Paper languages (decided 2026-10-20):** the paper analyses Java, Python and Go (Go added the same day, with the
mutation harness); every other language (C, JavaScript/TypeScript, Rust, ...) is irrelevant for now: tools still run
where they declare support, but no analysis, figure or tuning effort goes into those languages.

## Status (updated 2026-10-29)
- **Current experiment scope:** 15 CBOM generator tools integrated; the bar exam of 2026-10-23 passes 11 of them
  (`docs/findings.md` "Export review 2026-10-24"), confirmed by the rerun with eight decoys (bar-micro export of
  2026-10-29: the same 11 pass, the same 4 fail, the gap between the lowest pass and the highest fail is 8 vs 6 of
  14). Every spec but bar-micro lists 10 of the 11, `cbomkit-jar` left out since 2026-10-30 (no `tool_languages` needed: every passed tool passes in every language
  it was scored in). LLM providers are excluded for now; the provider workers and their analysis support remain implemented.
- **Exports of 2026-10-24 reviewed:** bar-micro, corpus-pilot-small, corpus-icpc, corpus-pilot-medium and
  mutation-pilot, rerun whole with vocabulary 2026-10-23.2 and the caps of 2026-10-23 (cdxgen 24 GiB, CipherRadar
  8 GiB), sizes recorded at pin time. Headline agreement at L4 0.78-0.81 (exact matcher) against RaQuN's 0.58-0.59;
  RQ5 F1 per tool and language filled with CIs; cdxgen OOM-killed 8 times at 24 GiB (read as `oom`), failed the bar
  anyway. stability-pilot and mutation-smoke were not in this round (the user runs them).
- **Done 2026-10-30:** the observer role is gone (`role: observer` in `tools.yaml`, `--observers-vote`,
  `common/tools_registry.py:votes`, the `role` columns of `levels-`, `items-`, `score-run<i>.csv` and the `°` mark:
  the bar exam is the one filter, the three former observers failed it; every tool votes, the voter
  groups stay). The stale `micro_2026-10-17` and `pilot` lines in `tools.yaml` went with it: the bar's verdicts are
  recorded in the bar-micro export, nothing in code reads them.
- **Done 2026-10-29:** the catalogs have eight decoy kinds (four added: a variable named after an algorithm, a hex
  constant shaped like a key, a URL ending in `.sha256`, a certificate file path; the planted calls stay as they
  are, for simplicity); the micro projects and their ground truth are regenerated (catalog and truth version
  2026-10-29.1), so bar-micro, mutation-smoke and mutation-pilot need a rerun, and the old bar-micro export must
  not be re-rendered (its line numbers moved). The decoys added after CipherSift's pinned release are held out for
  it. `vocab.csv` carries the record's `asset_type` and the "Spellings per identity" table counts algorithm assets
  only (cdxgen's certificate file names no longer count as spellings of ECDSA). The six questions of the paper are
  in `analysis/rqs.py` and the summary follows them (the bar exam, RQ1 volume, RQ2 conformity with "Where the
  information lives" from `sources-run<i>.csv`, RQ3 normalisation with "The registry and what it lacks" from the
  `registry` column of `items-run<i>.csv` and `registry-run<i>.csv` (vocabulary 2026-10-29.1: `gap:` per
  addition), RQ4 agreement, RQ5 detection with "Changes off the edit" from `mutation-scores.csv` and the noise floor
  `flux` of `stability.csv` joined by `report --baseline`); the corpus builder enumerates every cell and draws
  with configurable bins and a per-cell quota (`experiments/README.md` "Corpus sampling"). Re-rendered on the host:
  corpus-pilot-small and mutation-pilot (14 % of the pilot's items rest on non-registry families, RSA and EC
  foremost; CBOMkit and most 1.6 tools carry family and parameters in names, CipherRadar and CryptobomForge in
  fields; cdxgen changes untouched Java code in 30 % of the mutants, CipherRadar in 8-14 %).
- **Paper thread (discussed 2026-10-29):** step 0 "Subject tools" before the RQs (see "Research questions"); the
  paper owns that the bar requires a located finding (it decides cryptarium by construction) and that CipherSift
  passes trivially (only its decoy precision on the four new kinds is held out). To write: `docs/tool-selection.md`,
  the candidate table with every tool's fate; "eligible" wording in the summary's bar section. Open: whether the
  four tools that failed the bar appear after step 0 (the proposal: one table and done).
- **Waiting for the user:** human review of the snippet catalogs (the micro projects, their ground truth and the
  mutants are generated from them) and the decoys, the hand-check verdicts and the `languages` lists (all AI-made).
- **External:** the SCANOSS research key, the Cryptoscope artifact; the rater study after the corpus-main run (a
  sample of items stratified by k, two raters, Cohen's κ; a review of `vocab.csv` with κ is an option, not decided).
- **Small open analysis items:** CryptobomForge's IBM-draft windows are CodeQL's `contextRegion` (finding ±2 lines),
  so the exact line could be read as the window centre (one over-credit on micro-python); Jaro-Winkler candidates
  for curating the mapping table; per-field columns in `micro-run<i>.csv`; inferring `functions` from API names.
- **Next:** on the VM: stability-pilot,
  mutation-smoke, `make experiment-delete NAME=<x>` for the former smoke experiments e0-smoke, e0-cbomkit-smoke,
  e0-llm-smoke, e0-phase2-smoke and the runs under the old spec names e0-micro, e0-stability, e1-pilot-corpus,
  e1-icpc-corpus, e1-pilot-60, e4-mutation-smoke; corpus-main sampled (2026-10-30: pc6, 162 repos,
  1242 supported scans; 7 of its 54 Python repos use pyca, 22 of its 54 Go repos x/crypto); bar-micro and
  stability-pilot rerun on the pins of 2026-10-30 (every export so far ran on the old versions) -> the corpus-main run -> stability-pilot's export as `report --baseline` for mutation-main -> the rater
  study and mutation-main -> Phase 6.

## Phases 1-3 (done 2026-10-15 to 2026-10-18)
What exists and the decisions behind it. Numbers: `docs/findings.md`.

### Phase 1: reproducible experiment infrastructure
Commit pinning, provenance, the git server, the batch runner with its results volume and the corpus builder, all as
described under "How it works". Redis stays the queue and live status only: hundreds of repos × tools × repetitions
plus the mutants are several GB of CBOMs. The corpus builder samples GitHub stratified by stars and size (every cell
enumerated, a seeded uniform draw per cell since 2026-10-29), with recent
activity and a crypto grep over first-party source only (test, vendor, example and docs paths excluded); every
exclusion is recorded with its reason (refinement 11); `make corpus-prune` drops repos a tool timed out on in the
stability run.

### Phase 2: generators
- **LLM workers, one per provider.** The shared logic is `common/llm_cbom.py`: clone from the git server, pre-filter
  files with crypto API patterns, send line-numbered chunks with file paths, require file and line per asset,
  assemble CycloneDX 1.7 (free-text values mapped onto the enums, algorithm family and curve onto the 1.7 registry,
  originals kept in `bf-cbom:<field>`; `PROMPT_VERSION`, `ASSEMBLY_VERSION` in the metadata). Each provider worker is a thin adapter (client, model id,
  key), so all providers share prompts and file selection and differences come from the model. Models: DeepSeek
  `deepseek-v4-pro` (since 2026-10-21; `deepseek-chat` before), OpenAI `gpt-5`, Anthropic `claude-sonnet-5-5`, each
  with the provider's defaults, so none takes or applies a temperature (gpt-5 and Sonnet 5.5 reject one,
  DeepSeek ignores it while thinking, its default) and 3 repetitions measure the variance (refinement 10). For the
  reasoning models the output limit covers the reasoning (`LLM_MAX_OUTPUT_TOKENS`, DeepSeek 64K).
- **Costs are computed offline, never in the worker:** prices change and differ per model, cache hit and time of day.
  `analysis/data/llm_prices.yaml` is dated and sourced; `analysis/costs.py` prices the stored usage, so costs can be
  recomputed for any price date and the paper states the table version.
- **Engine independence:** tools sharing their detection logic (an engine, a rule set) are one voter
  (`voter_group`). The LLM workers are separate voters by default; merging them into one "LLM" voter (`--merge-llm`)
  is the sensitivity analysis, since they share prompts and file selection. The pattern-rule tools (securin-xbom,
  CipherRadar, SCANOSS) were one voter `grep-rules` until 2026-10-20; their rule sets share no rule
  (`misc/rule_overlap.py`), so they count apart, and the `engine-merged` ranking of `grouping-run<i>.csv` shows the
  former grouping next to `per-tool` and `llm-merged`.
- **Blocked on access:** SCANOSS's Java/Python rules (API key) and IBM Cryptoscope (artifact), see the table above.

### Phase 3: analysis library (`analysis/`)
Pure Python and unit-tested; runs in the runner image or on the host for a copied export.
- **Normalise** (`normalize/`): one record per (component, occurrence), as in the paper's normalisation figure, with `group`, `family`, `algorithm`, `primitive`, `functions`,
  `mode`, `padding`, `curve`, `key_size`, `digest`, `param_set`, `iterations`, `oid`, `protocol`,
  `protocol_version`, `material`, `asset_type`, location (`file`, `line`, `line_end`, `in_scope`, `file_exists`),
  `context`, `confidence`. Every value keeps `raw`, `src` (exact JSON path) and `via` (field, parse, dictionary,
  interpretive, api, dependency, ref, derived), so RQ2 can say where a tool puts information.
  - Precedence per spec: occurrence evidence > standard fields > tool properties > name > refs/dependencies > API
    context. A more specific value of the same kind refines (EC -> ECDSA); a different one is kept as a `conflict`;
    API context, rule ids and dependencies only fill gaps; derived values are tagged `derived:<dim>`.
  - The name parser (`vocab.py`) splits tokens, matches the longest alias, decomposes composite names, knows
    JOSE/SSH/JCA identifiers; code fragments stay unmapped (never guessed), lists are `ambiguous`.
  - `conformance.py` checks every standard-field value against the declared `specVersion`'s schema
    (`spec_issues`); `vocab.csv` is the reviewable mapping table.
  - Every record has a `resolution` (2026-10-19): `mapped` (an algorithm identity), `ambiguous` (a list of
    candidates), `api` (a code identifier such as `Cipher.getInstance`, recognised by shape for every tool, or a
    reference label a tool invented, `kind: api` on its dictionary entry), `non-crypto` (a finding outside
    cryptography reported as an asset: securin-xbom's audit rules), `undefined` (a name nobody understands; never
    guessed). Only `mapped` records become items;
    the shares per tool are reported. The asset types and their subtypes are CycloneDX's enums (`assetType`,
    `relatedCryptoMaterialProperties.type`, `primitive`; a test keeps `cbom-norm.schema.json` equal to them).
  - `record_id` = `<tool>:<component>:<occurrence>`, `site_id` = `<tool>:<file>:<line>`.
- **RQ2, and RQ1's traceability** (`schema.py`): whole-document validation against the vendored schemas (`jsonschema`
  with an offline `referencing` registry), split by `cbom_by` (refinement 3); the tools' CycloneDX versions stay as
  shipped (1.6: CBOMkit, cbomkit-jar, CryptoScan, pqcheck, securin-xbom, xgrep, qelens, Cryptarium and qScan; 1.7: cdxgen,
  CipherRadar, CipherSift, Q-DARPAN, SCANOSS; CryptobomForge IBM's draft, its own empty draft document when CodeQL
  finds nothing; what BF-CBOM writes itself, the LLM assembly, is 1.7); "meaningful" shares per CycloneDX asset type
  with the standard's own fields for that type (standard fields vs any source; no tautological assetType column);
  traceability (RQ1) per asset type, with `located_file` (names a file) and `located_line` (file and line) apart,
  weak and strong on the records with a line (refinement 4); key material gets its own rows so secrets in
  configuration files do not dominate.
- **Match.** Two n-way matchers over the same records, with named roles in RQ4 (decided 2026-10-22, swapped
  2026-10-23): the exact matcher carries the headline, the per-dimension decomposition, the call sites, the per-tool
  rates and the rater sample; RaQuN is the external cross-check, kept in the background (a sentence and its rows in
  the matcher x level table; the user wants no more attention on n-way matching). On the stability export at L4 the exact
  matcher has 269 items with 71 % at >= 2 voters, RaQuN 260 with 56 %: RaQuN's groups sit inside the exact clusters
  (it splits an algorithm cluster by detail), its parameter agreement is 100 % by construction (Jaccard >= 0.75 over
  few attributes), it has no call sites, and a terse tool ("AES" next to "AES-256-GCM") is "right alone" under it for
  lack of detail, not of correctness; the 56 -> 71 % gap is the agreement that breaks on details. The pilot's
  call-site matcher (a) was retired 2026-10-18; its line window lives on in (b).
  - (b) the **exact matcher** (`matchers/exact.py`): its groups are the ladder's clusters at a level, one per key
    (records whose identity at some level up to `--level`, default L4, is that key; cumulative), so location-less
    tools take part. Clusters never merge through a record with several keys (decided 2026-10-19: the earlier
    union-find let `hmac.new` merge HMAC with SHA-1 and `SHA-2` merge SHA-256 with SHA-512); a cluster whose members
    span several L4 identities is what the level declares "the same" (`units`, `multi_unit_clusters`); generic
    identities (SHA-2, EC, the bare `SHA` marker) are *attached* to the specific clusters and confirm without
    merging; records without an L4 identity are never matched. Run at every level for `matchlevels-run<i>.csv`
    (RQ3: matcher × level); inside a group, located records are chained into call-site groups (same file, lines
    within `--window`); `--closure` adds implied memberships (attached too).
  - (c) **RaQuN** (`matchers/raqun.py`): PyQuN's n-way matching with a structured similarity (Jaccard over
    `<dimension>=<value>` attributes: the identity at the level and the standard's dimensions, never our `group`),
    on the exact matcher's records (mapped, with an L4 identity; decided 2026-10-21), location-free, at most one
    asset per tool per group. Controlled comparison (2026-10-23): only the algorithm identity varies across levels;
    all other attributes use full normalised values from L0 onward. Each level is an independent run, with no
    cumulative grouping guarantee. Configured as in
    the RaQuN paper (k = n, the tools in the repository; Jaccard >= 0.75; decided 2026-10-21, no further
    configurations). Installed with the `raqun` extra. The UI's "Find Similar Components" (`misc/pyqun`, BERT over a bag of strings) is a different thing and
    stays a comparison only.
  - (d) further matchers: the interface (records in, match groups with per-dimension agreement out,
    `matchers/base.py`) must not assume (a)-(c) are the only ones.
  - Every matcher reports per group and dimension (algorithm, key size, mode, padding, curve, digest, location) how
    many voters report and agree (`base.py:agreement`); `compare.py` compares two matchers by co-membership.
- **Per-tool rates** (`score.py`, RQ4; no ranking claim without ground truth, the ranking is RQ5's by F1), per
  tool × dimension × level on the exact matcher's groups: items are (repo, unit)
  pairs (unit = L4 identity; a parameter: (unit, value); a location: (unit, file:line)), confirmed when a group holding the tool's
  record also holds another voter group's (same value / same call site), so a group is never counted twice; the
  point estimate is the **mean of the per-repository confirmation rates** (refinement 1; the pooled rate is a
  column), with bootstrap CIs over repos of that mean; a **CI, and a place in the sensitivity rankings, only with
  items in ≥ 3 repos and ≥ 10 items** (`MIN_REPOS`, `MIN_ITEMS`); exclusivity; majority coverage (the share of the
  repo's majority items the tool reports, a recall proxy next to confirmation's precision proxy); Dawid-Skene (binary
  EM without labels, seeded from the majority vote) and leave-one-voter-out with Kendall τ as sensitivity checks. Tailored (tool, repo) pairs are excluded (`--include-tailored`).
  `stability.py`: Jaccard across repeated runs and majority consensus sets (`--consensus`).
- **Tables** (`tables.py`), flat long-format CSVs in three layers, so new matchers only add files: **runs**
  (`runs.csv`, one row per stored run with status, timing, memory, provenance, LLM usage and `cost_usd`), **records**
  (`records-run<i>.csv`), **matches** (`groups-`, `members-`, `agreement-<matcher>-run<i>.csv`, `matchers-run<i>.csv`).
  Plus `micro-run<i>.csv`, `micro-items-run<i>.csv`, `items-run<i>.csv`, `units-run<i>.csv` (per repository `units-repo-run<i>.csv`), `levels-run<i>.csv`, `coverage-run<i>.csv`,
  `conformance-run<i>.csv`, the RQ2, score and stability tables. Join keys: (snapshot, variant, run, record_id) and
  (snapshot, variant, run, group_id); group ids restart per snapshot and variant. Full list: `experiments/README.md`.
- **Report** (`analysis/report/README.md`): plot recipes on the CSVs (UpSet of tools per item, fill-rate heatmaps,
  ladder, per-tool rates with CIs, the F1 ranking of RQ5, cost per confirmed item). The figures are built by the user later with a consistent,
  accessible palette.

## Phase 4: mutation harness (`mutation/`, RQ5: planted calls and decoys; insertion built 2026-10-20, decoys 2026-10-22)
- **Snippet catalog** (`mutation/catalog/{java,python,go}.yaml`, versioned; also the source of the micro projects,
  `mutation/micro.py`): the same 15 ids per language (RSA-2048
  keygen, EC P-256 keygen, ECDSA / Ed25519 signing, AES-128/256-GCM, AES-256-CBC, 3DES-CBC (Python: SHA3-256),
  RSA-OAEP, SHA-1/256, MD5, HMAC-SHA256, PBKDF2, TLS 1.3 context). Each has its expected canonical record (`unit` =
  the L4 identity), the identities it also contains, its imports and the `direct` and `indirect` templates (the
  algorithm name or a parameter in its own variable, Go: a function value; refinement 5); `wrapper` (a helper next
  to the host plus one call) is derived. A snippet only goes where its library is already there (decided 2026-10-20):
  Java is JCA throughout, Python's pyca snippets need a declared `cryptography` (stdlib ones go anywhere), Go's PBKDF2
  needs `go >= 1.24` or a required `golang.org/x/crypto`; dependency files are never edited, so every mutant is one
  edit of one file. **Decoys** (`decoys:` in each catalog, the one source of the micro projects' decoys too): eight
  kinds per language (the table in `experiments/corpus/README.md`): a comment and a log message naming algorithms,
  a CRC32 checksum, a Base64 encoding, a variable named after an algorithm, a hex constant that looks like a key, a
  URL ending in `.sha256` and a path naming a certificate file, each one `direct` body with no expected record; a tool that reports one has produced a false positive on a
  known negative.
- **Insert** (`lang.py`, `insert.py`; tree-sitter for Java and Go, Python's `ast`): into a first-party method or
  function with a caller elsewhere in the repo (by name, `host_callers` recorded as a covariate) or a `main`, never in
  test, vendor, example or generated code (refinement 6); before its first statement (Java inside its own try/catch,
  Go inside its own block, so locals never clash); imports where the language keeps them (Java sorted into its group,
  fully qualified on a name clash; Python after the absolute imports, aliased on a clash; Go sorted into the import
  group, `crand` next to `math/rand`); the mutated file must parse. Mutants in context are not compiled (no project
  builds); `tests/test_mutation.py` compiles or runs every snippet x style in small hosts (javac, go build, Python).
  New-file insertion is not built (repos without a host are reported).
- **Variants** (`mutants.py`): a spec's `mutation:` block (`per_repo`, `styles`, `snippets`, `seed`, `decoys`) gives
  every repo that many call mutants, (snippet, style) pairs drawn round-robin over the styles from a seeded shuffle,
  hosts from files not used yet, then as many decoy mutants (`decoys: equal`, the default; an int; 0 for none),
  round-robin over the decoy kinds, `direct` only; each a commit on the pinned commit (fixed author and date: the SHA
  depends only on the content), a branch `bfcbom/ins-<snippet>-<style>-<hash>` in the base mirror. `mutants.json`
  records file, host, call line, spans, line shifts, the inserts (a missing branch is rebuilt from them with the same
  SHA), `decoy` and the patch (Phase 6's mutant patches). mutation-main: ~30 repos per language among the smaller
  ones of corpus-main, ~10 calls and ~10 decoys each.
- **Ground truth from mutation** (`analysis/mutation.py`; `mutation.csv`, `mutation-rates.csv`,
  `mutation-scores.csv`, `bridge-run<i>.csv`): per (mutant, tool) against the same tool on the original, counting
  only new records (an original finding moved by the inserted lines is not new). A planted call: found at the site
  (the line naming the algorithm ±2), on the inserted span, in the file, in the repo (`masked` when the original
  already has the unit), leniently by group; parameters on the inserted lines. A planted decoy: `false_positive` when
  any new record sits on its inserted lines. Both: extra and lost units and sites (the "no other change" check, read
  next to the tool's run-to-run instability). With repeated runs a dimension counts when it holds in most of the
  mutant's runs, which also exposes LLM answers from memorised popular repos. Rates: the mean of per-repository rates
  with bootstrap CIs over repos (`score.py`'s support). Scores per tool, language and style: recall at the site and by
  group, precision = calls found / (calls found + decoys hit), F1, CIs over repos; the bridge to RQ4: Spearman between
  the exact matcher's L4 confirmation rate and F1 per language over tools, Kendall τ between the two rankings.
- **Removal mutants:** not built and not needed for RQ5 (the decoys give the negatives); if ever built, insertion and
  removal results are reported apart, never pooled (refinement 7).

## Phase 5: specs, analyses, ground truths
- **A spec is a spec:** which repos, which tools, how many repetitions, optionally a `mutation:` block and
  `tool_languages:`. Its name says the kind: `bar-` (the bar exam on ground truth), `stability-` (repeated runs),
  `corpus-` (real repositories as they are), `mutation-` (real repositories with their mutants). Names, not studies:
  nothing in the analysis depends on them (`experiments/README.md`).
- **The analysis is one code path on any spec:** `report` writes the same RQ1-RQ4 sections and per-tool rates for the
  12 repos of corpus-pilot-small and for the 54 or 81 of corpus-main; the bar-exam section when the micro
  projects are in the spec, the RQ5 section when it has mutants, stability when repetitions > 1. The pilot and ICPC
  specs are development runs; the paper's numbers come from corpus-main and mutation-main.
- **Ground truth exists in two places:** the micro projects (full: recall, precision, F1, the bar) and the mutants
  (planted calls and decoys: recall, precision and F1 on known items in real code). Everything else is agreement
  without ground truth. The rater study is not a spec: a hand-labelled sample drawn from the corpus run's
  `items-run<i>.csv`, stratified by k, two raters, Cohen's κ, precision per k; the k = 1 stratum is the "right alone"
  check.
- **The runs:** `bar-micro` (every tool once on the three micro projects: the verdicts; the bar-exam table
  shows all 15 tools), `stability-pilot` (every tool 3× on the micro projects and ten small pilot repos:
  determinism and run-to-run variance, needed before any agreement claim; its timings decide replicas and pruning),
  `corpus-main` (the stratified sample, 18 or 27 repos per language by `--per-cell` 2 or 3, the tools and their languages as the user set them after the bar exam: RQ1-RQ4), `mutation-main` (~30 repos per language from corpus-main,
  ~10 calls and ~10 decoys each: RQ5; `mutation-smoke` first). Also on the known items: the voter groups'
  independence (tools that miss the same mutants more often than chance share a blind spot, `analysis/README.md`
  "Voter groups").
- **Threats addressed explicitly:** shared blind spots (engine grouping), inserted calls being easier than real ones
  (three insertion styles, planted decoys for the precision side, the rater sample on real findings), moving targets
  (pinned digests and a replication package), benchmark-informed tools (refinement 13), normalisation creating
  agreement (refinement 14), the AI-written bar exam (human review, the bar low: half of plain calls in a declared
  language, the excluded tools reported in the bar table).

## Phase 6: replication package
- Tag a release and publish the images to GHCR (`.github/workflows/images.yml`).
- Archive the corpus files, all raw CBOMs, the mutant patches, the analysis outputs and the notebooks on Zenodo
  (concept DOI in the README).
- One command, `bf-cbom experiment reproduce`, reruns the analysis from the raw results.

## Verification still to do
- **Done:** the mutation smoke (2026-10-22) and the mutation pilot (2026-10-23) ran end to end on the VM
  (`docs/findings.md` "Mutation smoke", "Export review 2026-10-24"); both are rerun with the eight decoy kinds.
- **End to end:** bar-micro and a 5-repo corpus spec from the spec to the tables with `bf-cbom experiment reproduce`.
- **On the VM:** traceability against the mirror. The replica test is done (2026-10-30, How it works, "Memory
  budget").

## Method refinements (pitch review 2026-10-15; numbers are cited from code)
1. **Unit of analysis.** Every RQ is reported at **repo level** (the items per repo; all tools take part) and at
   **call-site level** ((item, file, line ±w); only tools with locations). The micro check follows the same split
   (`analysis/groundtruth.py:score_records`): call-site recall (the expected call found in its own function; the
   bar) and repo-level recall (the call's group anywhere in the project), side by side, never pooled. The micro projects are not changed to suit a tool: plain constant algorithm
   strings are the common real-world pattern, and a tool's blindness to them is a finding.
2. **Implied assets are not disagreement.** HMAC-SHA256 implies SHA-256, PBKDF2 implies HMAC; CBOMkit expands such
   hierarchies, others don't. `implies` in `algorithms.yaml` (generic rules from a record's digest plus an explicit
   table; protocols imply no cipher suites unless named) is applied as an optional closure (`--closure`), reported
   next to the plain sets.
3. **RQ2 only for native CycloneDX output.** When our worker adapter writes the CBOM, validity would measure us;
   `cbom_by: bf-cbom` in `tools.yaml` (the LLM workers) splits the RQ2 tables; the format is read from each document
   (CryptobomForge writes IBM's draft itself: `tool`, no schema).
4. **Traceability, weak and strong.** Weak: the file exists at the commit and the line is in range. Strong: lines
   line±w also contain a token from the alias table (algorithm name, provider string, crypto API identifier).
5. **Third insertion style** (Phase 4): indirection through a variable or constant separates pattern tools from
   data-flow tools.
6. **Reachable insertions** (Phase 4): only into methods with a caller or an entry point, never under tests; the
   insertion kind is recorded as a covariate.
7. **Removal mutants are not built** (Phase 4): the planted decoys give RQ5 its negatives; if removal is ever built,
   its candidates are easier by construction, so insertion and removal results are reported apart.
8. **Worker replicas.** Workers are stateless BLPOP consumers, so several containers can share a tool's queue:
   `make up-prod REPLICAS=...` starts them and every experiment counts them from their heartbeats (How it works,
   "Memory budget"). The long lanes decide N per tool; run times measured with replicas carry their contention.
9. **Statistics for few tools** (RQ5's bridge). Kendall τ over a handful of ranks is coarse: add Spearman between the
   confirmation rate (RQ4) and F1 on the planted items (RQ5) over all tool × dimension × language cells, with
   bootstrap over repos; Java, Python and Go are three replications of the method.
10. **LLM recall ceiling.** The pre-filter bounds what the LLM sees: keep it import-based and generous, versioned
    (`PROMPT_VERSION`) and stated as part of the tool in the paper.
11. **Corpus exclusions.** No repos from the tools' own test suites or published benchmarks, none whose crypto sits
    only in dependencies (the scope is first-party source), none exceeding a tool's timeout in the stability run
    (`experiments/corpus/exclusions.yaml`, `make corpus-prune`).
12. **Minimum viable paper:** the exact matcher's ladder and rates, insertion mutants with decoys and the paper
    languages with the independent engines already integrated; RaQuN's cross-check and Dawid-Skene are additions on
    top, removal mutants are out.
13. **Benchmark-informed tools.** A tool adapted while looking at our results (CipherSift: `tailored_on` in
    `tools.yaml`) has inflated scores there by construction, also in agreement-based scoring. Its tailored (tool, repo)
    pairs are marked `tailored` and left out of the ladder, the matchers and the scores by default; the corpus builder excludes
    those repos; pilot numbers are reported apart; its RQ5 scores carry no mark (decided 2026-10-30: CipherSift was developed
    independently by a colleague of the research group; only the bar exam keeps the `*`); the four decoy kinds added 2026-10-29 post-date its pinned release, so its decoy precision is the one
    part of the bar held out for it; the threats state that its rules and the micro ground truth were both written
    by Claude.
14. **Normalisation can create agreement; report how much.** The vocabulary and the specs were developed on the
    outputs we evaluate, and a too eager normaliser would work against our own hypothesis that tools report
    heterogeneous things. Every agreement number is reported as a ladder (`levels`) that varies only *when two
    reports count as the same algorithm*, on fixed items (an item = an L4 identity in a repository) with a cumulative
    relation (a match is never lost at a higher level, so shares only grow and each step is attributable): L0 raw
    names, L1 case and separators ignored, L2 standard vocabulary (whole names, 1.7 `algorithmFamily`, JOSE/SSH/JCA
    identifiers), L3 composite names parsed, L4 everything tool-specific (the tools' own spellings and labels, no
    judgements; the former L4lit level went with the last judgement entry on 2026-10-19); parameters enter from L2
    (digest from L3: CycloneDX has no field for it). Clusters are per key and never merge through a bridging record (decided
    2026-10-19); `confirmed_strict` and `multi_unit_clusters` say how much rests on generic attachments and on
    spellings that cover several identities; records without an L4 identity (`resolution`) are not items.
    The headline agreement (RQ4) is at L4, the L0 -> L4 deltas are RQ3's representational heterogeneity, and every
    table holds every level so the picture is checked across them (decided 2026-10-18 after review: the first ladder
    changed key *and* unit per level, so its shares were fractions of different populations and not monotone). The
    vocabulary is frozen before corpus-main (Rules).
    **Family and group:** `family` is the standard's own canonical level (CycloneDX 1.7 `algorithmFamily`, the
    registry), so L2 normalises to the vocabulary the tools claim to implement; the additions (RSA, EC, CSPRNG,
    PRNG, PBE, FN-DSA, NTRU-Prime, Hybrid-KEM, crypt, the generic SHA marker) are marked `registry: false`, listed
    with their reasons, and the share of items resting on them is reported (the registry names schemes, tools report
    key types without a scheme: a finding about the standard). `group` is ours (the ICPC paper's coarse families),
    a deterministic many-to-one aggregation of families used for the micro ground truth and figure readability only;
    no agreement or ranking claim depends on it, and CycloneDX `primitive` is the standard's own coarse axis where
    one is needed (`analysis/README.md`).
15. **Every tool gets its best fair run; no result may be BF-CBOM's fault** (decided 2026-10-17).
    - *Invocation:* the tool's documented best invocation for the repo at hand (queries, project types, flags),
      current pinned releases, robust workers (cwd outside the clone, timeouts, failure reporting), and everything
      that changes results in `tool_version` or the run notes.
    - *No repairs:* no source patches, no rewriting of a tool's input or output, no rule filtering, no files added to
      scanned repos. A crash is a result about the tool. The one intervention (sharpened 2026-10-20): when a crash
      on a specific file ends the whole scan, that file is left out through the tool's *own* exclusion mechanism, by
      its exact path, and recorded in the run notes (`workaround:` lines, the `workaround` column of runs.csv):
      CipherRadar's `.cradarignore`. Without such a mechanism the run fails (CryptoScan 1.4.0: its `--exclude`
      matches basenames only). A tool that runs but finds less is never steered (CBOMkit's `src/` indexing stays
      off). Effects of each, for the paper: `docs/findings.md` "Interventions".
    - *Analysis:* our analysis reads every location format a tool uses.
    - *Check a 0 before accepting it:* rerun the tool in a throwaway container with its debug output and find the
      cause; distinguish *by design* (cdxgen gives no Java locations) from *our setup* (cdxgen without `-t` never
      analysed Python or Go).
    - *Paper:* state the per-tool invocation in the replication package, and that the micro numbers of 2026-10-15
      were partly our setup errors (`docs/findings.md`).
