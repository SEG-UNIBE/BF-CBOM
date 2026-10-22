# Corpus

Repository lists and fixtures for the batch experiments (`experiments/*.yaml`, see
[the experiment guide](../README.md)). This directory is available read-only in the `runner` container at
`/app/experiments/corpus` through the experiments mount. The micro fixtures are generated and checked in;
sampled corpora live on the results volume.

| path | content |
|---|---|
| `micro/<language>/` | generated micro projects for Java, Python and Go, each with 14 expected crypto calls in separate functions and four decoys; published to the local git server as fixture repos `bfcbom/micro-<language>` (`path:` in an experiment spec) |
| `micro/ground_truth.yaml` | generated expected items, function-body lines, canonical records, accepted locations and decoys; read by `analysis/groundtruth.py` (`make analysis ARGS="micro <exp>"`) |
| `exclusions.yaml` | reviewed repos and name patterns the corpus builder never samples (the generators' own repos and test suites, CBOM benchmarks, BF-CBOM itself), each with its reason; `tailored_on` repos of `common/tools.yaml` are excluded as well (refinements 11 and 13) |
| `pilot-2026-10-08.yaml` | the 12 real projects of the pilot experiment in Java, Python and Go (of its 16; the TypeScript, C and JavaScript ones were removed 2026-10-20) pinned to the commits scanned then; use with `corpus: corpus/pilot-2026-10-08.yaml` |
| `pilot-2026-10-08-all.yaml` | all 16 pilot projects as scanned then: the record of the pilot and of what CipherSift was tuned on (`tailored_on` in `common/tools.yaml` reads it); no experiment uses it |
| `pilot-2026-10-22.yaml` | second pilot: 60 manually selected repositories, 20 each in Java, Python and Go, pinned to default-branch HEADs on 2026-10-22; used by `experiments/e1-pilot-60.yaml` |
| `icpc-2026.yaml` | the 23 ICPC paper repositories pinned at their HEAD of 2026-10-18, held out from tuning; six are marked `expect_crypto: low` |
| `corpus.yaml` (not in git) | the stratified corpus built by `make corpus`, by default 100 repos per language for Java, Python and Go; lives at `/data/results/corpus/corpus.yaml` in the runner (`make corpus-export` copies the corpus YAML files into `experiments/results/` on the host) |

Since 2026-10-22, `mutation/micro.py` generates both the micro source files and their ground truth from
`mutation/catalog/{java,python,go}.yaml`. Edit the catalogs or generator, then run
`uv run python -m mutation.micro`; do not edit or reformat the generated files, because the ground truth records
their exact lines. Tests check that the checked-in files match the generator.

The entry bar is at least 7 of 14 calls found in their functions, leniently, in one paper language. The analysis
also reports strict and repository-level recall, precision, F1 and decoy hits. The hit and accepted-location rules
are stated in the ground truth's header. The catalogs and decoys are AI-made and **not yet human-reviewed**;
review them before paper use.

The earlier five-language, 11-item fixtures came from the pilot of 2026-10-08 and are not comparable with the new
micro results. Their frozen ground truth remains in `tests/fixtures/pilot/ground_truth-2026-10-08.yaml` for tests
against the historical CBOMs.

Fixture commits are deterministic (fixed author and date, `common/experiment.py:publish_fixture`),
so a micro project has the same snapshot SHA on every machine as long as its files are unchanged.
