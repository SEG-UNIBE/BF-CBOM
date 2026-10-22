# Experiments

Batch runs for the journal extension (see `CLAUDE.md`, Phase 1). Specs live here and are mounted
read-only into the `runner` container; everything they produce stays in Docker volumes.

```
experiments/
├── *.yaml       # experiment specs
├── corpus/      # pinned repository lists, exclusions and micro fixtures
└── results/     # exported ZIPs and extracted results (ignored by Git)
```

```
make experiment EXP=experiments/mutation-smoke.yaml   # pin commits, mirror to gitserver, run, persist
make experiment-status NAME=mutation-smoke            # Redis progress + what is on disk
make experiment-export NAME=mutation-smoke            # experiments/results/mutation-smoke.zip (only way data reaches the host)
make experiment-list                                     # experiments stored on the volume
make experiment-delete NAME=mutation-smoke            # remove one from the volume (asks first)
make corpus ARGS="--per-language 100"                    # stratified corpus -> results volume corpus/corpus.yaml
make board                                               # live board of the stack: experiments, workers, queues
make board ARGS=--once                                   # one snapshot of it, then exit
```

**Watching a run.** `make experiment` shows its own progress while it runs (a line per tool with its count, the job
it runs and for how long). `make board`, in a second terminal, shows the whole stack (an experiment's bar counts the
results the runner has stored, so after `ARGS=--no-wait` it moves once `make experiment-collect` runs):
- every running experiment with a bar, results stored out of expected, its wave, the failures so far and the time
  left (per tool its jobs left times its mean duration so far, less what its running job has taken; the slowest tool
  ends the batch, `≥` while a tool has no result yet; the runner's own progress shows the same);
- every worker: busy, idle or down, the job it runs (repo, run, experiment) and for how long, the container's RAM
  against its limit, the jobs queued for it and its last result;
- whether Redis, the git server and the UI answer, and the host's RAM.

`Ctrl+C` closes the board; the experiment and the stack go on (`make up-prod` opens the board by itself once the
stack is up; `make logs` shows the containers' raw logs, `make down` stops the stack).

When a run ends it prints an overview (`misc/cli/overview.py`): runs stored out of expected, per tool the counts per
status, median and longest duration, peak RAM against the limit and OOM kills, the LLM token usage with an estimated
cost, every run that is not ok with its most telling error line, and the commands to retry, resume or export.
`make experiment-status` shows the same plus the live progress from Redis, `make experiment-list` one line per
experiment.

## Specs in this folder

The name says the kind of spec, then what it runs on: `bar-` (the bar exam on ground truth), `stability-` (repeated
runs), `corpus-` (real repositories as they are), `mutation-` (real repositories with their mutants). Renamed from
`e0-`, `e1-` and `e4-` on 2026-10-22, so that no experiment number reads as a research question.

| Spec | Tools | Repos | Purpose |
|---|---|---|---|
| `bar-micro.yaml` | all 15 tools, once | the 3 micro projects (`experiments/corpus/micro`: Java, Python, Go) | the voter bar: the micro check against the generated ground truth (`make analysis ARGS="micro bar-micro"`), which informs the role decision in `common/tools.yaml` (at least 7 of 14 in their functions in one paper language); 45 jobs |
| `stability-pilot.yaml` | the same 15, 3 repetitions | 3 micro projects + the 10 fastest pilot repos | run-to-run variance (`stability.csv`) and consensus sets for the scores; 585 jobs |
| `corpus-pilot-small.yaml` | the same 15 | the 12 pilot projects in Java, Python and Go (`experiments/corpus/pilot-2026-10-08.yaml`) | first real workload: location-aware agreement on the pilot's pinned projects; large repos (Kafka, Keycloak) push CBOMkit towards its timeout |
| `corpus-pilot-medium.yaml` | the same 15, once | 60 manually selected projects, 20 each in Java, Python and Go (`experiments/corpus/pilot-2026-10-22.yaml`) | second pilot, pinned to default-branch HEADs on 2026-10-22; 900 tool/repository pairs |
| `corpus-icpc.yaml` | the same 15 | 23 projects of the ICPC 2026 paper (`experiments/corpus/icpc-2026.yaml`, HEAD of 2026-10-18) | held-out counterpart of the pilot corpus: none of these repos was used to tune the vocabulary, the specs or CipherSift; six are marked `expect_crypto: low` (negative cases) |
| `mutation-smoke.yaml` | all 15 tools | pyjwt, jjwt, golang-jwt (pilot), 3 mutants each | the mutation harness end to end: the RSA-2048 keygen inserted in each style; CBOMkit and CryptoScan should find the direct one at its line (`mutation.csv`) |

LLM providers are outside the current experiment scope; their implementation remains available for possible later use.
The 15 tools include three observers: tools whose findings are analysed but do not confirm other tools' findings
or receive a rank (see [Observers](../analysis/README.md#observers)). The micro check reports entry-bar verdicts
but does not edit roles in `common/tools.yaml` or the specs.
Job totals include tool/repository pairs recorded as `unsupported` without scanning. `stability-pilot` still includes
four historical pilot repositories outside the paper languages (TypeScript, JavaScript and C).

**Second pilot (60 repositories).** On the 64 GB VM, after `make up-prod`:

```sh
make experiment EXP=experiments/corpus-pilot-medium.yaml EXPERIMENT_MEMORY_BUDGET_GB=0
make experiment-status NAME=corpus-pilot-medium
make experiment-export NAME=corpus-pilot-medium   # generates the report and experiments/results/corpus-pilot-medium.zip
```

The corpus records the full repository names and commits. It is a manual selection, separate from the
stratified corpus built by `make corpus`. Rerunning the command resumes this experiment at the pinned commits.

After a change to one worker, `make experiment EXP=experiments/mutation-smoke.yaml ARGS="--rerun <tool>"` redoes
its runs on three small repos and their mutants, one repo per paper language; its micro check, the same on
`bar-micro.yaml` (3 jobs). The corpus run on the full corpus (Java, Python and Go) and the mutation run on its
subset (see `CLAUDE.md`, Phases 4 and 5) have no specs yet; the corpus spec needs the corpus from `make corpus`
first, pruned with
`make corpus-prune ARGS="--in /data/results/corpus/corpus.yaml --from stability-pilot"` to drop repos a tool
timed out on.

## Spec format

Each spec is one `experiments/<name>.yaml`. An example with every field:

```yaml
name: e1-corpus            # [A-Za-z0-9._-]
tools: [cbomkit, cdxgen, cryptobomforge, cryptoscan]
repetitions: 3             # run_index 1..n per (repo, variant, tool)
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
mutation:                  # optional: every repo also gets insertion mutants as variants (E4, below)
  per_repo: 10             # mutants per repo (fewer when not enough (snippet, style) pairs fit a host)
  styles: [direct, wrapper, indirect]
  snippets: all            # or a list of catalog ids (mutation/catalog/<language>.yaml)
  seed: 1
```

Rerunning a spec is resumable: runs whose `run<k>.meta.json` exists are skipped, commits come from the
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

**Mutants (`mutation:`, Phase 4).** For every repo of a spec with a `mutation:` block the runner makes `per_repo`
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

**Which repos an experiment used** is answered by one file, its record `_experiment.yaml`: the spec as run, every
repo with the branch and commit it was resolved to (a fixture with its `path` and published commit), the tools, the
repetitions and the `mutation:` block. `experiment run` writes it once the snapshots resolve, and a resumed run
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
file listing. Every `run<k>.meta.json` also records the worker container's peak working set during the job (sampled
every second, the figure `docker stats` shows), its memory limit and the OOM kills during the job (`peak_memory_mb`,
`memory_limit_mb`, `oom_kills`), when the job was enqueued, started and finished (`enqueued_at`, `started_at`,
`finished_at`), the BF-CBOM commit the worker image was built from (`bfcbom_commit`, `-dirty` = uncommitted changes)
and, for LLM workers, the structured `usage` (requests, input / cached / cache-write / output / reasoning tokens, the
model ids the provider answered with; kept on failed runs). `_run-log.json` also records the runner's commit and the
image ids of the workers (by content) per session. There is no backward compatibility with experiments stored before
these fields existed: rerun them.

**Memory budget (waves).** Every worker takes one job at a time, so a batch needs at most the sum of the
compose memory limits of the workers that have jobs queued (`memory_gb` in `common/tools.yaml`, kept equal
to `docker-compose.yml` by a test). `EXPERIMENT_MEMORY_BUDGET_GB` (Makefile, default 14 for a laptop whose
Docker Desktop VM has ~20 GiB) caps that sum: the runner packs the tools into waves (largest first), feeds
one wave, collects it, then feeds the next; workers of later waves sit idle meanwhile. The wave count depends on
the selected tools, their limits and replicas; the runner prints the plan. A tool above the budget runs alone
with a warning, so the budget is not a hard cap. For the paper's runs on the 64 GB VM, use 0 (one wave,
everything in parallel):

```
make experiment EXP=experiments/corpus-pilot-small.yaml EXPERIMENT_MEMORY_BUDGET_GB=0   # per call
```

or change the default in the Makefile once per machine. The wave plan is printed before anything is
enqueued and stored in the experiment's Redis status (`waves`, `wave`).

**Watching a run.** On a terminal `run` and `collect` keep a live board at the bottom (`misc/cli/progress.py`): one
line for all results (done/total, remaining time, elapsed time, the wave and the statuses so far), then one per tool
with its count and remaining time (`progress.forecast`: its jobs left times its mean duration so far; the batch ends
with its slowest tool), the job it runs now and for how long (the longest-running one first, from the start times the workers
write to Redis), how many wait and what failed; tools of a later wave wait with their clock still. Every stored
result still prints its own line above the board (status, tool, snapshot, run, duration, path). Pinning and
mirroring count the repos the same way, and so does `corpus build` per language (repos picked out of the quota, the
cell searched or the repo cloned for the crypto check). Off a terminal only the lines print. `--no-wait` enqueues only the
first wave; after `make experiment-collect NAME=<exp>` rerun `run` for the next one. Jobs left pending by an interrupted
run are already in the queues and are collected in wave 1 regardless of the budget.

## Normalisation

**Why.** Generators describe the same crypto asset in different ways, so their CBOMs cannot be compared as they are.
The example of the paper figure (`context/fig-cbom-normalization_cyclonedx.pdf`): three tools report AES-CBC with a
256-bit key in `src/KdbxFile.cs` line 120:

| Tool | What it writes |
|---|---|
| tool 1 | name `AES-256-CBC`, primitive `block-cipher`, occurrence `src/KdbxFile.cs`, line 120 |
| tool 2 | name `AES`, custom properties `t2:mode = CBC`, `t2:file = KdbxFile.cs`, `t2:line = 120` |
| tool 3 | name `aes`, `parameterSetIdentifier: "256"`, occurrence `./src/KdbxFile.cs:121` |

Compared raw, the three share almost nothing. Normalised, they agree on the family (AES), the file and partly on mode
and key size, and differ by one line. Normalisation translates every CBOM into one record format (CBOM^norm), so that
the n-way matchers compare like with like, per dimension (algorithm, parameters, location).

**Three kinds of files.**

| What | Where | Holds |
|---|---|---|
| one adapter spec per tool | `analysis/adapters/<tool>.yaml` | the format the tool declares, which components count as findings, where each dimension sits in the tool's output (sources in order of precedence), the tool's own spellings (dictionary), notes on its quirks. Specs inherit from `_cyclonedx.yaml` (standard CycloneDX 1.6/1.7 paths), `_ibm-cbom-draft.yaml`, `_cbomkit.yaml`, `_llm.yaml` and state only their differences. Every integrated tool needs one (a test enforces it). |
| one shared vocabulary | `analysis/data/algorithms.yaml` + `analysis/data/cyclonedx/` | canonical names from the vendored CycloneDX 1.7 cryptography registry (families such as `AES`, `RSAES-OAEP`, `ECDSA`, `SHA-2`; curves with their OID-equal aliases, `secp256r1` = `P-256` = `prime256v1`), enum spellings, JOSE/SSH/JCA identifiers, an API table, the meaning of `parameterSetIdentifier` per family. Spellings traceable to standards or official APIs belong here regardless of how many tools use them; labels invented by a tool belong in its spec. |
| one target format | `analysis/data/cbom-norm.schema.json` | the shape of a normalised file and record, the same for every tool; every output validates against it |

*Worker adapters* (`common/cbom_adapters.py`, `workers/*`) are something else: they make a tool's raw output usable
CycloneDX at scan time. Adapter specs read CycloneDX into records, offline.

**From a CBOM to records.**

1. *Findings.* The spec decides which components count: crypto assets always; libraries only when the tool marks
   them as findings (CipherRadar's library imports); dependency inventories (cdxgen, pqcheck) are skipped.
2. *Locations.* One record per (component, occurrence); `path:line`, `path#line`, absolute temp-clone paths and
   IBM-draft line windows (`line` ... `line_end`) become a repo-relative file and line. A component without a
   location gives one record without location.
3. *Values.* The spec's sources are read in order of precedence: occurrence evidence (CryptoScan's matched `symbol`),
   standard fields, tool properties, the component name, refs and dependencies between components, the API in an
   occurrence's context. Each yields canonical values through the vocabulary; composite names are parsed
   (`AES-256-GCM` -> AES, 256, GCM; `HmacSHA512` -> HMAC, digest SHA-512; `RSA/ECB/OAEPWithSHA-256AndMGF1Padding` ->
   RSAES-OAEP, OAEP, digest SHA-256).
4. *Merge.* The first value per dimension wins. A later, more specific value of the same kind refines it (EC -> ECDSA,
   SHA-2 -> SHA-256); a different one is kept as a conflict (CipherRadar's `algorithmFamily` SHA-2 on a component named
   SHA-1). Supporting evidence (API context, rule ids, dependencies) only fills gaps.
5. *Nothing is guessed.* Unknown spellings and code fragments (`parseCsvMap`, `SHA-" + this.strength`) stay unmapped,
   a name listing several algorithms (`RSA, ECC, AES`) is marked ambiguous, and our own derivations (group from
   family, key size from curve, default primitive) are tagged `derived:<dim>`.

**What a record keeps.** The canonical values (`group`, `family`, `algorithm`, `primitive`, `functions`, `mode`,
`padding`, `curve`, `key_size`, `digest`, `param_set`, `iterations`, `oid`, `protocol`, `protocol_version`,
`material`, `asset_type`, `purl`, `confidence`), the location (`file`, `line`, `line_end`, `in_scope`, and
`file_exists` when the git mirror is mounted), the occurrence `context`, and per dimension `raw` (the value as found,
or the whole text that was parsed), `src` (the exact JSON path) and `via` (field, parse, dictionary, api, dependency,
ref, derived). So matchability can be measured with and without normalisation, and it stays visible where a tool puts
its information (structured field, custom property, only the name).

**Format conformance.** Reading is lenient (any version, the IBM draft, fields of later versions), but every value
read from a standard field is checked against the official JSON schema of the CycloneDX version the CBOM declares.
Deviations become `spec_issues` on the record: `field` (the version does not define the field, e.g. 1.7
`algorithmFamily` in a 1.6 CBOM), `enum` (`padding: PSS`, `assetType: key`), `type`, `deprecated` (1.6 `curve` in a
1.7 CBOM); per document `dialect` (not CycloneDX) and `version`. This feeds the RQ1 questions "which tool reports in
what, and where does it violate its declared standard"; a full validation of whole documents is a separate step.

**How much agreement does normalisation create?** A normaliser developed on the outputs it evaluates could manufacture
agreement and work against our own hypothesis that tools report heterogeneous things. So agreement is reported as a
ladder (`levels`, below; CLAUDE.md method refinement 14). The ladder varies only *when two reports count as the same
algorithm*; the unit stays fixed: an algorithm is its identity under the full normalisation (L4), so every level's
share is a fraction of the same population. Two records match at a level when they are equal under any of the key
functions up to that level (cumulative), so a match is never lost at a higher level, every tool's share can only grow
from L0 to L4, and each step's gain is one normalisation step:

| Level | Two reports match when ... |
|---|---|
| L0 | the component names are identical as reported |
| L1 | ... or equal with case and separators ignored (`SHA-256` = `sha256`) |
| L2 | ... or map to the same entry of the standard vocabulary: whole names and CycloneDX 1.7 `algorithmFamily` through the registry and JOSE/SSH/JCA identifiers (`DESede` -> 3DES, `HS256` -> HMAC) |
| L3 | ... or parse to the same algorithm (`AES-256-GCM` -> AES) |
| L4 | ... or get the same identity from everything tool-specific: spec sources (symbols, properties, refs, dependencies), the tool's own labels in its dictionary, API context |

A worked example with one record per tool, and the policy behind the specs: `analysis/README.md`.

The records of a repository fall into clusters at each level, one per key (the records whose identity at that level
or below is the key; clusters never merge through a record that has several keys); a tool's algorithm is confirmed
at a level when a cluster holding one of its records also holds a record of another voter group. The L0 share reads
as "another tool uses a verbatim identical spelling". Hypotheses are tested at L2/L3, the L0 -> L4 gap is reported as
representational heterogeneity, and rankings are checked for stability across levels. Parameters (key size, mode, padding, curve, digest) get the same
ladder from L2 on over (algorithm, value) pairs with values from any source: a pair is confirmed when the tool's value
is admitted at the level (L2 from a standard field, L3 also parsed from the name, L4 from any source) and a record of
another voter group in the same cluster carries the same admitted value. `--closure` (refinement 2) adds the
algorithms a record implies (its digest, the `implies` table: HMAC-SHA256 -> HMAC and SHA-256, PBKDF2 -> HMAC) as
items of the tool, matchable from L3 on (an implication is parsed knowledge; it never merges clusters), so tools that
expand such hierarchies and tools that do not are compared on equal terms; the plain and the closed numbers are
reported apart, never pooled. The per-tool and pooled numbers of the pilot and the ICPC corpus are in
`docs/findings.md` ("Export review 2026-10-19"): most of the gain comes from the standard vocabulary and parsing, the
tool specs add a few points.

**Frozen.** The vocabulary and the adapter specs were developed on the pilot corpus (the development set); the main
corpus is held out. Since 2026-10-18 they are frozen: every change bumps `version` in `analysis/data/algorithms.yaml`
and adds an entry with the reason and the fingerprint (`python -m analysis.freeze`) to `analysis/data/CHANGELOG.md`;
`tests/test_vocab_frozen.py` fails otherwise. The versions are recorded in every `.norm.json`; a two-rater review of
`vocab.csv` (Cohen's κ) is an option for the paper, not decided.

**Curating and extending.** After `normalize`, read the `unmapped` and `partial` rows of `_ANALYSIS/vocab.csv`. A
spelling traceable to a standard or an official API (the registry, RFC 7518 JOSE, SSH, JCA names, OS and library
APIs) goes into the shared vocabulary, however many tools use it; a label a tool invented (rule names, category
labels) goes into the tool's dictionary, read as it says and with a comment naming its source; what a tool's own
metadata already classifies is read structurally (`rule_crypto_segments`), never listed; code identifiers are
recognised by shape (`api`); an ambiguous spelling (`rand`) and garbage stay unmapped (`undefined`). Judgements about
what a label "really" means are not made (`interpretive: true` exists in the engine but no spec uses it, a test
enforces this). Bump the version, add the changelog entry and rerun. A new tool gets `analysis/adapters/<tool>.yaml` (inherit `_cyclonedx`, state its quirks), a small fixture in
`tests/fixtures/normalize/` (open-source tools only) and a test in `tests/test_normalize.py`.

## Analysis

The analysis (`analysis/`) works offline: it reads the stored CBOMs from the results volume and needs neither Redis
nor running workers. It uses only runs with status `ok` (failed, timed-out and `unsupported` runs are skipped) and,
by default, only algorithm assets and repetition 1. Every agreement analysis (RQ1, ladder, matchers, scores,
stability, micro) reads the `original` variants only; mutants are normalised too and feed the E4 tables alone, so a
mutated repository is never counted once per mutant.

```
make analysis ARGS="report stability-pilot"     # all of the below in one pass, plus _ANALYSIS/_SUMMARY.md (Markdown tables)
make analysis ARGS="normalize stability-pilot"  # every CBOM -> normalised records (run<k>.norm.json next to it)
make analysis ARGS="schema stability-pilot"     # RQ1: schema validity, meaningful shares, traceability (--fetch on a laptop)
make analysis ARGS="levels stability-pilot"     # how much agreement normalisation creates: L0 (raw) to L4 (tool specs)
make analysis ARGS="micro bar-micro"          # score each tool against the micro projects' ground truth, two levels
make analysis ARGS="tables stability-pilot"     # flat CSV tables (runs, records, exact-matcher groups, micro) for pandas/R
make analysis ARGS="score stability-pilot"      # RQ4: confirmation, exclusivity, Dawid-Skene, leave-one-out, CIs; stability
```

**`report <exp>`** normalises every CBOM once and writes the CSV tables for the analyses below, then
`_ANALYSIS/_SUMMARY.md`: Markdown tables for a first look (runs per tool, the micro inclusion matrix and recall at two
levels, who finds each micro asset, fill rates and spec issues, the L0-L4 ladder, how many voter groups report each
algorithm per repository, LLM token counts, the list of files). The summary is rendered only from the CSV files, so
it never shows anything they don't hold; `python -m analysis.summary <exp>/_ANALYSIS/` rebuilds it from an export.
Its numbers carry `'` between thousands (13'043); the CSVs keep plain numbers. Each report clears and rebuilds
`_ANALYSIS/`. The standalone `micro` command also writes `micro-run<k>.json`; `report` writes its micro results
as CSV tables instead.
`experiment export` runs `report` first (skip with `--no-report`), so every zip carries current tables. On a
terminal both show a live line per step (done/total, remaining time, elapsed time and the run being worked on), so a
long step reads as working, like a run's board ("Watching a run"); off a terminal each step prints one line when it
ends.

**`normalize <exp>`** reads every stored ok CBOM (all repetitions, including mutants) into normalised records with
its tool's adapter spec, as described in [Normalisation](#normalisation). It writes the normalised files and the
first two diagnostic tables below. `tables` (also part of `report`) writes the per-run coverage, units and
conformance tables. The diagnostic tables aggregate original variants only, so mutants do not multiply counts.

| File | Content |
|---|---|
| `<snapshot>/<variant>/<tool>/run<k>.norm.json` | next to `run<k>.json`: declared format/version, adapter spec and versions, the original's sha256 (detects a stale file), stats, the records |
| `_ANALYSIS/normalize.json` | per tool over all repetitions of original variants (the normaliser's diagnostics): formats declared, assets per asset type, where values came from (standard field, property, occurrence, name, API context), spec issues, conflicts, top unmapped values |
| `_ANALYSIS/vocab.csv` | one row per (tool, source, raw value): status (`mapped`, `partial`, `unmapped`, `ambiguous`, `known`, or the entry's `kind`: `api`, `non-crypto`), what it mapped to, leftover tokens, via, interpretive, kind, count; the reviewable mapping table |
| `_ANALYSIS/coverage-run<k>.csv` | one row per (tool, dimension), run k, original variants (the same CBOMs as `units-run<k>.csv`): algorithm assets (CBOM components) with a value the tool reported vs only one we derived, as counts and shares of the assets (fill-rate heatmaps); every row also carries the tool's `assets` and the assets per resolution (`mapped`, `ambiguous`, `api`, `non-crypto`, `undefined`) |
| `_ANALYSIS/units-run<k>.csv` | one row per tool, run k, original variants: how it packages its findings: `assets` (algorithm components), `records` (their occurrences), `items` (distinct L4 identities of its mapped records per repository, summed), records_per_asset_median / _max, multi_occurrence_share (assets at more than one place), assets_per_item (above 1: one component per finding), unlocated_share (assets without a file), role (`analysis/README.md` "Packaging per tool"), loc_line / loc_window / loc_import / loc_file / loc_none / loc_unread (algorithm records by the kind of place they name, `analysis/README.md` "Units") |
| `_ANALYSIS/units-repo-run<k>.csv` | one row per (tool, repository), run k, original variants: `assets`, `records` and `items` of that CBOM as in `units-run<k>.csv` (whose counts are these rows summed), with `role` and `language`; the summary shows them as a repository x tool matrix |
| `_ANALYSIS/conformance-run<k>.csv` | one row per (tool, scope, issue kind), run k, original variants: `asset` scope counts assets with a value read from standard fields that break the declared version's schema (field, enum, type, deprecated), `document` scope counts CBOMs (dialect, version); tools without issues have no row |

**`schema <exp>`** answers RQ1 (`analysis/schema.py`): every CBOM that declares a CycloneDX version with a vendored
schema (1.6, 1.7) is validated as a whole document with `jsonschema` (offline, `referencing` registry), reported per
CBOM in `schema-run<k>.csv` (valid, error count, error kinds `<keyword>:<path>`); tools whose CBOM our worker assembles
(`cbom_by: bf-cbom` in `common/tools.yaml`, the LLM workers) are reported apart. `meaningful-run<k>.csv` gives, per tool,
CycloneDX asset type and that type's own fields (`analysis/schema.py:MEANINGFUL_FIELDS`: an algorithm's primitive,
parameter = parameter set or curve, mode, padding, functions, OID, security levels; a key's type, size,
algorithm, format; a certificate's subject, issuer, validity, signature algorithm, public key, format; a protocol's
type, version, cipher suites; algorithm assets have no dedicated key-size field), the share of assets carrying a value in the standard
fields and, where the normaliser reads such a value, from any source (the standard fields included, so it is never
lower). `traceability-run<k>.csv` gives per (snapshot, tool, asset type) how
many records name a file (`located_file`), a file and a line (`located_line`), how many of those with a line exist at
the pinned commit with the line inside the file (weak) and how many also have a vocabulary token within ±2 lines
(strong, refinement 4); `related-crypto-material` (keys, secrets) is kept apart from algorithms. In `_SUMMARY.md`
every asset type has its own table under `#### Asset type`, CycloneDX's four in the standard's order (algorithm,
certificate, protocol, related-crypto-material), then `unknown` for values outside them. The traceability check needs
the snapshot's files: on the VM the git mirror; on a laptop with a copied export `--fetch` pulls each pinned commit once
into `SNAPSHOT_CACHE_DIR` (default `~/.cache/bf-cbom/snapshots`, one shallow `git fetch` per repo). `report` runs all
of this too (`report --fetch`).

**Analysing a copied export on the host.** The analysis only reads files, so an export unzipped or copied into
`experiments/results/` can be analysed without Docker. Install the analysis dependencies with `uv sync --extra raqun`, then run
`RESULTS_DIR=experiments/results uv run bf-cbom analysis report <exp> --fetch`.
The VM runs the experiments; the laptop re-runs the analysis with other options.

**RaQuN** (matcher (c), `analysis/matchers/raqun.py`) runs next to the exact matcher, always in the runner image
(the `raqun` extra, PyQuN from the user's fork) and on the host after `uv sync --extra raqun` (`--raqun/--no-raqun`
forces it): tools are models, a tool's distinct canonical assets are elements, built from the exact matcher's
records (mapped, with an L4 identity), with `<dimension>=<value>` attributes (the identity at the level and the
standard's dimensions, not our `group`), RaQuN's Jaccard on them is the per-field agreement;
`groups-raqun`/`members-raqun`/`agreement-raqun-run<k>.csv` have the same columns as the exact tables (at the
scoring level), `matchers-run<k>.csv` compares the two partitions per repository and level (co-membership pair
Jaccard, share of groups that sit inside one group of the other matcher), and RaQuN's rows in
`matchlevels-run<k>.csv` put it next to the exact matcher level by level (its attributes are the identity and the
values admitted at that level).

**`score <exp>`** scores and ranks the tools without ground truth (RQ4, `analysis/score.py`) on the exact
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
posterior of its items). `loo-run<k>.csv` recomputes the ranking without each voter group with Kendall's tau against
the full ranking. Pairs a tool was tuned on are excluded (`--include-tailored` keeps them). Observers (`role:
observer` in `common/tools.yaml`: qelens, cryptarium, qScan) are scored like every tool and confirmed by the voters,
but confirm nobody, make nothing non-exclusive, are no Dawid-Skene annotators and get no rank (`role` column; every
voter number is the same with and without them); `--observers-vote` makes them voters. `stability.csv` covers
every (tool, repository) with repeated runs: Jaccard of the algorithm sets between runs, the record-count CV and the
consensus set (algorithms in more than half of the runs); with repetitions the scores use that consensus set
(`--no-consensus` scores run k alone). LLM costs come from the stored token usage and the dated price table
`analysis/data/llm_prices.yaml` (`cost_usd`, `priced_model`, `price_period`, `price_table_version` in `runs.csv`; the
summary adds cost per repository, per item and per confirmed item).

**`levels <exp>`** computes the normalisation ladder (L0-L4, see [Normalisation](#normalisation)): per repository the
items each tool reports (`analysis/README.md` "Units"), and the share that a tool of another voter group
(`voter_group` in `common/tools.yaml`) also reports. It prints a tools x levels table and writes
`_ANALYSIS/levels-run<k>.csv` (dimension, level, tool, voter_group, role, items, confirmed, share; tool `all` pools
the voters, an observer has rows of its own; `dimension` is `algorithm` or a parameter: key_size, mode, padding,
curve, digest, which exist from L2 on) and `_ANALYSIS/items-run<k>.csv`: one row per (snapshot, variant, tool, item;
`unit` = its L4 identity) with the tool's voter group and role, whether the tool was tuned on the repo (`tailored`),
whether the row is only implied (`implied`, with `--closure`), the repo's language, the number of records and
distinct spellings behind it, and per level the voter groups in the item's largest cluster (`k_L0` .. `k_L4`;
observers are no voter groups, so an item only observers report has k 0). That table is location-free, so every tool
takes part; it is the basis for UpSet plots and the tools-per-item distribution. Options: `--run K` for another repetition, `--merge-llm` (every LLM worker one
voter), `--observers-vote`, `--include-tailored`, `--closure` (refinement 2).

**`micro <exp>`** scores every tool on the micro projects (`experiments/corpus/micro`, repos named `<owner>/micro-<language>`)
against `experiments/corpus/micro/ground_truth.yaml`, 14 expected items per project (a catalog snippet in a function of its own;
the file calls them `assets`) plus 4 decoys (code that names or resembles cryptography without using it). Projects and
ground truth are generated from the snippet catalog by `uv run python -m mutation.micro` (never edited by hand; a test
fails when they drift; the ground truth's header states the rules). Scored from the normalised records
(`analysis/groundtruth.py:score_records`) at two levels (CLAUDE.md, refinement 1). It prints one row per project and
tool, including tools that did not run there (unsupported, failed):

| Column | Meaning |
|---|---|
| strict | call site: expected items with a record of their group on one of their lines (the body of their function), out of 14; for a key generation a key record of the group counts too |
| lenient | call site: also a record of unknown family there |
| repo | repository level: expected items whose group the tool reports anywhere in the project, location ignored; items sharing a group are found together. Needs a known family, so it can be lower than lenient |
| precision | correct locations out of all the tool reported in the micro source file; one is correct on an expected item's line or a line the ground truth marks as related or accepted (a sub-use such as a digest, an import of the item's API) |
| decoys | the decoys with a crypto record on their lines: false positives with ground truth |
| F1 | harmonic mean of the lenient recall (over the expected calls) and the precision (over the reported places); 0 when no call was found |
| bar | `pass` when the tool found at least half of the calls, lenient (the entry bar, 7 of 14) |
| missed | ids of the expected items the tool did not find (lenient) |

After the rows the command lists each tool's verdict over all its projects: `passed` (the entry bar cleared in at
least one language), `disqualified` (scored and below the bar wherever scored; the candidates to leave out of the
further experiments) or `not judged` (no scored run). The same verdict is the `entry_bar` column of
`micro-run<k>.csv` and of the summary's entry-bar table.

A location window (CryptobomForge's IBM-draft `detectionContext`, the finding ±2 lines) counts at every line it spans.

The former `sites` command is retired. `tables` and `report` write the exact matcher's repository and call-site
groups (`scope: repo` / `site`) into the same `groups-exact-run<k>.csv`; `--window` controls the line distance
(default 2). See the matcher description below.

The commands write to the experiment's folder on the volume:

```
<exp>/_ANALYSIS/
├── _SUMMARY.md                            # report: Markdown tables of everything below, for reading
├── micro-run1.json                       # micro: per (project, tool), status, verdict, recalls, precision, F1, hits, misses, false sites and decoys
├── runs.csv                              # tables: one row per stored run (all repetitions)
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
├── tables-run1.json                      # tables: settings used, row counts, versions, the voter map (group, role, engine per tool)
├── normalize.json                        # normalize: diagnostics per tool over every run (sources, spec issues, conflicts)
├── schema-run1.csv                       # schema (RQ1): one row per CBOM: declared version, valid, errors, error kinds
├── meaningful-run1.csv                   # schema (RQ1): per tool and dimension, assets with a value from standard fields / any
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
full group, member and agreement tables are written at the scoring level (`--level`, default L3), the
per-level summary `matchlevels-run<k>.csv` for all five levels (L0-L4). Inside a group the located records form call-site groups
(same file, lines within `--window`, default 2; a window record spans `line` .. `line_end`). Per group and dimension
(key size, mode, padding, curve, digest, location) the agreement table says how many voters report a value admitted
at the level and how many carry the majority value. A member whose own identity differs from the group's key joined
through a lower level (`matched`); `--closure` lets a record join the groups of the algorithms it implies (`implied`
member rows). The settings are recorded in `tables-run<k>.json`. The tables come in three layers, so further matchers only add
files and never change these:

| File | Layer | One row per | Main columns |
|---|---|---|---|
| `runs.csv` | runs | stored run, every repetition, incl. failed, timed-out and `unsupported` | repo, snapshot, commit, variant, variant_commit, language, tool, run, tailored (the tool was tuned on this repo), status, duration_sec, size_bytes, error (the most telling line), peak_memory_mb, memory_limit_mb, oom_kills, enqueued_at, started_at, finished_at, queue_sec, received_at, tool_version, image_ref, bfcbom_commit, bom_format, spec_version (as the CBOM declares), components, crypto_assets and, by the declared `assetType`, algorithm_assets, certificate_assets, protocol_assets, material_assets, other_assets, plus located_file_assets and located_line_assets (crypto assets with a file, and with a file and a line, in the CBOM), expect_crypto (from the spec), workaround (recorded setup workarounds, such as CipherRadar `.cradarignore`); for LLM workers requests, requests_failed, chunks_total, chunks_sent, chunks_skipped (over `LLM_MAX_CHUNKS`, never sent), input_tokens, cached_input_tokens, cache_write_tokens, output_tokens, reasoning_tokens, models_answered, cost_usd, priced_model, price_period, price_table_version |
| `records-run<k>.csv` | records | normalised record: one (component, occurrence) of one tool | snapshot, variant, tool, run, location_kind (`line`, `window`, `import`, `file`, `none`, `unread`), record_id (`<tool>:<component>:<occurrence>`), site_id (`<tool>:<file>:<line>` when located), every record dimension, resolution (`mapped`, `ambiguous`, `api`, `non-crypto`, `undefined`; only `mapped` records become items), `raw_*`, `src_*`, unmapped, conflicts, spec_issues |
| `groups-exact-run<k>.csv` | matches | group of the exact matcher: `repo` scope = one cluster per key at the level (its members plus attached records), `site` scope = its located records at one call site | matcher, repo, snapshot, commit, variant, run, group_id (`E00001`, clusters sorted by key; sites `E00001.001`), n_tools (tools, observers included; k counts voter groups: `voter_groups`), tools, language, level, scope, key (the level's key: a raw name at L0, an identity at L4), voters, voter_groups (observers are none), n_records, n_sites, units (L4 identities of the voting members; of all members when only observers are in), n_units, n_attached, located_voters, colocated_voters (repo scope; voters only), file, line_min, line_max, parent (site scope) |
| `members-exact-run<k>.csv` | matches | (group, record) pair | matcher, snapshot, variant, run, group_id, record_id, tool, unit (the record's L4 identity), implied (attached through `--closure`), matched (its own identity differs from the group's key: joined through a lower level), attached (a generic or implying record, no key of its own in the group) |
| `groups-raqun-run<k>.csv`, `members-raqun-run<k>.csv`, `agreement-raqun-run<k>.csv` | matches | RaQuN's groups (repo scope only; `n_elements`, `similarity` = Jaccard of the attribute sets) | as the exact tables |
| `matchers-run<k>.csv` | matches | (repository, level): exact vs RaQuN | repo, snapshot, variant, run, level, matcher_a, matcher_b, records_common, groups_a, groups_b, pairs_a, pairs_b, pairs_both, pair_jaccard, a_in_one_b, b_in_one_a |
| `score-run<k>.csv` | scores | (dimension, tool) at the matcher's level | dimension, level, tool, voter_group, role (voter, observer), repos (with items), items, confirmed, rate (mean of the per-repository confirmation rates), rate_pooled, ci_low, ci_high (bootstrap of the mean over repositories; only with enough support), exclusive, exclusivity, rank (only with items in ≥ 3 repos and ≥ 10 items), ds_support, ds_sensitivity, ds_specificity (algorithm dimension only) |
| `loo-run<k>.csv` | scores | (dimension, left-out voter group) | dimension, level, left_out, kendall_tau, ranking |
| `grouping-run<k>.csv` | scores | (dimension, voter grouping) | dimension, level, grouping (`declared`, `per-tool`, `llm-merged`, `engine-merged`), voter_groups, ranked, kendall_tau (against `declared`), ranking: the sensitivity checks of the voter groups (`analysis/README.md` "Voter groups") |
| `stability.csv` | scores | (tool, snapshot, variant) with >= 2 runs | repo, snapshot, variant, tool, level, runs, records_mean, records_cv, items_mean, jaccard_mean, jaccard_min, consensus, unstable |
| `mutants.csv` | E4 | mutant (mutants.json) | repo, snapshot, commit, language, variant (the mutant id), variant_commit, snippet, style, catalog_version, unit (expected L4 identity, `protocol:TLS` for the TLS context), family, expected, also, file, call_line, related_lines, spans (`kind:start-end`), host, host_line, host_callers, host_entry, imports_added, qualified (Java types written fully qualified), patch |
| `mutation.csv` | E4 | (mutant, tool) | repo, snapshot, language, variant, snippet, style, tool, voter_group, role, status (`ok`, or why it is not judged: the mutant's or the original's run missing or not ok), runs_mutant, runs_original, unit, masked (the original already has the unit: no repository-level value), found_repo, found_file, found_span (inserted lines ±2), found_site (call line ±2), found_group (lenient: same group at the site), key_size, mode, padding, curve, digest, iterations, protocol_version (the expected value on a new record of the unit on the inserted lines; empty = not judged), extra_units, lost_units, extra_sites, lost_sites (other changes; majority sets), new_records (per run). Only new records count: an original finding moved by the insertion is not new; with repetitions a value holds in most of the mutant's runs |
| `mutation-rates.csv` | E4 | (tool, language, style or `all`, dimension) | tool, voter_group, role, language, style, dimension, repos, mutants (judged), detected, rate (mean of per-repository rates), pooled, ci_low, ci_high (bootstrap over repositories, only when supported: score.py's MIN_REPOS and MIN_ITEMS), supported |
| `agreement-exact-run<k>.csv` | matches | (group, dimension) | matcher, repo, snapshot, variant, run, group_id, scope, key, dimension (key_size, mode, padding, curve, digest; location for repo groups), reporting (voters with a value), agreeing (voters carrying the majority value), majority, values |
| `matchlevels-run<k>.csv` | matches | (matcher, level) | matcher, level, run, repos, items (algorithms by L4 identity), records, k1, k2, k3, k4plus (items by the voter groups of their largest group at the level), share_k2plus, `<dim>_groups` and `<dim>_agree` for key_size, mode, padding, curve, digest (groups with 2+ voters reporting a value admitted at the level; share where all agree), location_groups, location_agree (groups with 2+ located voters; share with 2+ at one call site) |
| `micro-run<k>.csv` | ground truth | micro project and tool of the spec, also unsupported and failed runs | project, language, tool, run, tailored, status (of the run, `missing` without one), verdict (`pass`, `fail`, `unsupported`, `error`, `missing`), n_items, strict, lenient, repo, recall_strict, recall_lenient, recall_repo, sites_in_file, sites_correct, precision, f1, bar_items, passes_bar, entry_bar (`passed`, `disqualified`, `not judged`, over all scored projects of the tool), missed, repo_missed, repo_extra (families reported that the project does not contain), decoys (count), decoys_hit (ids) |
| `micro-items-run<k>.csv` | ground truth | (micro project, tool, expected item) of every scored run | project, language, tool, run, item, families, insertion_style (`direct`, `indirect`), callsite_strict, callsite_lenient, repo |

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
items.drop_duplicates(["snapshot", "variant", "unit"])["k_L3"].value_counts()   # voter groups per item at L3
```

**Export.** The analysis files live inside the experiment folder, so the normal export takes them along together with
the record, every CBOM and its meta file. The export runs `analysis report` first, so the tables and
`_ANALYSIS/_SUMMARY.md` in the zip match the stored runs (`ARGS=--no-report` skips it):

```
make experiment-export NAME=stability-pilot   # -> experiments/results/stability-pilot.zip on the host, open stability-pilot/_ANALYSIS/_SUMMARY.md
```

