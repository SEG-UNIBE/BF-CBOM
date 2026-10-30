# Tool versions (cutoff 2026-10-30)

The rule, as the paper states it: for each tool we used the latest stable release published by 30 October 2026,
excluding pre-releases and nightly builds. For tools without any stable release we used the default branch at its
last commit on that date. Engines bundled by a tool's own build stay at the version the tool pins.

Checked on 2026-10-30 against GitHub (tags and releases), npm, PyPI, crates.io and ghcr.io. Every pin is exact: a
release version with the checksum its publisher lists, an image digest, or a full commit SHA. The pins live in
`docker/Dockerfile.worker-<tool>`. Every run records what actually ran (`tool_version`, `image_ref`,
`bfcbom_commit` in `runs.csv`). Moving to a newer version means moving the cutoff date for every tool, and then
rerunning bar-micro.

| Tool | Version or commit | Published | How it is pinned | Source |
|---|---|---|---|---|
| CBOMkit | 2.3.0 (sonar-cryptography 1.7.0, bundled) | 2026-09-09 | image digest | `ghcr.io/cbomkit/cbomkit` |
| cbomkit-jar | cbomkit-action v2.3.0 (sonar-cryptography 1.7.0, bundled) | 2026-09-07 | image digest | `ghcr.io/cbomkit/cbomkit-action` |
| cdxgen | 13.5.0 | 2026-10-23 | image digest | `ghcr.io/cdxgen/cdxgen` (GitHub's latest release; npm's `latest` tag still names 12.9.0) |
| CipherRadar | default branch `efa5d9de322c9120b15acc2e958dc6c1c4e609e1` (only pre-releases exist) | 2026-09-07 | full SHA, built from source | github.com/nk-sentinel/cipherradar |
| CipherRadar's engine | OpenGrep 1.33.1 (CipherRadar pins none) | 2026-10-29 | sha256 per arch | github.com/opengrep/opengrep |
| CipherSift | 1.2.0 | 2026-10-19 | source tarball sha256 | github.com/SEG-UNIBE/ciphersift |
| CryptobomForge | cryptobom-forge 1.1.0 | 2024-01-25 | release wheel, `requirements.txt` pinned | github.com/Santandersecurityresearch/cryptobom-forge |
| CryptobomForge's engine | CodeQL CLI 2.27.3; packs java-queries 1.11.13, python-queries 1.8.13, cpp-queries 1.9.2 (those of tag `codeql-cli/v2.27.3`) | 2026-10-20 | zip sha256; pack versions | github.com/github/codeql-cli-binaries |
| CryptoScan | 1.5.0 | 2026-10-19 | sha256 from the release's `checksums.txt` | github.com/csnp/cryptoscan |
| pqcheck | release `pqcheck-v0.1.0` = `c0ccf5be96e71add49c9d9865b020a279d1f1a4a` | 2026-07-13 | full SHA, built from source | github.com/Rafael-Ryu/pqcheck |
| Q-DARPAN | default branch `72a4eddfcc8c115d168be4ec26107fb539020675` (no release) | 2026-09-07 | full SHA | github.com/RohanExploit/q-darpan |
| SCANOSS | crypto-finder 0.43.0 | 2026-10-29 | sha256 from the release's checksums file | github.com/scanoss/crypto-finder |
| SCANOSS's engine | OpenGrep 1.22.0, as crypto-finder's own Dockerfile pins it | 2026-05-20 | sha256 per arch | github.com/opengrep/opengrep |
| SCANOSS's rules | open-crypto-rules default branch `55d46c2365c1fea84aa41baab70af62e9538a81d` (no release) | 2026-10-26 | full SHA | github.com/scanoss/open-crypto-rules |
| securin-xbom | default branch `a270c5ffd393d904efc1d5ad51f406dabe197117` (no release) | 2026-04-09 | full SHA | github.com/securin-public/securin-xbom |
| securin-xbom's engine | semgrep 1.183.0 (xbom pins none) | 2026-10-29 | PyPI version | pypi.org/project/semgrep |
| securin-xbom's registry rules | semgrep-rules default branch `16b9e6d45f10649f6a531ec4838ed9924372c3bc` | 2026-10-21 | full SHA, frozen at build | github.com/semgrep/semgrep-rules |
| xgrep | 0.101.0 | 2026-10-28 | sha512 from npm's `dist.integrity` | npm `@mondoohq/xgrep_linux_<arch>` |
| Cryptarium | v0.3.0 | 2026-09-18 | `go install` at the tag, go.sum | github.com/sgoveia/cryptarium |
| qelens | crate `cbom` 0.1.2 | 2026-07-21 | `cargo install --locked --version` | crates.io/crates/cbom |
| qScan | `@quantakrypto/qscan` 0.12.0 | 2026-08-14 | npm version | npm |

Notes:
- **pqcheck.** The pinned release `v0.1.0` predates the default branch by about 20 fix commits, and both carry the version string 0.1.0. The rule takes the release.
- **CipherRadar.** Its tags are release candidates only (`v0.4.0-rc.4`, `v0.5.0-rc.1`), so the rule takes the default branch.
- **cdxgen.** It moved from 12.8.5 to 13.5.0 across a major version. Its invocation flags (`--include-crypto`, `--evidence`, `-t`, `--no-install-deps`) are unchanged in 13.5.0's CLI reference.
- **Bar exam.** cdxgen, Cryptarium, qelens and qScan failed the bar exam and run on bar-micro only.
