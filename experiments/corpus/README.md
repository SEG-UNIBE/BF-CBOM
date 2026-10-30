# Corpus

Repository lists and fixtures for the batch experiments (`experiments/*.yaml`, see
[the experiment guide](../README.md)). This directory is available read-only in the `runner` container at
`/app/experiments/corpus` through the experiments mount. The micro fixtures are generated and checked in;
sampled corpora live on the results volume.

| path | content |
|---|---|
| `micro/<language>/` | generated micro projects for Java, Python and Go, each with 14 expected crypto calls in separate functions and eight decoys; published to the local git server as fixture repos `bfcbom/micro-<language>` (`path:` in an experiment spec) |
| `micro/ground_truth.yaml` | generated expected items, function-body lines, canonical records, accepted locations and decoys; read by `analysis/groundtruth.py` (`make analysis ARGS="micro <exp>"`) |
| `exclusions.yaml` | reviewed repos and name patterns the corpus builder never samples (the generators' own repos and test suites, CBOM benchmarks, BF-CBOM itself), each with its reason; `tailored_on` repos of `common/tools.yaml` are excluded as well |
| `pilot-2026-10-08.yaml` | the 12 real projects of the pilot experiment in Java, Python and Go (of its 16; the TypeScript, C and JavaScript ones were removed 2026-10-20) pinned to the commits scanned then; use with `corpus: corpus/pilot-2026-10-08.yaml` |
| `pilot-2026-10-08-all.yaml` | all 16 pilot projects as scanned then: the record of the pilot and of what CipherSift was tuned on (`tailored_on` in `common/tools.yaml` reads it); no experiment uses it |
| `pilot-2026-10-22.yaml` | second pilot: 60 manually selected repositories, 20 each in Java, Python and Go, pinned to default-branch HEADs on 2026-10-22; used by `experiments/corpus-pilot-medium.yaml` |
| `icpc-2026.yaml` | the 23 ICPC paper repositories pinned at their HEAD of 2026-10-18, held out from tuning; six are marked `expect_crypto: low` |
| `corpus.yaml` (not in git) | the stratified corpus built by `make corpus`: per language every (star bin x size bin) cell enumerated completely and `--per-cell` repos drawn at random (by default 3 x 3 cells x 2 = 18 per language for Java, Python and Go), each with its `stratum`, `crypto_apis` (the API families the pre-filter matched) and `topics`; the frame (`cells`: every cell's population and quota) and every excluded candidate with its reason are in the file (`experiments/README.md` "Corpus sampling"); lives at `/data/results/corpus/corpus.yaml` in the runner (`make corpus-export` copies the corpus YAML files into `experiments/results/` on the host) |

Since 2026-10-22, `mutation/micro.py` generates both the micro source files and their ground truth from
`mutation/catalog/{java,python,go}.yaml`. Edit the catalogs or generator, then run
`uv run python -m mutation.micro`; do not edit or reformat the generated files, because the ground truth records
their exact lines. Tests check that the checked-in files match the generator.

The entry bar is at least 7 of 14 calls found in their functions, leniently, in one paper language. The analysis
also reports strict and repository-level recall, precision, F1 and decoy hits. The hit and accepted-location rules
are stated in the ground truth's header. The catalogs and decoys are AI-made (the last four decoy kinds added 2026-10-29) and **not yet human-reviewed**;
review them before paper use.

**The eight decoys** (`decoys:` in each catalog; one function each in the micro projects, and the mutation harness
plants the same ones as mutants, `decoys` in a spec's `mutation:` block) are known negatives, each aimed at one way a
tool can be fooled:

| decoy | what it is | the trap |
|---|---|---|
| `comment` | a comment naming algorithms (`TODO: replace the MD5 and DES fallbacks with SHA-256 and AES`) above plain arithmetic | a tool that matches algorithm names without reading code reports MD5, DES, SHA-256 and AES |
| `log-message` | a print or log statement whose string names algorithms (`"RSA and ECDSA keys are rotated nightly"`) | the same names inside a string literal, which some pattern tools scan and some skip |
| `crc32` | a CRC32 checksum (`zlib.crc32`, `java.util.zip.CRC32`, `hash/crc32`) | a hash-like API that is no cryptography; a tool that takes every checksum or `hash` package for a crypto asset reports it |
| `base64` | a Base64 encoding (`base64.b64encode`, `java.util.Base64`, `encoding/base64`) | an encoding that sits next to keys and ciphertext in real code but is no cipher; a tool that lists encoders with ciphers reports it |
| `identifier` | a variable named after an algorithm that holds plain text (`sha256Tag = "checksum-" + len`) | an algorithm name inside an identifier; a tool that matches names in code, not only in strings and comments, reports SHA-256 |
| `hex-constant` | a 64-digit hex string constant (`expectedHex = "9f86d081…0a08"`, the length of a SHA-256 digest or an AES-256 key), only its length is used | a value shaped like a key or a digest; a tool that reports key material or secrets by shape lists it |
| `url` | a download URL ending in `.sha256` (`https://example.org/downloads/app-1.0.tar.gz.sha256`) | an algorithm name as a file suffix inside a URL; the string-matching trap in its most common real-world form |
| `cert-path` | a path string naming a certificate file (`config/certs/ca_ecdsa.pem`; no such file exists, only its suffix is checked) | a certificate or key file name carrying an algorithm; a tool that lists certificates by file name reports ECDSA (cdxgen names its certificate assets after their files) |

Any crypto record on a decoy's lines is a false positive with ground truth (the micro check's `decoys` column; a
mutant's `false_positive` in `mutation.csv`). A non-cryptographic PRNG (`math/rand`, `random`) is deliberately not a
decoy: the vocabulary counts PRNGs as findings, so a report there would not be a clean false positive.

The earlier five-language, 11-item fixtures came from the pilot of 2026-10-08 and are not comparable with the new
micro results. Their frozen ground truth remains in `tests/fixtures/pilot/ground_truth-2026-10-08.yaml` for tests
against the historical CBOMs.

Fixture commits are deterministic (fixed author and date, `common/experiment.py:publish_fixture`),
so a micro project has the same snapshot SHA on every machine as long as its files are unchanged.
