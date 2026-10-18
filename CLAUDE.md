# CLAUDE.md in BF-CBOM git repo

Shared context for coworkers and for Claude Code (which loads this file automatically). Details live elsewhere:
`notes.md` (build, operations, per-tool recipes and gotchas, implementation notes per work package),
`docs/findings.md` (results so far), `experiments/README.md` (spec format, normalisation, analysis outputs),
`analysis/report/README.md` (plot recipes), `analysis/data/CHANGELOG.md` (vocabulary versions),
`docs/pilot-2026-10-08/` (the colleague's pilot).

## Rules
- **Start with make, never plain compose.** `make up-prod`; clean rebuild `make clean-docker && make up-prod
  NO_CACHE=1`. Never run plain `docker compose up` or `docker compose run`: the Makefile exports the image tag
  (`VERSION`) and `BASE_IMAGE`; without them compose looks for `:latest` images and tries to build from a nonexistent
  `base`. Every runner command has a target: `experiment`, `experiment-list` / `-status` / `-collect` / `-cancel` /
  `-delete` / `-export`, `analysis`, `corpus`, `corpus-prune`. After every pull run `make up-prod` first: the runner
  bakes the CLI and the workers bake `common/`.
- **Nothing lands on the host unless exported.** Results and git mirrors stay in the named volumes `results-data` and
  `git-data`; `make experiment-export` is the only path out, which is why the CLI runs inside the `runner` container.
  A copied export can be analysed on the host: `RESULTS_DIR=exports uv run bf-cbom analysis report <exp>`.
- **The laptop prepares, the VM runs.** Batch runs happen on the 64 GB VM (`EXPERIMENT_MEMORY_BUDGET_GB` about 52; the
  prod worker limits sum to 61 GiB). The laptop is for code, configs and analysing copied exports.
- **Results are disposable until the paper's runs.** One code path per feature, no compatibility with older result
  layouts; old experiments are rerun, not migrated.
- **Benchmark every tool as shipped, in its best fair run** (refinement 15). Fix our setup, never the tool.
- **Pinned builds:** base images, uv, git sources and CodeQL packs are pinned (bumping: `notes.md`). Never pin an old
  dependency to dodge a bug; fix our code.
- **The vocabulary is frozen** (refinement 14): `analysis/data/algorithms.yaml` and `analysis/adapters/*.yaml` change
  only with a version bump and a reason in `analysis/data/CHANGELOG.md` (`tests/test_vocab_frozen.py`). The pilot
  corpus is the development set; ICPC and the E1 corpus are held out.
- **Lint, format, tests:** `uv run ruff check --fix`, `uv run ruff format`, `uv run pytest` (CI runs all three).

## How it works
- **Coordinator and workers.** Streamlit UI on :8501 (`coordinator/`); Redis is the job queue (`jobs:<worker>` /
  `results:<worker>`); one Docker image per generator (`workers/<tool>/main.py`) on `common/worker.py:run_worker`. A
  worker runs each job in a killable subprocess (hard timeout), publishes `worker:<name>:alive` heartbeats, marks the
  job running via the routing hash `jobs:routing` (`common/jobs.py:mark_job_running`) and pushes the `JobResult` to
  `results:<name>`; the coordinator ingests via `coordinator/redis_io.py:ingest_worker_results`; experiment jobs are
  routed to `results:exp:<name>`.
- **Job states:** held, queued, running, completed, failed, timeout, cancelled, unsupported; one table
  (`common/utils.py:_STATUS_META`) feeds every page. `unsupported` = the repo's language is outside the tool's
  `languages` (`common/jobs.py:tool_supports`), decided at issue time in the UI or recorded as a meta file by the
  runner (`common/experiment.py:mark_unsupported`; `--run-unsupported` disables it).
- **Batch experiments.** `make experiment EXP=experiments/<x>.yaml` runs `bf-cbom experiment run` in the runner
  (`misc/cli/experiment_cmd.py`, logic in `common/experiment.py`): it pins every repo's commit, mirrors it to the
  `gitserver` service (one served repo `<owner>/<repo>--<sha12>.git` per snapshot, because CBOMkit only checks out a
  clone's default branch; mutants will be branches `bfcbom/<variant>`), enqueues through the normal Redis protocol and
  stores the results on `results-data`. Resumable: a run with a stored meta file is skipped, `--retry-failed` redoes
  the runs that are not ok. A spec entry with `path:` publishes a directory as a deterministic commit
  (`publish_fixture`). Spec format and the list of specs: `experiments/README.md`.
- **Experiment folder:** `_experiment.yaml`, `_run-log.json` (runner sessions: times, VM RAM and CPUs, budget, waves,
  peak VM RAM, worker image IDs), `_ANALYSIS/` (with `_SUMMARY.md`; emptied and rebuilt by every `report`),
  `lock.json`, then `<owner>__<repo>@<sha12>/<variant>/<tool>/run<k>.json` plus `run<k>.meta.json`. A folder without
  `_experiment.yaml` is ignored by every command (`experiment list` names it for deletion).
- **Provenance.** `RepoInfo.commit` is resolved at inspection start (`coordinator/redis_io.py:enrich_repo`) and checked
  out exactly by `clone_repo`. `JobResult` carries `tool_version`, `image_ref` (env `BF_CBOM_IMAGE`), `bfcbom_commit`
  (env `BF_CBOM_COMMIT`, stamped into every image as its last layer by make and bake; `-dirty` = uncommitted changes),
  `run_index`, `started_at` / `finished_at`, `notes`, the container's `peak_memory_mb` / `memory_limit_mb` /
  `oom_kills` during the job (`common/memory.py`, cgroup v2, sampled every second) and, for LLM workers, `usage`
  (requests, input / cached / cache-write / output / reasoning tokens, the model ids answered with; kept on failed
  runs).
- **Tool registry:** `common/tools.yaml` (loader `common/tools_registry.py`): engine family, `voter_group` (tools sharing
  an engine or rule set count as one voter in RQ4; default the tool itself), `merge_group`, `languages`,
  `native_cyclonedx`, status, the pilot verdicts, `tailored_on` and `memory_gb` (= the worker's compose memory limit;
  a test keeps them equal).
- **Memory budget (waves):** `EXPERIMENT_MEMORY_BUDGET_GB` (laptop default 14, 0 = unlimited) caps the summed
  `memory_gb` of the tools fed at once; the rest run in later waves. Runner and UI plan waves with
  `common/tools_registry.py:plan_waves` (UI: held jobs, `release_due_waves`, `coordinator/scheduler.py`).
  `EXPERIMENT_REPLICAS="tool=N,..."` counts replicas started with `docker compose up -d --scale` (README "Parallelism
  and memory").
- **Corpus and fixtures** (`corpus/`, mounted read-only into the runner): `micro/<language>/` hand-written micro
  projects with `micro/ground_truth.yaml` (11 assets each, five languages); `pilot-2026-10-08.yaml` (16 pinned real
  projects, the development set); `icpc-2026.yaml` (the 23 ICPC paper repos at HEAD of 2026-10-18, held out);
  `exclusions.yaml` (repos the corpus builder never samples, with reasons). `make corpus` builds a stratified GitHub
  corpus (`common/corpus.py`; needs `GITHUB_TOKEN` in `docker/env/coordinator.env`).
- **Analysis** (`analysis/`, offline, reads the results volume only; `make analysis ARGS="<command> <exp>"` writes
  `<exp>/_ANALYSIS/`; `experiment export` runs `report` first):
  - `normalize/`: CBOM -> normalised records (below); `normalize` also writes `run<k>.norm.json` next to each CBOM.
  - `schema.py`: RQ1, whole-document validation, meaningful shares, traceability; `snapshots.py` reads a pinned
    commit's files from the git mirror or a cache fetched with `--fetch`.
  - `levels.py`: the L0-L4 normalisation ladder (refinement 14) and which algorithms each tool reports per repo.
  - `matchers/`: n-way matchers on the records, `exact.py` and `raqun.py`, compared by `compare.py` (Phase 3).
  - `score.py`, `stability.py`, `costs.py`: RQ4 scores, run-to-run stability, LLM costs from
    `analysis/data/llm_prices.yaml`.
  - `groundtruth.py`: micro recall at call sites and at repo level, precision, inclusion bar, field-by-field
    comparison with `expected`.
  - `tables.py` (flat CSV tables), `summary.py` (`_SUMMARY.md`, rendered from the CSVs only).
  - Commands: `report` (everything, one pass), `normalize`, `schema`, `levels`, `micro`, `tables`, `score`. The UI's
    Experiments page shows stored experiments read-only.
- **Normalisation, three kinds of files.** One **adapter spec per tool** `analysis/adapters/<tool>.yaml` (declared
  format, which components are findings, where each dimension sits in order of precedence, the tool's own dictionary,
  named extractors; inherits `_cyclonedx.yaml`, `_ibm-cbom-draft.yaml`, `_cbomkit.yaml`, `_llm.yaml`); one **shared
  vocabulary** for synonyms that are not tool-specific (`analysis/data/algorithms.yaml` plus the vendored CycloneDX
  1.7 registry and 1.6/1.7 schemas in `analysis/data/cyclonedx/`; a spelling one tool uses goes into its spec, once
  two tools use it, it moves to the shared vocabulary); one **target format** `analysis/data/cbom-norm.schema.json`.
  A new tool needs a spec (a test fails otherwise). *Worker adapters* (`common/cbom_adapters.py`, `workers/*`) make
  raw output usable CycloneDX at scan time; *adapter specs* read CycloneDX into records, offline.

## Generators
New worker: `workers/<tool>/main.py` with `run_worker`, `docker/Dockerfile.worker-<tool>`, a compose service,
`AVAILABLE_WORKERS` in the Makefile, a `docker-bake.hcl` target, a `tools.yaml` entry, an adapter spec, and a worker
adapter if the native output isn't CycloneDX. Tools that only orchestrate other scanners are not integrated (OQS
Scanner, decided 2026-10-17). Versions are pinned in the Dockerfiles and recorded per run; recipes in `notes.md`.

| Worker | Voter group | Languages | Invocation and caveats |
|---|---|---|---|
| `cbomkit` | cbomkit | Java, Python, Go | CBOMkit server image, cloned through the git server over TLS; Python covers pyca only; modules need a build file and `src/`; large repos need ~45 min and a 16 GB heap |
| `cbomkit-jar` | cbomkit | Java, Python, Go | cbomkit-action, source only, the same engine (packaging differences are measured); `CBOMKIT_ENSURE_SRC=1` opt-in |
| `cdxgen` | cdxgen | Java, Python, JS, Go | `--include-crypto --evidence`, `-t python` / `-t go` for those repos; algorithms carry locations only for JS/TS; Java code yields none (atom has no crypto sinks) |
| `cryptobomforge` | cryptobomforge | Python, C | CodeQL's crypto inventory queries, C with `--build-mode none`; writes IBM's draft `1.4-cbom-1.0`, not CycloneDX |
| `deepseek`, `chatgpt`, `claude` | one each, `merge_group: llm` | all five | `common/llm_cbom.py` (below); `chatgpt` / `claude` need `make up-prod EXTRA_PROFILES=llm` and a key in `docker/env/worker-<provider>.env` |
| `cryptoscan` | cryptoscan | eleven | always `--include-quantum-safe`; skips folders named demo, example, sample |
| `pqcheck` | pqcheck | Python, Go | |
| `qdarpan` | qdarpan | Java, Go, C | the worker adapter moves `qdarpan:location.N` into `evidence.occurrences` |
| `securin-xbom` | grep-rules | Java, Python | the registry rule sets it fetches online, frozen unfiltered at a semgrep-rules commit |
| `cipherradar` | grep-rules | Java, Python, Go, JS | runs outside the clone; Java files with its crash trigger pre-excluded via `.cradarignore` (recorded) |
| `scanoss` | grep-rules | Go, C, JS, Rust | crypto-finder with the public open rules; Java/Python rules need an API key (research key requested) |
| `ciphersift` | ciphersift | Java, Python, Go | benchmark-informed: `tailored_on` the micro projects and the pilot corpus (refinement 13) |
| `xgrep` | xgrep | Java, Python, Go, JS | |
| `cryptoscope` | – | Java | blocked: the implementation is not public; artifact requested from Moffie et al. |

---

# BF-CBOM 2.0: ground-truth-free comparison of CBOM generators (journal extension)

## Research questions
The ICPC 2026 paper introduced BF-CBOM and showed that CBOM generators disagree strongly. The journal extension asks
which generator can be trusted on real code that has no reference CBOM. It frames tool comparison as **n-way
matching** (normalise -> match -> score) and validates the scores by **inserting and removing known crypto calls** in
real projects.
- **RQ1:** do outputs conform to CycloneDX?
- **RQ2:** what do tools report?
- **RQ3:** how well can assets be matched, per kind of information (algorithm, parameters, location)?
- **RQ4:** can tools be scored and ranked without ground truth?

Decisions: mutants are hosted on the local git server; the LLM generator reads the code; the corpus is ~100 Java +
~100 Python repos, mutation runs on a ~30-repo subset.

## Status (2026-10-18)
- **Phases 1-3 are done** in code and tests. Waiting for the VM: rerun e1-icpc-corpus (new provenance, usage and memory
  fields; traceability against the mirror), run e0-stability, build the E1 corpus with `GITHUB_TOKEN` and prune it,
  check replicas.
- **Waiting for the user:** a `pyproject.toml` in the PyQuN fork (draft in `notes.md` WP6), then pin it in the `raqun`
  extra and the misc-pyqun image; an explicit DeepSeek model id (the default `deepseek-chat` is no longer on DeepSeek's
  model page and is priced as `deepseek-flash` meanwhile); human review of the micro ground truth, the hand-check
  verdicts and the `languages` lists (all AI-made); whether Go becomes a third paper language.
- **External:** the SCANOSS research key, the Cryptoscope artifact, the two-rater reviews (`vocab.csv`, matcher
  validation).
- **Small open analysis items:** CryptobomForge's IBM-draft windows are CodeQL's `contextRegion` (finding ±2 lines),
  so the exact line could be read as the window centre (one over-credit on micro-python); Jaro-Winkler candidates
  for curating the mapping table; per-field columns in `micro-run<k>.csv`; inferring `functions` from API names.
- **Next:** Phase 4, the mutation harness. Phases 5 and 6 not started.

## Phases 1-3 (done 2026-10-15 to 2026-10-18)
What exists and the decisions behind it. Numbers: `docs/findings.md`; implementation notes per work package:
`notes.md` ("Completing Phases 1-3, WP1-WP8").

### Phase 1: reproducible experiment infrastructure
Commit pinning, provenance, the git server, the batch runner with its results volume and the corpus builder, all as
described under "How it works". Redis stays the queue and live status only: hundreds of repos × tools × repetitions
plus the mutants are several GB of CBOMs. The corpus builder samples GitHub stratified by size and stars, with recent
activity and a crypto grep over first-party source only (test, vendor, example and docs paths excluded); every
exclusion is recorded with its reason (refinement 11); `make corpus-prune` drops repos a tool timed out on at E0.

### Phase 2: generators
- **LLM workers, one per provider.** The shared logic is `common/llm_cbom.py`: clone from the git server, pre-filter
  files with crypto API patterns, send line-numbered chunks with file paths, require file and line per asset,
  assemble CycloneDX 1.6 (free-text values mapped onto the enums, originals kept in `bf-cbom:<field>`;
  `PROMPT_VERSION`, `ASSEMBLY_VERSION` in the metadata). Each provider worker is a thin adapter (client, model id,
  key), so all providers share prompts and file selection and differences come from the model. Temperature 0 where
  the API accepts it, 3 repetitions, since temperature 0 is not determinism (refinement 10).
- **Costs are computed offline, never in the worker:** prices change and differ per model, cache hit and time of day.
  `analysis/data/llm_prices.yaml` is dated and sourced; `analysis/costs.py` prices the stored usage, so costs can be
  recomputed for any price date and the paper states the table version.
- **Engine independence:** tools sharing an engine or rule set are one voter (`voter_group`). The LLM workers are
  separate voters by default; merging them into one "LLM" voter (`--merge-llm`) is the sensitivity analysis, since
  they share prompts and file selection.
- **Blocked on access:** SCANOSS's Java/Python rules (API key) and IBM Cryptoscope (artifact), see the table above.

### Phase 3: analysis library (`analysis/`)
Pure Python and unit-tested; runs in the runner image or on the host for a copied export.
- **Normalise** (`normalize/`): one record per (component, occurrence), as in the paper's normalisation figure, with `group`, `family`, `algorithm`, `primitive`, `functions`,
  `mode`, `padding`, `curve`, `key_size`, `digest`, `param_set`, `iterations`, `oid`, `protocol`,
  `protocol_version`, `material`, `asset_type`, location (`file`, `line`, `line_end`, `in_scope`, `file_exists`),
  `context`, `confidence`. Every value keeps `raw`, `src` (exact JSON path) and `via` (field, parse, dictionary,
  interpretive, api, dependency, ref, derived), so RQ3 can say where a tool puts information.
  - Precedence per spec: occurrence evidence > standard fields > tool properties > name > refs/dependencies > API
    context. A more specific value of the same kind refines (EC -> ECDSA); a different one is kept as a `conflict`;
    API context, rule ids and dependencies only fill gaps; derived values are tagged `derived:<dim>`.
  - The name parser (`vocab.py`) splits tokens, matches the longest alias, decomposes composite names, knows
    JOSE/SSH/JCA identifiers; code fragments stay unmapped (never guessed), lists are `ambiguous`.
  - `conformance.py` checks every standard-field value against the declared `specVersion`'s schema
    (`spec_issues`); `vocab.csv` is the reviewable mapping table.
  - `record_id` = `<tool>:<component>:<occurrence>`, `site_id` = `<tool>:<file>:<line>`.
- **RQ1** (`schema.py`): whole-document validation against the vendored schemas (`jsonschema` with an offline
  `referencing` registry), split by `native_cyclonedx` (refinement 3); "meaningful" shares from standard fields only
  vs any source; traceability weak and strong (refinement 4).
- **Match.** Several n-way matchers over the same records, compared against each other in RQ3. They are easy to
  confuse:
  - (a) the pilot's **call-site matcher** (same file, |Δline| ≤ 2, regex families): retired 2026-10-18; its line
    window lives on in (b).
  - (b) the **exact matcher** (`matchers/exact.py`): groups by the canonical algorithm identity of a ladder level
    (`--level`, default L3) at repository level, so location-less tools take part; inside a group, located records
    are chained into call-site groups (same file, lines within `--window`); `--closure` adds implied memberships.
  - (c) **RaQuN** (`matchers/raqun.py`): PyQuN's n-way matching with a structured similarity (Jaccard over
    `<dimension>=<value>` attributes), location-free, at most one asset per tool per group. Optional until PyQuN is
    packaged. The UI's "Find Similar Components" (`misc/pyqun`, BERT over a bag of strings) is a different thing and
    stays a comparison only.
  - (d) further matchers: the interface (records in, match groups with per-dimension agreement out,
    `matchers/base.py`) must not assume (a)-(c) are the only ones.
  - Every matcher reports per group and dimension (algorithm, key size, mode, padding, curve, digest, location) how
    many voters report and agree (`base.py:agreement`); `compare.py` compares two matchers by co-membership.
- **Score** (`score.py`, RQ4), per tool × dimension × level on the exact matcher's groups: confirmation rate (share
  confirmed by at least one other voter), exclusivity, Dawid-Skene (binary EM without labels, seeded from the
  majority vote) as a second model, leave-one-voter-out with Kendall τ, bootstrap CIs over repos. Tailored (tool,
  repo) pairs are excluded (`--include-tailored`). `stability.py`: Jaccard across repeated runs and majority consensus
  sets (`--consensus`).
- **Tables** (`tables.py`), flat long-format CSVs in three layers, so new matchers only add files: **runs**
  (`runs.csv`, one row per stored run with status, timing, memory, provenance, LLM usage and `cost_usd`), **records**
  (`records-run<k>.csv`), **matches** (`groups-`, `members-`, `agreement-<matcher>-run<k>.csv`, `matchers-run<k>.csv`).
  Plus `micro-run<k>.csv`, `micro-assets-run<k>.csv`, `algorithms-run<k>.csv`, `levels-run<k>.csv`, `coverage.csv`,
  `conformance.csv`, the RQ1, score and stability tables. Join keys: (snapshot, variant, run, record_id) and
  (snapshot, variant, run, group_id); group ids restart per snapshot and variant. Full list: `experiments/README.md`.
- **Report** (`analysis/report/README.md`): plot recipes on the CSVs (UpSet of tools per asset, fill-rate heatmaps,
  ladder, ranking with CIs, cost per confirmed asset). The figures are built by the user later, following the
  `dataviz` skill.

## Phase 4: mutation harness (`mutation/`, RQ4 validation; next)
- **Snippet catalog** (`mutation/catalog/{java,python}.yaml`), about 15 assets per language: RSA-2048 keygen, EC P-256
  / Ed25519 signing, AES-128/256-GCM/CBC, SHA-1/256, HMAC, PBKDF2, TLS context. Each snippet has its expected canonical
  record, its imports and three insertion styles: direct call, wrapper function, indirection through a variable or
  constant (`alg = "RSA"; getInstance(alg)`; refinement 5, the pilot's shared blind spot).
- **Insert** with tree-sitter into an existing method that has a caller (or an entry point), or a new file in an
  existing package, so it compiles in context; never under test directories (refinement 6). Record the expected file,
  line and insertion kind (a covariate).
- **Remove:** delete or neutralise call sites that ≥2 independent voters reported, confirmed by tree-sitter queries;
  removed assets are expected to disappear. Removal candidates are easier by construction, so insertion and removal
  results are reported apart, never pooled (refinement 7).
- **Variants:** one insertion or removal per mutant; ~30 repos × ~10 mutants × 2 languages, about 600 mutants, each a
  branch on the git server; the runner scans original and mutant. Choose the subset among the smaller repos.
- **Ground truth from mutation**, per tool: insertion detected (by algorithm, parameters, location), removal reflected,
  no other changes in the diff (stability). With repeated runs, an inserted asset counts as detected only if it
  appears in most runs of the mutant and not in the original; this also exposes LLM answers from memorised popular
  repos, which cannot react to the change.

## Phase 5: experiments
- **E0 (stability):** every tool 3× on the five micro projects and ten small pilot repos
  (`experiments/e0-stability.yaml`), for determinism and run-to-run variance; needed before any agreement claim; its timings decide replicas and pruning.
- **E1 (RQ1, RQ2):** all tools on the full corpus: validity, meaningfulness and traceability rates; assets per tool and
  fill rates per dimension; the tools-per-asset distribution (exact matcher, so RQ2 does not depend on RaQuN).
- **E2 (RQ3):** matchability per dimension, exact vs RaQuN. Matcher validation: a stratified sample of ~300 match
  groups and ~200 unmatched assets labelled by 2 raters (Cohen's κ), giving matcher precision and recall.
- **E3 (RQ4, ranking without ground truth):** the agreement-based scores and rankings per dimension.
- **E4 (RQ4, validation):** mutation runs give per-tool detection rates as partial ground truth. Compare the rankings
  of E3 and E4 (Kendall τ with bootstrap CIs over repos; refinement 9). Inspect "right alone" cases: sample exclusive
  findings and validate them manually, to quantify the penalty for being right alone.
- **Threats addressed explicitly:** shared blind spots (engine grouping), inserted calls being easier than real ones
  (three insertion styles plus a manual sample), moving targets (pinned digests and a replication package),
  benchmark-informed tools (refinement 13), normalisation creating agreement (refinement 14).

## Phase 6: replication package
- Tag a release and publish the images to GHCR (`.github/workflows/images.yml`).
- Archive the corpus files, all raw CBOMs, the mutant patches, the analysis outputs and the notebooks on Zenodo
  (concept DOI in the README).
- One command, `bf-cbom experiment reproduce`, reruns the analysis from the raw results.

## Verification still to do
- **Mutation smoke test:** insert an RSA-2048 keygen into one Java and one Python repo; at least CBOMkit and CryptoScan
  report it at the recorded file and line; a removal mutant drops the asset.
- **End to end:** E0 on 5 repos from the spec to the tables with `bf-cbom experiment reproduce`.
- **On the VM:** replica scaling (`--scale` with `EXPERIMENT_REPLICAS`) and traceability against the mirror.

## Method refinements (pitch review 2026-10-15; numbers are cited from code)
1. **Unit of analysis.** Every RQ is reported at **repo level** (the set of canonical algorithm records per repo; all
   tools take part) and at **call-site level** ((record, file, line ±k); only tools with locations). The micro check
   follows the same split (`analysis/groundtruth.py:score_records`): call-site recall (the asset at the right file and
   line ±2; the inclusion bar) and repo-level recall (the asset's own family, `expected.family`, anywhere in the
   project), side by side, never pooled. The micro projects are not changed to suit a tool: plain constant algorithm
   strings are the common real-world pattern, and a tool's blindness to them is a finding.
2. **Implied assets are not disagreement.** HMAC-SHA256 implies SHA-256, PBKDF2 implies HMAC; CBOMkit expands such
   hierarchies, others don't. `implies` in `algorithms.yaml` (generic rules from a record's digest plus an explicit
   table; protocols imply no cipher suites unless named) is applied as an optional closure (`--closure`), reported
   next to the plain sets.
3. **RQ1 only for native CycloneDX output.** When our worker adapter writes the CBOM, validity would measure us;
   `native_cyclonedx` in `tools.yaml` splits the RQ1 tables.
4. **Traceability, weak and strong.** Weak: the file exists at the commit and the line is in range. Strong: lines
   line±k also contain a token from the alias table (algorithm name, provider string, crypto API identifier).
5. **Third insertion style** (Phase 4): indirection through a variable or constant separates pattern tools from
   data-flow tools.
6. **Reachable insertions** (Phase 4): only into methods with a caller or an entry point, never under tests; the
   insertion kind is recorded as a covariate.
7. **Removal bias is acknowledged, not fixed** (Phase 4): insertion and removal results are reported apart.
8. **Worker replicas.** Workers are stateless BLPOP consumers, so `docker compose up --scale worker-<tool>=N` works;
   E0 timings decide N per tool; the memory budget counts replicas (`EXPERIMENT_REPLICAS`).
9. **Statistics for few tools** (Phase 5). Kendall τ over a handful of ranks is coarse: add Spearman between agreement
   score and mutation detection rate over all tool × dimension × language cells, with bootstrap over repos; Java and
   Python are two replications of the method.
10. **LLM recall ceiling.** The pre-filter bounds what the LLM sees: keep it import-based and generous, versioned
    (`PROMPT_VERSION`) and stated as part of the tool in the paper.
11. **Corpus exclusions.** No repos from the tools' own test suites or published benchmarks, none whose crypto sits
    only in dependencies (the scope is first-party source), none exceeding a tool's timeout at E0
    (`corpus/exclusions.yaml`, `make corpus-prune`).
12. **Minimum viable paper:** the exact matcher, insertion-only mutation and both languages with the independent
    engines already integrated; RaQuN, removal mutants and Dawid-Skene are additions on top.
13. **Benchmark-informed tools.** A tool adapted while looking at our results (CipherSift: `tailored_on` in
    `tools.yaml`) has inflated scores there by construction, also in agreement-based scoring. Its tailored (tool, repo)
    pairs are marked `tailored` and left out of the ladder and the scores by default; the corpus builder excludes
    those repos; pilot numbers are reported apart; the threats state that its rules and the micro ground truth were
    both written by Claude.
14. **Normalisation can create agreement; report how much.** The vocabulary and the specs were developed on the
    outputs we evaluate, and a too eager normaliser would work against our own hypothesis that tools report
    heterogeneous things. Every agreement number is reported as a ladder (`levels`): L0 raw names, L1 case and
    separators ignored, L2 standard vocabulary (whole names, 1.7 `algorithmFamily`, JOSE/SSH/JCA identifiers), L3
    composite names parsed, L4 everything tool-specific; L4lit leaves out the interpretive dictionary entries
    (judgements rather than synonyms, `interpretive: true`); parameters enter from L2. Hypotheses are tested at
    L2/L3, the L0 -> L4 gap is reported as representational heterogeneity, rankings are checked across levels. The
    vocabulary is frozen before E1 (Rules); two raters review `vocab.csv`.
15. **Every tool gets its best fair run; no result may be BF-CBOM's fault** (decided 2026-10-17).
    - *Invocation:* the tool's documented best invocation for the repo at hand (queries, project types, flags),
      current pinned releases, robust workers (cwd outside the clone, timeouts, failure reporting), and everything
      that changes results in `tool_version` or the run notes.
    - *No repairs:* no source patches, no rewriting of a tool's input or output, no rule filtering, no files added to
      scanned repos. A crash is a result about the tool; a minimal workaround that lets it produce output
      (CipherRadar's `.cradarignore`) is allowed and recorded.
    - *Analysis:* our analysis reads every location format a tool uses.
    - *Check a 0 before accepting it:* rerun the tool in a throwaway container with its debug output and find the
      cause; distinguish *by design* (cdxgen gives no Java locations) from *our setup* (cdxgen without `-t` never
      analysed Python or Go).
    - *Paper:* state the per-tool invocation in the replication package, and that the micro numbers of 2026-10-15
      were partly our setup errors (`docs/findings.md`).
