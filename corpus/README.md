# Corpus

Repository lists and fixtures for the batch experiments (`experiments/*.yaml`, see
`experiments/README.md`). This directory is mounted read-only into the `runner` container at
`/app/corpus`; nothing here is generated.

| path | content |
|---|---|
| `micro/<language>/` | hand-written micro projects (Java, Python, Go, JavaScript, C) with 11 known crypto assets each; published to the local git server as fixture repos `bfcbom/micro-<language>` (`path:` in an experiment spec) |
| `micro/ground_truth.yaml` | the 11 assets with files, lines, families and the expected canonical record per language; read by `analysis/groundtruth.py` (`bf-cbom analysis micro <exp>`) |
| `pilot-2026-10-08.yaml` | the 16 real projects of the pilot experiment (8 Java/Python, 8 Go/C/JS) pinned to the commits scanned then; use with `corpus: ../corpus/pilot-2026-10-08.yaml` |
| `corpus.yaml` (not in git) | the stratified ~100+100 corpus built by `make corpus`; lives on the results volume (`corpus-export` copies it here) |

Provenance: the micro projects and their ground truth come from the pilot of 2026-10-08
(`docs/pilot-2026-10-08/`), were written with AI assistance and are **not yet human-reviewed**.
Review them before any paper use; they are also the seed of the mutation snippet catalog (Phase 4):
each asset is a known call with a known canonical record and, for asset 9, the "algorithm via
constant" insertion style.

Fixture commits are deterministic (fixed author and date, `common/experiment.py:publish_fixture`),
so a micro project has the same snapshot SHA on every machine as long as its files are unchanged.
