# Findings

The reviews of the paper's runs: what was run, what failed, and what the runs showed about the tools as shipped and
about our own setup. The paper reports three experiments, each run once, on the tool versions of 30 October 2026
([`tool-versions.md`](tool-versions.md)).

| Experiment | Run on | Review |
|---|---|---|
| `bar-micro`, the bar exam | 2026-11-19 | [Bar exam](#bar-exam-bar-micro-2026-11-19) |
| `corpus-main`, the corpus run | 2026-11-19, on the corpus of 2026-11-18 | [Corpus run](#corpus-run-corpus-main-2026-11-19) |
| `mutation-main`, the mutation run | 2026-11-19, on the 30 smallest clean repositories per language of `corpus-main` | [Mutation run](#mutation-run-mutation-main-2026-11-19) |

All three ran on the same build of BF-CBOM, `0aff29b64be4`.

Pilot runs on separate sets of repositories served to build the instrument. None of their results is a finding, and
this file does not report them. Those repositories are excluded from the corpus
([The corpus](#the-corpus-drawn-2026-10-30-redrawn-2026-11-18-without-the-pilot-sets)).

## How the runs are made
- **One time limit.** Every tool has one hour per scan (`WORKER_TIMEOUT_SEC=3600`, `docker-compose.yml`), so a
  timeout means the same for every tool and the failure rates compare tools under one budget. No tool came near it
  in the bar exam.
- **As shipped, no interventions.** Each tool runs at its pinned version with its documented invocation, on the
  repository root, and nothing in its input or its output is changed. A crash, like a timeout, is the tool's result,
  and the run fails. CipherRadar's Java pass ends with a nil pointer on `new ECGenParameterSpec(<identifier>)` and
  writes no CBOM. Its own ignore file could leave such files out (`CRADAR_EXCLUDE_CRASHERS` in the worker), and this
  is off in every run.
- **A tool that runs but finds less is never steered.** CBOMkit indexes a directory as a module only when it has a
  build file and a `src/` directory. No empty `src/` is added for it (`CBOMKIT_ENSURE_SRC`, off in every run).
- **Documented options that change results** are set once, for every repository. CryptoScan always gets
  `--include-quantum-safe`, since its CBOM otherwise leaves out AES, SHA-2, HMAC and PBKDF2. cdxgen gets `-t java` or
  `-t go`, since 13.x picks the language from the packages the BOM lists and skips its code analysis when none is
  listed. CipherRadar runs from a working directory outside the clone, so a `.cradar.yml` committed in a scanned
  repository cannot change the scan.
- **Memory limits differ per tool** (`memory_gb` in `common/tools.yaml`), each set above the peaks seen before the
  runs. An OOM kill counts as the tool's failure, and each review lists every run that reached its limit.
- **A debug rerun diagnoses and never replaces.** A rerun with debug output explains an empty or a failed result.
  Only an error in our setup leads to a new run of that tool on every repository, recorded as such.
- **A failed run keeps a repository out of the mutation run** for every tool: `mutation-main` takes per language the
  30 repositories of `corpus-main` with the smallest checkout among those where every supported tool's run was ok.

## Bar exam (bar-micro, 2026-11-19)
- **Run.** 14 workers (13 tools, CBOMkit in both of its distributions) on the three micro projects, build
  `0aff29b64be4`, 42 runs in 30 s: 32 ok, 10 unsupported (the language is outside the tool's declared ones), none
  failed. Every run recorded the version the registry names.
- **Verdicts.** The bar is 7 of the 14 known calls in one language, counted leniently. 11 of the 14 pass. Ten pass
  in every language they were scored in. cdxgen passes in Go only: in Java it finds 0 calls, because its Java
  algorithms carry no location (6 of 14 at repository level). Cryptarium fails with 0 everywhere, since its CBOM has
  no locations, qelens with 5 in Python, many of its findings sitting on import lines, and qScan with 5 in every
  language. The lowest passing score is 8 (CBOMkit in Python, where it covers pyca only), the highest failing 5.

  | Tool | Java | Python | Go | Verdict |
  |---|---|---|---|---|
  | CBOMkit | 14 | 8 | 14 | bar passed |
  | CBOMkit-action | 14 | 8 | 14 | bar passed |
  | cdxgen | 0 | – | 9 | bar passed, in Go |
  | CipherRadar | 14 | 14 | 13 | bar passed |
  | CryptobomForge | – | 9 | – | bar passed |
  | CryptoScan | 13 | 12 | 13 | bar passed |
  | pqcheck | – | 14 | 14 | bar passed |
  | Q-DARPAN | 14 | – | 13 | bar passed |
  | SCANOSS | – | – | 14 | bar passed |
  | securin-xbom | 12 | 13 | – | bar passed |
  | xgrep | 14 | 14 | 14 | bar passed |
  | Cryptarium | 0 | 0 | 0 | bar failed |
  | qelens | – | 5 | – | bar failed |
  | qScan | 5 | 5 | 5 | bar failed |

  Known calls found, of 14. A dash: the tool does not declare the language.
- **Decoys.** Only CipherRadar reports cryptography on a decoy: the comment and the log message that name
  algorithms, in every language (precision 0.92 / 0.89 / 0.86 in Java / Python / Go).
- **xgrep reports each algorithm also where its function is called.** Its precision on the micro projects is 0.79 /
  0.66 / 0.62 (Java / Python / Go) at full recall: its wrapper resolution adds an occurrence at the call of the
  function that holds the cryptography (`step01()` in `main`, context `crypto/rsa (resolved via wrapper step01 at
  main.go:24)`). The ground truth counts only the function's own lines, so these places are false ones. In the
  mutation run the same behavior shows as changes off the edit, at the callers of a mutant's host.
- **The specs after it** list ten of the eleven passing tools. CBOMkit-action is left out, since its scores equal
  the server's, and cdxgen runs in Go only (`tool_languages: {cdxgen: [go]}`).
- **The paper's tables** of the subject selection (`tab_bar_exam_results.tex`, `tab_candidate_tools.tex`,
  `tab_decoy_types.tex`) are this export's files, unchanged.

## The corpus: drawn 2026-10-30, redrawn 2026-11-18 without the pilot sets
- **Why a redraw.** The paper discloses the pilot runs and says the corpus is held out from them. The draw of
  2026-10-30 held two repositories of pilot sets, `cisco-ai-defense/skill-scanner` and `dromara/Sa-Token`, whose
  reviews had prompted vocabulary entries. The builder excluded the generators' own repositories and the first pilot
  set, but not the two later sets.
- **The rule now.** `experiments/corpus/exclusions.yaml` names the three pilot files (`pilot_sets`), and no
  repository of them is sampled.
- **The redraw is frozen** (`make corpus --frame ... --as-of 2026-10-30T15:05:23Z`, the first draw's
  `generated_at`): every repository the frame has a check for keeps that commit and result, and a candidate the
  first draw never reached is pinned at its default branch's last commit at or before that moment and checked
  there. Same frame, same seed: the shuffle of every cell is the one of 2026-10-30.
- **Result.** Exactly the two repositories left, and the next candidates of their cells moved up, both in the cell
  of 2'000 stars or more and 3 to 30 MB: in Java `bethington/ghidra-mcp` (branch `dev`, commit `f86152842c81`, 6'014
  stars, JCA) and in Python `rust-lang/this-week-in-rust` (commit `cc58c5660ecf`, 2'654 stars, pyca and other). Both
  passed the crypto pre-filter at the first try. The other 160 repositories are identical in every field, in the
  same order, 54 per language as before. The nested draws pc1 to pc5 were redrawn the same way and carry pc6's
  commits.
- **Checked.** With the exclusions of 2026-10-30 the frozen redraw gives the corpus file of that day exactly, without
  a request. A test replays the committed pc6 from the zipped frame, the seed and the exclusions
  (`tests/test_corpus.py`).
- **A finding about our own tooling.** The crypto pre-filter's Python patterns use `\s` and `\b`, which git's
  grep reads on Linux and not on a Mac (Apple's git), where they match nothing: on a Mac host every Python
  repository would have been dropped as free of cryptography. The draw of 2026-10-30 ran in the runner and is not
  affected. The check now refuses a git that cannot read its patterns.

## Corpus run (corpus-main, 2026-11-19)
- **Run.** The 10 tools on the 162 repositories, 54 per language, in one wave without replicas, in 7 h 05 min. Of
  the 1'620 pairs of a tool and a repository, 486 are unsupported (the language is outside the tool's) and 1'134
  are scans: 6 tools scan Java, 7 Python and 8 Go. 1'120 scans gave a CBOM, 9 ended in an error and 5 at the
  one-hour limit. The scans sum to 15.2 tool-hours, and CBOMkit's queue, 7.1 h, sets the run's length. Every run
  recorded the version the registry names.
- **Failures, all the tools' own.** CBOMkit ends with a parse error on 6 Python repositories (ASPP/pelita,
  Blizaine/Maestro, PancrePal-xiaoyibao/PancrePal-xiaoyibao, QingyangZhang/EMPO, jeshraghian/snntorch,
  real-stanford/universal_manipulation_interface) and reaches the hour on 4 (stanfordnlp/CoreNLP,
  OctoGramApp/OctoGram, hashicorp/terraform-provider-azuread, Gaurav-Gosain/tuios). CipherRadar's Java pass crashes
  on 3 (`java.resolveFirstArg`: Tencent/TencentKona-11, google/j2objc, yuchuangu85/Android_Framework_Source), and
  it reaches the hour on OctoGram.
- **Memory.** No run reached its limit and none was OOM-killed. The highest peaks are cdxgen's 19.9 of 24 GiB
  (guardian-nexus/AuditKit-Community-Edition), CBOMkit's 15.9 of 20, CipherRadar's 11.3 of 16 (OctoGram, the scan
  that reached the hour) and xgrep's 6.8 of 8. The VM peaked at 42.2 of 121.5 GiB.
- **As shipped.** CBOMkit reports no cryptography in 43 of its 48 completed Python scans, since it covers pyca
  only. CipherRadar's 1'289 `unread` places lie inside archives (`<archive>!/<path>`, JAR and ZIP files), which a
  checkout does not unpack. 295'188 of CryptoScan's 410'230 records come from ossf/malicious-packages, a repository
  of OSV data files.
- **cdxgen in Go.** It reports no cryptography in 5 repositories where six or seven other tools do
  ([below](#cdxgen-reads-one-go-module)).
- **The mutation run's selection.** 13 repositories have a failed scan (6 Python, 5 Java, 2 Go), which leaves 48,
  49 and 52 clean ones. The 30 smallest checkouts per language are `experiments/corpus/mutation-main.yaml`.

## Mutation run (mutation-main, 2026-11-19)
- **Run.** 90 repositories, 30 per language, each in 31 variants: the original, 15 with an injected call and 15
  with an injected decoy. That is 2'700 mutants, 1'350 calls and 1'350 decoys, from catalog version `2026-10-29.1`.
  Of the 27'900 pairs of a tool and a variant, 8'370 are unsupported and 19'530 are scans, and every scan gave a
  CBOM. The run took 9 h 41 min and 49.3 tool-hours, the longest queue being CipherRadar's (9.4 h). The VM peaked
  at 32.8 of 121.5 GiB.
- **Memory.** No scan failed and none was OOM-killed. 98 scans count events at their limit and finished. CBOMkit's
  39 are on three repositories (CloudNetService/CloudNet, pickles4evaaaa/mybibliotheca, runfinch/finch), with a
  working set of up to 18.2 of 20 GiB. cdxgen's 59 are on five Go repositories (googleapis/gapic-showcase,
  openclaw/discrawl, prest/prest, runfinch/finch, turbot/steampipe), with a working set of 5.1 to 12.2 of 24 GiB.
  The limit also counts the container's file cache, which the kernel reclaims first, so cdxgen's events are cache
  reclaim.
- **The hit rule** (decided 2026-11-02). An injected call counts as detected when the tool reports its algorithm
  on one of the inserted lines, imports excluded, which is the bar exam's rule. Decoys are judged on the same lines.
  A stricter reading, the line naming the algorithm ±2, stays a column (`found_site`, `recall_site`). In Go it
  loses the tools that put the record on another statement of the snippet (`cipher.NewGCM` four lines below
  `aes.NewCipher`): xgrep 0.70 against 0.94, CBOMkit 0.63 against 0.83, CipherRadar 0.63 against 0.72, pqcheck 0.65
  against 0.72, every other Go tool within 0.03. In Java and Python both readings give the same recall.
- **Scores.** F1 ranges from 0.07 (CBOMkit in Python) to 1.00 (xgrep in Python): xgrep 0.97 to 1.00, SCANOSS 0.99
  (Go), CBOMkit 0.95 (Java) and 0.91 (Go). Precision is 1.00 for every tool but two. CipherRadar (0.75 to 0.80)
  reports the algorithms named in comments and log messages, 108 to 110 of the 110 to 112 such decoys per language,
  and no other decoy kind. CryptobomForge (0.99) has one hit, and it is no report on the decoy: its records carry
  CodeQL's context window, the finding ±2 lines, and a hex-constant decoy inserted two lines above a SHA-256 call
  becomes the first line of that call's window.
- **Changes off the edit.** Only two tools report anything new outside the inserted lines. xgrep does in 0.02
  (Java), 0.10 (Python) and 0.11 (Go) of its mutants: the injected algorithm again at the callers of the host
  function ("resolved via wrapper"), at up to 114 places in one mutant, as on the micro projects. Q-DARPAN does in
  0.04 of its Go mutants: it reports the added import on the `import (` line, a few lines above the inserted one.
- **Python's snippet mix.** 4 of the 30 Python repositories declare `cryptography`, so 418 of the 450 Python calls
  are the seven standard-library snippets (hashlib, hmac, PBKDF2, ssl). CBOMkit covers pyca only and finds 16 of
  the 32 pyca calls, a recall of 0.04.
- **Intervals of width 0.** CipherRadar, CryptoScan and securin-xbom find the same Java snippets in every
  repository (13, 12 and 8 of the 15), so their interval over repositories collapses. The snippets are a fixed set
  and not a sample: the interval measures the effect of the host code, which is none for these three.
- **CBOMkit's low repositories.** It finds 0 of 15 calls in google/copybara (a Bazel project), 1 in
  jakgor471/BSPEntSpy and 5 in heroku/webapp-runner: it indexes a directory as a module only with a build file and
  a `src/` directory ([How the runs are made](#how-the-runs-are-made)).
- **cdxgen in Go.** 35 of the 450 injected Go calls sit in modules it does not read
  ([below](#cdxgen-reads-one-go-module)).

## cdxgen reads one Go module
A Go repository can hold several modules, each a folder with its own `go.mod`. cdxgen's dependency inventory covers
all of them. Its crypto inventory, in a repository with a module at the root, covers that module only. Checked on
2026-11-04 with the pinned upstream image (13.5.0), the worker's flags and Go environment, in a throwaway container.
The debug runs diagnose and replace no result.
- **The mechanism.** cdxgen runs `go list -deps` in every module (`--recurse`, on by default, "suitable for
  mono-repos"), then golem once, `golem analyze --dir <scan root>`. Go's `./...` stops at a module boundary.
  - imulab/go-scim has a root module, whose `./...` is the 5 packages under `cmd/`, while its cryptography sits in
    the nested modules `pkg/v2` and `mongo/v2`: 0 crypto assets. The same cdxgen run on `pkg/v2` reports SHA-1 at
    `pkg/v2/service/filter/meta.go:124`, 2 assets in all.
  - sigstore/scaffolding has no root module, only a `go.work` naming 10 of its 12 modules. `go list ./...` fails
    there ("directory prefix . does not contain modules listed in go.work"), golem's output is all but empty, 0
    crypto assets. The same cdxgen run on `tools/tuf` reports 17.
- **As shipped.** No option of cdxgen 13.5.0 runs the crypto analysis per module, and every tool runs on the
  repository root. Scanning each module apart would steer the tool, so the results stay and this is a finding about
  cdxgen: a repository's module layout decides how much of its code cdxgen's CBOM covers.
- **The layout of the corpus** (read from each repository's tree at its pinned commit, the `go.mod` and `go.work`
  paths): of the 54 Go repositories, 27 are one root module, 18 have a root module and nested ones, and 9 have no
  root module. Of those 9, 4 hold one module in a subfolder and 5 hold several, and one has a `go.work` at the root
  (sigstore/scaffolding).
- **In the corpus run.** In the 18 repositories with a root module and nested ones, every algorithm cdxgen locates
  lies in the root module. The other seven Go tools report 2'439 algorithm records in files of the nested modules,
  in 10 of the 18 repositories, which is 6 % of their 42'378 records in Go files. Without a root module cdxgen does
  report from the modules below, in riffpad/riffpad from three of them. It reports no cryptography in 5
  repositories where six or seven other tools report 76 of the corpus's 453 Go items: imulab/go-scim and
  sigstore/scaffolding (above), ovn-kubernetes/ovn-kubernetes (four modules, none at the root), and
  gardener/gardener and Gaurav-Gosain/tuios, which have a root module, nested ones and a `go.work`. The debug runs
  do not cover the last three.
- **In the mutation run.** 35 of the 450 injected Go calls sit in modules cdxgen does not read: 20 in nested
  modules below a root module (imulab/go-scim 14, reugn/go-streams 5, go-gorm/gen 1) and the 15 of
  sigstore/scaffolding. cdxgen finds none of them, while the other seven Go tools find 66 to 97 % of them. Of the
  other 415 calls it finds 317, a recall of 0.76 against 0.70 over all 450. Its remaining misses follow the
  snippet: it never reports the 3DES-CBC and the RSA-OAEP call (0 of 28 each).
