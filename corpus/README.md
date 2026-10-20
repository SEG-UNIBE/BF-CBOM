# Corpus

Repository lists and fixtures for the batch experiments (`experiments/*.yaml`, see
`experiments/README.md`). This directory is mounted read-only into the `runner` container at
`/app/corpus`; nothing here is generated.

| path | content |
|---|---|
| `micro/<language>/` | hand-written micro projects (Java, Python, Go, JavaScript, C) with 11 expected items each (known crypto calls at known lines); published to the local git server as fixture repos `bfcbom/micro-<language>` (`path:` in an experiment spec) |
| `micro/ground_truth.yaml` | the 11 assets with files, lines, families and the expected canonical record per language; read by `analysis/groundtruth.py` (`bf-cbom analysis micro <exp>`) |
| `exclusions.yaml` | reviewed repos and name patterns the corpus builder never samples (the generators' own repos and test suites, CBOM benchmarks, BF-CBOM itself), each with its reason; `tailored_on` repos of `common/tools.yaml` are excluded as well (refinements 11 and 13) |
| `pilot-2026-10-08.yaml` | the 12 real projects of the pilot experiment in Java, Python and Go (of its 16; the TypeScript, C and JavaScript ones were removed 2026-10-20) pinned to the commits scanned then; use with `corpus: ../corpus/pilot-2026-10-08.yaml` |
| `pilot-2026-10-08-all.yaml` | all 16 pilot projects as scanned then: the record of the pilot and of what CipherSift was tuned on (`tailored_on` in `common/tools.yaml` reads it); no experiment uses it |
| `corpus.yaml` (not in git) | the stratified ~100+100 corpus built by `make corpus`; lives on the results volume (`corpus-export` copies it here) |

Provenance: the micro projects and their ground truth come from the pilot of 2026-10-08
(`docs/pilot-2026-10-08/`), were written with AI assistance and are **not yet human-reviewed**.
Review them before any paper use; they are also the seed of the mutation snippet catalog (Phase 4):
each asset is a known call with a known canonical record and, for asset 9, the "algorithm via
constant" insertion style.

Fixture commits are deterministic (fixed author and date, `common/experiment.py:publish_fixture`),
so a micro project has the same snapshot SHA on every machine as long as its files are unchanged.
