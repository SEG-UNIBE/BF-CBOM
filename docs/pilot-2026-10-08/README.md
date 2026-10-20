# Pilot experiment of 2026-10-08 (what was kept in BF-CBOM)

A colleague ran a one-day pilot for the journal extension: 15 CBOM generators on a small pinned
corpus, graded against an inclusion bar, and the usable tools' findings cross-matched by location
with hand-checked samples. The pilot lived in its own repository; this directory keeps what BF-CBOM
builds on. The raw tool outputs, logs and the private session transcripts were not carried over.

| kept where | what |
|---|---|
| `corpus/micro/<language>/` + `corpus/micro/ground_truth.yaml` | the five hand-written micro projects (Java, Python, Go, JavaScript, C; 11 assets each) and their ground truth, machine-readable |
| `corpus/pilot-2026-10-08.yaml` | 12 of the 16 real projects (those in Java, Python and Go; the other four removed 2026-10-20) at the commits scanned then (full SHAs) |
| `analysis/data/families.yaml`, `analysis/sites.py`, `analysis/match.py`, `analysis/groundtruth.py` | the family table, the location-aware matcher (same file, line ±2, compatible family, unknown matches nothing) and the micro scoring, ported to BF-CBOM's result layout |
| `tests/fixtures/pilot/` | CBOMkit, pqcheck and CipherRadar outputs on the micro projects (open tools only), used as regression fixtures |
| `common/tools.yaml` | engine families, per-language scope, pilot verdicts and the candidate tools |
| this directory | `landscape-README.md` + `cbom_producers.csv` (tool landscape survey, 176 records), `javapy_analysis_report.txt`, `native_REPORT.md` (the pilot's own reports) |

Provenance: the micro projects, the ground truth and every hand-check verdict were produced with
AI assistance on 2026-10-08 and have **not been reviewed by a human**. The grading rules are by
A. Boll. Review before any paper use.

## Inclusion bar (grading rules of 2026-10-08)

A tool is usable if it does not crash, its output makes sense, it usually finds ≥ 50 % of the
instances, it gives some kind of call location, and it can ideally be normalised. Test per
supported language: the micro project (recall against the ground truth) plus one mid-size real
project (sanity check only; recall on real code is not part of the bar). Format problems do not
count: if a converter can read location and name, the tool passes (even with locations in custom
properties or an invalid CycloneDX). `bf-cbom analysis micro <exp>` computes this bar.

| Tool | Java | Python | Go | C | JS/TS |
|---|---|---|---|---|---|
| Mondoo xgrep | pass | pass | pass | – | pass |
| CBOMkit (action jar) | pass | pass | pass | – | – |
| CipherRadar | borderline | pass | pass | fail | pass |
| pqcheck | – | pass | pass | – | – |
| Q-DARPAN | pass | – | pass | pass | – |
| securin-xbom | pass | pass | fail | fail | fail |
| SCANOSS (open rules) | ? | ? | pass | pass | fail |
| qelens | borderline | fail | fail | fail | fail |
| cdxgen 12.8.5 | fail | fail | ? | ? | ? |
| CryptobomForge | fail | fail | – | – | – |
| pqc-mat | – | fail | – | fail | – |
| OQS Scanner, cryptarium, qScan | fail | fail | fail | fail | fail |
| DeepSeek (old URL-only worker) | fail | fail | ? | ? | ? |

`?` means not testable, not a fail (SCANOSS Java/Python rules need an API key). The DeepSeek row
was graded from saved outputs of the old worker that sent only the repo URL; the rewritten worker
(`common/llm_cbom.py`) reads code and emits locations, so the grade is superseded.

Open grading questions raised on 2026-10-15: Q-DARPAN Java passes only through the format
relaxation (every algorithm was named `jca` in the nano test); pqc-mat Python is 2/11 as reported
but 7/11 if its systematic −2 line offset is corrected; the bar counts strict matches for
cryptarium (4/11) but the xgrep summary used lenient.

## Headline numbers of the cross-tool comparison

Strict precision (hand-checked "real crypto use") by number of agreeing tools k:

| corpus | k=1 | k=2 | k≥3 |
|---|---|---|---|
| Java/Python (5 voters) | 48 % | 88 % | 100 % |
| Go/C/JS (6 voters) | 39 % | 60 % | 96 % |

Per-tool solo precision ranged from ~100 % (CBOMkit, Q-DARPAN, pqcheck) to 17–31 % (CipherRadar,
securin-xbom Python); only ~18 % of clusters had two or more tools. Pure agreement therefore
punishes careful tools for being right alone, which is the "right alone" threat in CLAUDE.md
(E4) and the motivation for the Dawid-Skene weighting. Shared blind spots of all tools: algorithm
names that arrive through a variable, constant or NID, and generic EVP/WebCrypto dispatch (the
third insertion style of Phase 4). On the micro projects agreement does not reproduce the
ground-truth ranking (everything ~1.0 on micro-go; a failed-grade tool piggybacks on others via
unknown-family findings), which is why unknown names now match nothing.

Matcher decisions taken from the pilot: same file, |Δline| ≤ 2, family sets must intersect,
nearest-neighbour linking per tool (no chaining), unknown family never matches; EC covers
ECDSA/ECDH/EdDSA/X25519 and named curves; keep algorithm assets only (keys, certificates,
protocols dropped) and only primary-language source files.

## Recommendations for BF-CBOM (status)

1. Location-aware matching: done (`analysis/match.py`).
2. Commit pinning without silent fallback: done (Phase 1).
3. More languages: Go has the most passing tools (6); the micro corpus covers five languages.
4. A fixed corpus: `corpus/pilot-2026-10-08.yaml` plus the Phase 1 corpus builder.
5. Per-language tool scope ("not supported" ≠ failed): `languages` in `common/tools.yaml`.
6. Timeouts up to 45 min and 16 GB heap for CBOMkit on large repos (pilot: Keycloak 551 s,
   Vault 1649 s, Kafka 364 s with the indexing workaround): not applied yet (compose limits).
7. CBOMkit module-indexing bug (needs a build file *and* `src/`; Kafka scanned 7 of 2,900 files):
   to be reproduced with our server-image worker; workaround is an empty `src/` at the root.
8. CryptobomForge emits IBM's pre-standard `1.4-cbom-1.0`, not CycloneDX: the ICPC claim needs a
   correction; `native_cyclonedx: false` in `tools.yaml`.

## Candidate workers (open-source tools that passed) and how the pilot ran them

Exact versions: pqcheck PyPI 0.1.0 (3e99c74), Q-DARPAN 72a4edd, securin-xbom a270c5f (+ semgrep),
CipherRadar efa5d9d (+ opengrep v1.12.1), CBOMkit action jar: sonar-cryptography d1d1b8f,
cbomkit-lib 414247c, cbomkit-action 34a3d96. Pilot host: Debian 12, Python 3.11/3.12, Go 1.26.8,
no Docker.

| tool | command | notes |
|---|---|---|
| pqcheck | `pqcheck scan <repo> --policy cryptoct-advisory --format cbom -o out.json` | Python 3.12; a Go toolchain on `PATH` enables its go/types analyser (else tree-sitter); unset `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` so nothing calls an LLM |
| Q-DARPAN | `qdarpan scan <repo> --offline --surface source --out <dir>` → `<dir>/<run>/cbom.json` + `findings.jsonl` | locations only in custom properties `qdarpan:location.N` (`path:line`); the adapter should move them into `evidence.occurrences` (`analysis/sites.py` already reads the property) |
| securin-xbom | `python xbom.py scan <repo> --bom-types cbom -o <dir>` → `<dir>/cbom.cdx.json` | must run offline (empty `REGISTRY_RULESETS`, no network), otherwise it pulls extra rules from semgrep.dev; keep cdxgen off `PATH` (its layer 2 is broken with cdxgen 12); `SEMGREP_SEND_METRICS=off` |
| CipherRadar | `cradar --verbose scan <repo> -o out.json --validate --no-baseline --passes 1,2` | needs opengrep and writable `XDG_CONFIG_HOME`/`XDG_CACHE_HOME` (else opengrep dies silently and pass 2 reports 0); nil-pointer crash on `new ECGenParameterSpec(<identifier>)` (jjwt, Keycloak): on rc 2 add the failing file to `.cradarignore`, retry, record the ignored files; CBOM also contains `library` import components |
| CBOMkit action jar | `java -Xmx16g -Duser.home=<empty> -jar CBOMkit-action.jar` with `GITHUB_WORKSPACE=<repo> GITHUB_OUTPUT=<f> CBOMKIT_OUTPUT_DIR=<dir> CBOMKIT_LANGUAGES=java,python CBOMKIT_JAVA_REQUIRE_BUILD=false CBOMKIT_GENERATE_MODULE_CBOMS=false CBOMKIT_JAVA_JAR_DIR=<action>/src/main/resources/java/scan` | source-only scan; decide whether to replace or complement the server-image worker |

Not carried over: the pilot's xgrep results, the SCANOSS open-rules results (Java/Python rules need a key), and the
failed tools.

## Access requests sent by the pilot on 2026-10-08 (replies pending)

SCANOSS research API key, IBM Cryptoscope, Crypsy (Näther & Hirsch), Monet+ CBOMpy, Xygeni
and Encryption Consulting.
