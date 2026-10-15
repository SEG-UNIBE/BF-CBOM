# CBOM producers: landscape survey (status 2026-10-06)

Context: extending Bögli/Spieler/Kehrer, *BF-CBOM*, ICPC 2026, to an EMSE paper. The ICPC paper's tool search was done around Sept 2025 and found three CBOM generators: CBOMKit, cdxgen and CryptobomForge.

Files:
- `cbom_producers.csv` has every record (176 rows), with full-text cells and evidence URLs.
- This page has condensed tables. Cells are shortened, so see the CSV for the wording and sources.

## Key findings

1. **The field grew a lot after Sept 2025.** There are 79 in-scope static producer records in tiers A–D, and **62 were first published in 2026**. Most are tiny: 0–5★, one author, often visibly LLM-assisted (CLAUDE.md files, single code drops), several from hackathons (Smart India Hackathon SIH26164, PSB). Only a handful are company- or project-backed beyond the paper's three:
   - SCANOSS crypto-finder
   - OmniTrust CBOM-Lens
   - CSNP/QRAMM CryptoScan and CryptoDeps
   - quantakrypto qScan
   - Mondoo xgrep (free but closed source)
   - Linux Foundation's `lfreleng-actions/cbom-action`, which wraps CBOMkit
2. **The ICPC paper's claim that all three tools report CycloneDX needs a correction.** CryptobomForge emits **IBM's pre-standard draft `1.4-cbom-1.0`**:
   - `bomFormat: "CBOM"`, component type `crypto-asset`
   - serializer `JsonV1Dot4CbomV1Dot0` from a forked cyclonedx-python-lib, pulled via git+ssh
   - output is not schema-valid under CycloneDX 1.6, which has `additionalProperties:false` on `cryptoProperties`

   Evidence: `cbom/cli/cli.py:9,74`, `pyproject.toml`, `README.md:209-211`. CryptobomForge also has had no release since 2024-01-25.
3. **"Claims CycloneDX" ≠ "is valid CycloneDX".**
   - Most producers claim **CycloneDX 1.6** (about 50), some **1.7** (about 10).
   - Several are non-conformant (⚠): `cryptographic-asset` without `cryptoProperties`, custom keys, or 1.5 (pre-CBOM).
   - Some READMEs show stale formats while the code emits something else (csnp: README "CryptoBOM 1.0"/1.5, code 1.6; qelens: docs 1.5, code 1.6).
   - Others use fully proprietary "CBOM" JSON: QuantumSeal `quantumseal-cbom/1`, juankaukian, QuantumSentinel, AC-Scanner.

   Running every producer's output through the official schema validator would make a cheap, solid research question.
4. **Coupling: almost all tools write a standalone CBOM.** The exceptions:
   - **cdxgen** (crypto components inside the SBOM; filter `type == cryptographic-asset` or use `--component-type cryptographic-asset`) and **CryptoDeps** (library and crypto components mixed). Both are easy to extract.
   - Several tools emit a CBOM only behind a flag (`--format cbom` / `cyclonedx`); their default output is proprietary JSON, e.g. SCANOSS, qelens.
   - **advanced-security/cbom-action** does **not** produce a CBOM despite its name. It emits SARIF, the front half of the CryptobomForge pipeline.
5. **The CBOMkit family has one detection engine.** CBOMkit, cbomkit-action, cbomkit-lib and the LF cbom-action all wrap **sonar-cryptography ("hyperion")**. cbomkit-theia is the only separate engine, and it scans containers and filesystems, not source. Count them as 2 producers, not 5.
6. **cdxgen added CBOM in v10.3.0 (2024-04-07)**, Java only. It now also covers JS/TS source-level algorithm inventory, and its **default spec is 1.7**. The new `tracebom` (eBPF) is a runtime CBOM feature.
7. **Engines are mostly shallow.** Most of the long tail is regex/pattern-based, followed by tree-sitter. Only a few tools do semantic analysis: sonar-cryptography, Roslyn (dotnet-cbom), call-graph reachability (SCANOSS), atom/Joern (cdxgen), and the paper-only Cryptoscope and Crypsy. CodeQL is used only via CryptobomForge (pqc-mat).
8. **New research competitors, all paper-connected:**
   - **Crypsy** (Näther & Hirsch, arXiv 2608.04857, Aug 2026): CycloneDX 1.7; Go, Ruby and configs. Reports F1 0.92 vs 0.66 for CBOMkit on Go invocations. Code is available only via an OSF view-only link.
   - **Cryptoscope** (IBM Research, arXiv 2503.19531, Java): code not public.
   - **FuzzyExtract** (TCS, AsiaCCS 2026 poster, Java bytecode): code not public.
   - **Leirimaa** (MSc thesis, Jyväskylä 2024, Node.js regex): `github.com/krmapale/CBOM_tool`.
9. **Two competing empirical studies appeared in Aug 2026. Both have been read; see "Competing and related studies" and `papers/notes_*.md`.**
   - **Olewinski/Sandler/Ebinger (ARES 2026 workshops)** do not cite BF-CBOM, though it is in their tool-source sheet. They build a 49-tool capability matrix, then benchmark open-source tools on 30 hand-labelled assets: 1 Python file plus `openssl.cnf` and `nginx.conf`.
     - They report counts only, with no precision/recall. CryptoScan found 13/30, CBOMkit-hyperion 3/30, theia 4/30.
     - cdxgen, CBOM-Lens, cbom-action and cbomkit-action reported nothing. For cdxgen this follows from scope: 12.1.2 emitted a valid, empty BOM because its CBOM does not cover Python. Their supplement contains the raw CBOMs.
     - They conclude that all outputs schema-validate yet cannot be merged without custom normalisation, and recommend orchestrating tools. They build no orchestrator.
   - **Nocera/Scanniello (SEAA 2026)** cite BF-CBOM as [3]. They mined GitHub on 2026-04-22 and found 64 CBOM tools (46 generators, 7 consumers, 11 both). 93% of generators have no dependents. They do no output, validity or spec-version analysis.
   - **Recall check of our survey:** we had 40/64 of Nocera's tools. Of the 24 we lacked, only securin-xbom is a real static producer, and it is long-tail. The rest are TLS-only tools, wrappers, demos, consumers, or dead. Olewinski's matrix adds only commercial or network tools.
10. **Commercial tools.** None is clearly free for research: IBM (about 90-day trial) and ReversingLabs (14-day trial) are borderline. Mondoo xgrep is free of charge but proprietary, even though it's marketed as open source. Xygeni needs an account and a token. **SandboxAQ AQtive Guard imports CBOMs only** and does not produce them.

## Legend

- **Tiers.**
  - A: the paper's baseline tools and their wrappers.
  - B: newer tools that are substantive and/or backed by an organisation.
  - C: research prototypes connected to a paper.
  - D: long-tail community, startup and hackathon projects.
  - E: commercial, listed for reference only.
- **¹ Maintained** means a new version (release, tag or registry version) between 2026-04-06 and 2026-10-06. "unclear (commits YYYY-MM)" means there are no releases, only commits.
- **² Standard claimed** is what the tool states. Where the code was checked, the code's value wins. ⚠ means the output was found non-conformant to CycloneDX.
- **First pub.** is the first public appearance (repo creation, first release, or paper). For cdxgen the CBOM feature dates from 2024-04 (see CSV `cbom_since`).
- **★** is the GitHub star count as of 2026-10-06. Note that PQCensus's 297★ look inorganic: one commit, a brand-new account, 0 forks, 0 watchers.
- **BF-CBOM integr.** is a rough rating (easy/moderate/hard) of how easily the tool could run as a containerised, headless, offline worker.

## In-scope producers (static analysis → CBOM)


### Tier A – Baseline tools of the ICPC paper (and their wrappers) (10)

| Tool | Input | First pub. | Maintained¹ | Open source | Paper | CBOM coupling | Standard claimed² | Engine | Languages / inputs | BF-CBOM integr. | ★ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [cdxgen](https://github.com/cdxgen/cdxgen) | mixed: source, container im… | 2019-12 | yes (2026-10) | Apache-2.0 | – | in SBOM → filter `cryptographic-asset` (easy) | CycloneDX 1.7 (1.6 opt.) | Mixed: Java keystore/certificate parsing + a… | Java | easy | 1085 |
| [CryptobomForge (Santander)](https://github.com/Santandersecurityresearch/cryptobom-forge) | source via CodeQL | 2023-12 | no | GPL-3.0 | – | standalone | IBM CBOM draft 1.4-cbom-1.0 | CodeQL | Java, Python, Go?, ... whatever CodeQL crypt… | moderate | 41 |
| [sonar-cryptography (CBOMkit-hyperion)](https://github.com/cbomkit/sonar-cryptography) | source | 2024-06 | yes (2026-09) | Apache-2.0 | – | standalone | CycloneDX 1.6 | SonarQube plugin | Java | moderate | 86 |
| [CBOMkit (hyperion)](https://github.com/cbomkit/cbomkit) | source | 2024-09 | yes (2026-09) | Apache-2.0 | – | standalone | CycloneDX 1.6 | service around sonar-cryptography | Java, Python | moderate | 144 |
| [CBOMkit-theia](https://github.com/cbomkit/cbomkit-theia) | container | 2024-09 | yes (2026-05) | Apache-2.0 | – | standalone | CycloneDX 1.6 | Go | language-agnostic file/artifact scan | easy | 42 |
| [cbomkit-action](https://github.com/cbomkit/cbomkit-action) | source | 2025-02 | yes (2026-09) | Apache-2.0 per CBOMkit | – | standalone | CycloneDX 1.6 | Docker GitHub Action | Java, Python | easy | 16 |
| [cbomkit-lib](https://github.com/cbomkit/cbomkit-lib) | source | 2025-06 | yes (2026-09) | Apache-2.0 | – | standalone | CycloneDX (ver. n/s) | Java library embedding sonar-cryptography-pl… | Java, Python | moderate | 5 |
| [pqc-mat / VECTOR (AbstractionsLab)](https://github.com/AbstractionsLab/pqc-mat) | source | 2026-05 | unclear (commits 2026-07) | AGPL-3.0 | – | standalone | CycloneDX 1.6 | CodeQL + cryptobom-forge | Python, C, C++ | moderate | 1 |
| [cbom-scanner-cli (motosatoshi84)](https://github.com/motosatoshi84/cbom-scanner-cli) | mixed | 2026-08 | unclear (commits 2026-08) | ? | – | standalone | CycloneDX 1.6 | Meta-pipeline: cdxgen + cbomkit-theia | union of the three tools | hard | 0 |
| [lfreleng-actions/cbom-action (LF RelEng)](https://github.com/lfreleng-actions/cbom-action) | source | 2026-09 | yes (2026-09) | Apache-2.0 | – | standalone | CycloneDX (ver. n/s) | Wrapper: runs a CBOMkit scanner docker image | java, python, go, csharp | easy | 0 |

### Tier B – Newer / substantive tools (OSS or free) (14)

| Tool | Input | First pub. | Maintained¹ | Open source | Paper | CBOM coupling | Standard claimed² | Engine | Languages / inputs | BF-CBOM integr. | ★ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [CBOM-Lens (OmniTrust ILM)](https://github.com/OmniTrustILM/cbom-lens) | filesystem + container | 2025-09 | yes (2026-08) | Apache-2.0 | – | standalone | CycloneDX 1.6/1.7 | own Go detectors | not language-based | moderate | 4 |
| [SCANOSS crypto-finder](https://github.com/scanoss/crypto-finder) | source | 2025-10 | yes (2026-10) | GPL-2.0 | – | standalone via flag | CycloneDX 1.6 | OpenGrep | multi-language via OpenGrep rules | easy | 5 stars, 0 forks |
| [CipherIQ cbom-generator](https://github.com/CipherIQ/cbom-generator) | filesystem | 2025-12 | no | partial | – | standalone | CycloneDX 1.6/1.7 | C | n/a | moderate | 8 |
| [CryptoDeps (CSNP / QRAMM toolkit)](https://github.com/csnp/cryptodeps) | source-level dependency man… | 2025-12 | yes (2026-10) | Apache-2.0 | – | in SBOM → filter `cryptographic-asset` (easy) | CycloneDX 1.5/1.6 | Go | Go, npm, Python, Maven | easy | 5 |
| [CryptoScan (CSNP / QRAMM toolkit)](https://github.com/csnp/cryptoscan) | source + config + dependenc… | 2025-12 | yes (2026-07) | Apache-2.0 | – | standalone | CycloneDX 1.6 | Go, regex/pattern rules over files | 15+ languages recognised | easy | 21 |
| [CBOMpy (Monet+)](https://monetplus.cz/en/identity/tools-for-pqc-analysis) | source + config files | 2026-01 | unclear | ✗ closed | – | standalone | CycloneDX (ver. n/s) | static analysis | Java, .NET, Python | hard | n/a |
| [cbom4cert (Anthony Harrison)](https://github.com/anthonyharrison/cbom4cert) | filesystem | 2026-03 | yes (2026-04) | Apache-2.0 | – | standalone | CycloneDX (ver. n/s) | Python `cryptography` library X.509 parsing … | n/a | easy | 0 |
| [dotnet-cbom (PostQuantum.CryptographicBillOfMaterials)](https://github.com/systemslibrarian/PostQuantum.CryptographicBillOfMaterials) | source | 2026-06 | yes (2026-08) | MIT in repo/v1.2.0 | – | standalone | CycloneDX 1.6 | Roslyn semantic analysis | C#/.NET only | moderate | 0 |
| [Mondoo xgrep](https://mondoo.com/docs/xgrep/code-scanning/cbom) | source | 2026-06 | yes (2026-09) | ✗ closed | – | standalone via flag | CycloneDX 1.6 | tree-sitter AST | Java, Kotlin, Scala, Python, Go, C#, JS/TS, … | easy | npm only |
| [qScan / quantakrypto pqc-tools](https://github.com/quantakrypto/pqc-tools) | source + config/IaC | 2026-06 | yes (2026-08) | Apache-2.0 | – | standalone | CycloneDX 1.6 | own pattern/regex detectors in TypeScript | 14 languages: JS/TS, Python, Go, Java/Kotlin… | easy | 14 |
| [qelens cbom (ex keylens)](https://github.com/qelens/cbom) | source | 2026-07 | yes (2026-07) | MPL-2.0 | – | standalone via flag | CycloneDX 1.5/1.6 | tree-sitter AST | README: Python and JavaScript | easy | 0 |
| [qrp-mcp (Elkan Electronic)](https://github.com/StanimirTenev/qrp-mcp) | source + CI/CD configs + IaC | 2026-08 | yes (2026-10) | Apache-2.0 | – | standalone | CycloneDX 1.6 | own scanner in Python | C/C++, .NET, Go, Java, JS/TS, PHP, Python, R… | easy | GitHub stars not … |
| [PQCensus (XiantingWu)](https://github.com/XiantingWu/PQCensus) | source + config + dependenc… | 2026-09 | unclear (commits 2026-09) | Apache-2.0 | – | standalone | CycloneDX 1.7 | Python AST | Python | easy | 297 |
| [Xygeni Scanner CLI](https://docs.xygeni.io/xygeni-scanner-cli/xygeni-cli-overview/generate-cbom-with-the-xygeni-cli) | source + app config + IaC +… | ? | unclear | ✗ closed | – | standalone | CycloneDX 1.6 | proprietary static analysis of cryptographic… | Java, Kotlin, JavaScript/TypeScript, Python,… | hard | n/a |

### Tier C – Research prototypes (paper-connected) (4)

| Tool | Input | First pub. | Maintained¹ | Open source | Paper | CBOM coupling | Standard claimed² | Engine | Languages / inputs | BF-CBOM integr. | ★ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [CBOM_tool / pqcbom.js (Leirimaa MSc)](https://github.com/krmapale/CBOM_tool) | source | 2024-10 | unclear | ? | Kristian Leirimaa. 'Supporting PQC migration and cryptographic agility… | standalone | CycloneDX 1.6 | regular-expression search | JavaScript / Node.js crypto module | moderate | 0 stars, 0 forks |
| [Cryptoscope (IBM Research)](https://arxiv.org/abs/2503.19531) | source | 2025-03 | unclear | ✗ closed | Micha Moffie, Omer Boehm, Anatoly Koyfman, Eyal Bin, Efrayim Sztokman,… | standalone | CycloneDX 1.6 | custom: ANTLR parsing, own data-flow + contr… | Java only | hard | n/a |
| [FuzzyExtract (TCS Research)](https://doi.org/10.1145/3779208.3804889) | binary | 2026-06 | unclear | ? | Manish Shukla, Sachin Lodha. 'POSTER: FuzzyExtract - A Lightweight App… | unclear | CBOM | stack simulation + depth-bounded approximate… | Java bytecode | hard | n/a |
| [Crypsy (Näther & Hirsch)](https://osf.io/4bc5p/overview?view_only=dc5aab1d48d9466f8260fe4e2a8e0e79) | source + filesystem config … | 2026-08 | unclear | partial | Christian Näther, Eduard Hirsch. 'Hidden Ciphers and Where to Find The… | standalone | CycloneDX 1.7 | rule-based static discovery | Go, Ruby | unknown/har… | n/a |

### Tier D – Long tail: small community / startup / hackathon projects (2025-2026) (51)

| Tool | Input | First pub. | Maintained¹ | Open source | Paper | CBOM coupling | Standard claimed² | Engine | Languages / inputs | BF-CBOM integr. | ★ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [Aqua-CBOM (ppscon/CBOM)](https://github.com/ppscon/CBOM) | container image | 2025-09 | no | partial | – | standalone | CycloneDX 1.4/1.6 | Prebuilt Go binary 'aqua-cbom' on extracted … | container filesystems | moderate | 1 |
| [RivicQ CSPM / CryptoBOM SaaS (RivicQ GmbH; repo moved from rivic-q/cryptobom-saas to RivicQ/RivicQ_CSPM_EaaS)](https://github.com/RivicQ/RivicQ_CSPM_EaaS) | mixed: GitHub repository co… | 2025-12 | unclear | partial | – | standalone | CycloneDX 1.6 | Go regex rule table | language-agnostic regex | moderate | 3 stars / 1 fork |
| [CryptoServe (ecolibria)](https://github.com/ecolibria/cryptoserve) | source + dependency manifes… | 2026-01 | yes (2026-08) | Apache-2.0 | – | standalone via flag | CycloneDX 1.5 ⚠ | regex/pattern scanner | JS/TS, Go, Python, Java/Kotlin, Rust, C/C++ | moderate | 3 |
| [CipherRadar (nk-sentinel; cradar CLI)](https://github.com/nk-sentinel/cipherradar) | source | 2026-03 | yes (2026-09) | partial | – | standalone | CycloneDX 1.7 | OpenGrep rules + tree-sitter + YARA rules, l… | multi-language source | easy | 1 |
| [demo-pqc-kyverno CBOM scanner (nirmata)](https://github.com/nirmata/demo-pqc-kyverno) | source and binaries | 2026-03 | no | ? | – | standalone | CycloneDX 1.6 | regex/pattern matching over source text | Python, Java, Go, Rust, C/C++ | moderate | 0 stars / 0 forks |
| [pqaudit (PQCWorld)](https://github.com/PQCWorld/pqaudit) | source | 2026-03 | yes (2026-04) | MIT | – | standalone via flag | CycloneDX 1.6 | TypeScript regex/rule-based | multi-language source | easy | 3 |
| [PQC-Atlas (saisravan909)](https://github.com/saisravan909/pqc-atlas) | source | 2026-03 | no | Apache-2.0 | – | standalone | CycloneDX 1.7 | go/parser AST for Go | Go, Java, Python | moderate | 1 |
| [qproof (Qryptonic / qproof contributors)](https://pypi.org/project/qproof/) | source + dependency manifes… | 2026-03 | no | MIT | – | standalone via flag | CycloneDX 1.6 | regex/pattern scanners + classifier + policy… | Python, JS/TS, Go, Java, Rust + configs | easy | repo qproof/qproo… |
| [qsafe / pqc-posture (Miles0sage/quantum-mcp)](https://pypi.org/project/qsafe/) | source | 2026-03 | no | MIT | – | inside other artifact | CycloneDX (ver. n/s) | regex | multi-language regex | moderate | repo Miles0sage/q… |
| [qscan (EthanCratchley)](https://github.com/EthanCratchley/qscan) | source | 2026-03 | unclear (commits 2026-04) | MIT | – | standalone via flag | CycloneDX 1.6 | regex + tree-sitter + entropy + shadow-crypt… | multi-language source | easy | 1 |
| [The-Crypto-Audit-CBOM- / CBOM Auditor (shaghaf-404)](https://github.com/shaghaf-404/The-Crypto-Audit-CBOM-) | source | 2026-03 | no | ? | – | standalone via flag | CycloneDX (ver. n/s) | Python ast walker + regex tables + dependenc… | Python only | moderate | 1 star / 0 forks |
| [Open Quantum Secure / OQS Scanner (jimbo111)](https://github.com/jimbo111/open-quantum-secure) | source + live TLS/SSH endpo… | 2026-04 | unclear (commits 2026-08) | MIT | – | standalone via flag | CycloneDX 1.7 | Go, own multi-engine scanners | multi-language source | easy | 3 |
| [securin-xbom (Securin, securin-public/securin-xbom)](https://github.com/securin-public/securin-xbom) | source | 2026-04 | no | Apache-2.0 per pyproje | – | standalone via flag | CycloneDX 1.6 | Semgrep with ~13 custom YAML rule files | Python, JavaScript/TypeScript, Java, Go, C/C… | moderate | 0 stars / 0 forks |
| [acdi (crates.io; vral-parmar/acdi)](https://crates.io/crates/acdi) | mixed: filesystem, source, … | 2026-05 | yes (2026-05) | MIT | – | standalone via flag | CycloneDX 1.7 | Rust single binary | multi-language source, configs, binaries | easy | crates.io 273 dow… |
| [cryptosweep (maximilliangrand)](https://github.com/maximilliangrand/cryptosweep) | source + dependency manifes… | 2026-05 | unclear (commits 2026-10) | MIT | – | standalone via flag | CycloneDX 1.6 | TypeScript scanners with per-finding confide… | multi-language source via patterns, manifests | easy | 0 |
| [CycloGuard (deepansh257)](https://github.com/deepansh257/cycloguard) | source | 2026-05 | unclear (commits 2026-07) | ? | – | standalone | CycloneDX 1.6 | TypeScript AST | JS/TS | moderate | 1 |
| [pqc-readiness-cli (qtonicquantum) [successor of qtonicquantum/pqc-readiness-checklist; NEW LEAD not in Nocera list]](https://github.com/qtonicquantum/pqc-readiness-cli) | filesystem: PEM/DER certifi… | 2026-05 | unclear | MIT | – | standalone | CycloneDX 1.7 | X.509 parsing | N/A | easy | 0 stars |
| [pqcheck (Rafael-Ryu)](https://github.com/Rafael-Ryu/pqcheck) | source + lockfiles + PEM/DE… | 2026-05 | yes (2026-07) | Apache-2.0 | – | standalone | CycloneDX 1.6 | Python tree-sitter + lockfile parsing + YAML… | multi-language source, lockfiles, certs/keys | easy | 0 |
| [precogs-cbom / @precogs/cbom (Precogs.ai)](https://www.npmjs.com/package/precogs-cbom) | mixed: source | 2026-05 | yes (2026-07) | ? | – | standalone via flag | CycloneDX 1.6 | web-tree-sitter | JS/TS, Python, Java, Go, Ruby, PHP, C/C++, C… | moderate | npm: 2 versions |
| [qryx (TAIPANBOX)](https://github.com/TAIPANBOX/qryx) | mixed: source, binaries, co… | 2026-05 | yes (2026-09) | Apache-2.0 | – | standalone via flag | CycloneDX 1.6 | Go | Go | easy | 2 |
| [Reel GitHub Action / Reel CLI (getreeldev)](https://github.com/getreeldev/reel-action) | container | 2026-05 | yes (2026-07) | ✗ closed | – | standalone | CycloneDX (ver. n/s) | closed-source Reel CLI | container images | hard | 0 |
| [Spectra (HarshalPatel1972)](https://github.com/HarshalPatel1972/spectra) | source + deps + certs + liv… | 2026-05 | yes (2026-05) | MIT per README badge | – | standalone via flag | CycloneDX 1.6/1.7 | Go | Go, Python, Java, JS + manifests/config | easy | 1 |
| [cbom-scan (CoupDeVent)](https://github.com/CoupDeVent/cbom-scan) | source | 2026-06 | unclear (commits 2026-06) | Apache-2.0 | – | standalone | CycloneDX 1.7 ⚠ | Python regex/pattern | Python, Java, JavaScript | easy | 1 |
| [cbom-scan (thefinalmilkman)](https://github.com/thefinalmilkman/cbom-scan) | source | 2026-06 | no | MIT | – | standalone | CycloneDX 1.6 | Node.js zero-dep pattern scanner | multi-language | easy | 0 |
| [Janus CryptoBOM (Sean-Khorasani)](https://github.com/Sean-Khorasani/Janus_CryptoBOM) | mixed: source, binaries | 2026-06 | unclear (commits 2026-06) | Apache-2.0 per README | – | standalone via flag | CycloneDX 1.6 | Rust agent: comment/string-stripped static s… | source multi-language | hard | 1 |
| [qsentinel (0xjyotirmoy)](https://github.com/0xjyotirmoy/qsentinel) | mixed: live TLS endpoints | 2026-06 | unclear (commits 2026-08) | MIT | – | standalone | CycloneDX 1.6 | active TLS/PQ-group probe | TLS endpoints | moderate | 4 |
| [QuantumSafe Scan (Danny-397, PyPI quantumsafe-scan)](https://github.com/Danny-397/Quantum-Safe-Scan) | source | 2026-06 | yes (2026-07) | MIT | – | standalone via flag | CycloneDX 1.6 | Python regex/pattern rules | multi-language source | moderate | 3 |
| [saqura-pqc-scan (KyotoTechLLC)](https://github.com/KyotoTechLLC/saqura-pqc-scan) | source | 2026-06 | yes (2026-06) | MIT | – | standalone via flag | CycloneDX 1.6 | Node.js pattern scanner | multi-language source | easy | 0 |
| [cryptoscan (Ubermm)](https://github.com/Ubermm/cryptoscan) | filesystem | 2026-07 | yes (2026-07) | Apache-2.0 | – | standalone via flag | CycloneDX 1.6 | Go | multi-language source via patterns | easy | 0 |
| [php-laravel-sbom-cbom (chetanganatra)](https://github.com/chetanganatra/php-laravel-sbom-cbom) | source | 2026-07 | unclear (commits 2026-07) | MIT | – | standalone | CycloneDX 1.6 | PHP regex/pattern scanning | PHP | easy | 0 |
| [PQ Readiness Scorecard GitHub Action (brandonjsellam-Releone / TRELYAN)](https://github.com/brandonjsellam-Releone/pq-readiness-scorecard) | source + manifests | 2026-07 | yes (2026-07) | MIT | – | standalone via flag | CycloneDX 1.6 | regex rule table in a single 113 KB Node scr… | language-agnostic text regex | easy | 0 stars, 0 forks |
| [pqc-scan (sachhg)](https://github.com/sachhg/pqc-scan) | source + configs/deps/keys/… | 2026-07 | yes (2026-09) | MIT | – | standalone via flag | CycloneDX 1.6 | tree-sitter AST | multi-language source | easy | 3 |
| [ProofByte pqc-radar (oleg-vdv)](https://github.com/oleg-vdv/proofbyte) | source | 2026-07 | yes (2026-07) | MIT | – | standalone via flag | CycloneDX 1.6 | regex/pattern scanner | Java/Kotlin/Scala, Python, JS/TS, Go, C#, Ru… | easy | 1 |
| [shorproof](https://github.com/shorproof/shorproof) | source | 2026-07 | yes (2026-08) | MIT | – | standalone via flag | CycloneDX 1.6 | TypeScript, binding-aware import tracking | JavaScript/TypeScript only | easy | 1 |
| [smp-pqc-inventory (crates.io; rwilliamspbg-ops/smp-pqc-testkit)](https://crates.io/crates/smp-pqc-inventory) | dependencies | 2026-07 | yes (2026-07) | Apache-2.0 | – | standalone | CycloneDX 1.6 | Cargo.lock parser + curated crate classifica… | Rust | easy | crates.io 63 down… |
| [cbom_scan.py (bangaaman/cbom)](https://github.com/bangaaman/cbom) | source | 2026-08 | unclear (commits 2026-08) | ? | – | standalone | CycloneDX 1.6 | Python stdlib regex, 112 rules, CWE-mapped | Python, Java/Kotlin, JS/TS, Go, .NET, PHP, R… | easy | 1 |
| [PQC & CBOM Risk Auditor (pietrodiwalsi-design)](https://github.com/pietrodiwalsi-design/pqc-cbom-risk-auditor) | source | 2026-08 | unclear (commits 2026-09) | Apache-2.0 | – | standalone | CycloneDX 1.6 ⚠ | regex per file extension | .py .java .go .js .ts .c .cpp .cs .kt .rs | moderate | 1 |
| [QuantumSentinel (exocognosis)](https://github.com/exocognosis/QuantumSentinel) | source | 2026-08 | yes (2026-08) | MIT | – | standalone | CycloneDX (ver. n/s) | regex rules over file lines | language-agnostic text regex | moderate | 0 |
| [3329lens / lens3329 (crates.io; 3329lens/3329lens)](https://crates.io/crates/lens3329) | source tree + lockfiles | 2026-09 | yes (2026-09) | Apache-2.0 | – | standalone via flag | CycloneDX 1.6 | Rust | Rust, npm, PyPI ecosystems | easy | crates.io 17 down… |
| [c-bom (nagarjunsanji)](https://github.com/nagarjunsanji/c-bom) | Node package.json | 2026-09 | unclear | ? | – | standalone | CycloneDX 1.6 | TypeScript | npm dependencies | moderate |  |
| [cbom-tools (npm; lennonhah)](https://www.npmjs.com/package/cbom-tools) | source | 2026-09 | yes (2026-10) | Apache-2.0 per package | – | standalone | CycloneDX 1.6 | regex patterns + declarative package.json de… | JS/TS | easy | npm: 4 versions i… |
| [cbomscope (polycratia)](https://github.com/polycratia/cbomscope) | source + live TLS probe | 2026-09 | unclear (commits 2026-10) | MIT | – | standalone | CycloneDX 1.6 | Go | source files | easy | 2 |
| [cryptarium (sgoveia)](https://github.com/sgoveia/cryptarium) | source + go.mod + certs/key… | 2026-09 | yes (2026-09) | Apache-2.0 | – | standalone | CycloneDX 1.6 | Go + tree-sitter | Go, Python, JS/TS, Java, C/C++ | easy |  |
| [CRYPTONEX (nivas1899)](https://github.com/nivas1899/cryptonex) | source, dependency manifest… | 2026-09 | unclear (commits 2026-09) | Apache-2.0 | – | standalone via flag | CycloneDX 1.6 | Python regex/tree-sitter rules | Python, Java, JS/TS, C#, Rust, PHP, Ruby, Sw… | easy | 0 |
| [ECDAT cluster (SIH 2026 SIH26164) incl. PyPI 'ecdat' (profm0r14rty/ecdat)](https://pypi.org/project/ecdat/) | source | 2026-09 | yes (2026-09) | MIT per README badge | – | standalone | CycloneDX 1.6 | regex signature engine | Python, JavaScript/TypeScript, Java, others … | easy | ecdat repo 0 stars |
| [kxco-pq-scan (KnightsbridgeAIQ)](https://github.com/KnightsbridgeAIQ/kxco-pq-scan) | filesystem | 2026-09 | unclear (commits 2026-10) | Apache-2.0 | – | standalone via flag | CycloneDX 1.6 | Node.js dependency-tree/package analysis | JavaScript/npm dependencies | easy | 0 |
| [pqinv (angga-22)](https://github.com/angga-22/pqinv) | mixed: source repo | 2026-09 | yes (2026-09) | MIT | – | standalone via flag | CycloneDX 1.6 | Go | Go, JWT/JOSE strings, other source via regex | easy | 0 |
| [Q-DARPAN (RohanExploit)](https://github.com/RohanExploit/q-darpan) | mixed: source | 2026-09 | unclear (commits 2026-09) | Apache-2.0 | – | standalone | CycloneDX 1.7 | tree-sitter | C, C++, Java, Go | easy | 0 |
| [quantum-safe-audit / pqc-audit (AnimeshShaw/quantum-safe-ts)](https://github.com/AnimeshShaw/quantum-safe-ts) | source | 2026-09 | yes (2026-10) | Apache-2.0 | – | standalone via flag | CycloneDX 1.6 | AST walk | JavaScript, TypeScript | easy | 1 |
| [CryptoAgility (chimaobikanu / Mainno Ltd)](https://github.com/chimaobikanu/cryptoagility) | source, config, certificate… | 2026-10 | yes (2026-10) | AGPL-3.0 | – | standalone | CycloneDX 1.6 | Single-file Python | multi-language source via patterns | easy | 0 |
| [HealthPQC (GiaDang-GCT-TG-VN)](https://github.com/GiaDang-GCT-TG-VN/HealthPQC-PQC-framework-for-healthcare) | source | 2026-10 | unclear (commits 2026-10) | MIT | – | standalone | CycloneDX 1.6 | Python ast | Python | easy | 1 |

## Commercial products (reference list; none verified free for research)

Short records only, as agreed. "Scope = yes" means a static (code/binary) CBOM export is documented or claimed. Most of the rest is marketing-level evidence. Entries marked "matrix" come from the Olewinski et al. supplement.

| Product | Vendor | Scope | Input | Standard claimed | CBOM since | Free for research? | Reference |
|---|---|---|---|---|---|---|---|
| AgileSec Analytics (ex InfoSec Global) | Keyfactor | yes | mixed: source code, binaries, file… | CycloneDX (ver. n/s) | ? | no | https://docs.keyfactor.com/agilesec/latest/introduction |
| AVX ONE PQC Assessment Tool | AppViewX | yes | mixed: code, dependencies, package… | CycloneDX (ver. n/s) | 2025-04 | no | https://www.businesswire.com/news/home/20250423931888/en |
| Binarly Transparency Platform (BTP) | Binarly | yes | binary | CycloneDX (ver. n/s) | 2024-11 | no | https://docs.binarly.io/user-guides/export/cbom |
| CBOM Secure | Encryption Consulting | yes | mixed: source code, binaries, cont… | CycloneDX 1.6 | ? | unclear | https://www.easternprogress.com/encryption-consulting-unveils-industry-first-cbom-solution-to-secure-software-supply-chains-against-quantum-threats/article_8529c74c-008b-5f0f-aa28-3718a39f4b2c.html |
| CipherScan | QuantumGenie | yes | mixed: source | CBOM | ? | unclear | https://www.einpresswire.com/article/935699176/quantumgenie-now-available-on-microsoft-aws-google-cloud-vs-code-splunk-marketplaces-for-post-quantum-cryptography |
| Delivery Shield CBoM | OpsMx | yes | source | CycloneDX (ver. n/s) | 2025-01 | no | https://go.opsmx.com/hubfs/Delivery-Shield-CBoM-Datasheet-30-Jan-25.pdf |
| IBM Quantum Safe Explorer / Guardium Quantum Safe / Guardium Cryptography Manager | IBM | yes | mixed: source + object code | CycloneDX 1.6 | 2023-05 | unclear | https://newsroom.ibm.com/2023-05-10-IBM-Unveils-End-to-End-Quantum-Safe-Technology-to-Safeguard-Governments-and-Businesses-Most-Valuable-Data |
| JFrog Advanced Security (Xray) CBOM export | JFrog | yes | artifacts/binaries/containers in A… | CycloneDX (ver. n/s) | ? | no | https://docs.jfrog.com/security/docs/cbom-export |
| QuProtect R3 Reconnaissance | QuSecure | yes | binary | CBOM | 2025-08 | unclear | https://www.businesswire.com/news/home/20250804287956/en/Binarly-Partners-with-QuSecure-to-Accelerate-Enterprise-PostQuantum-Readiness |
| Spectra Assure | ReversingLabs | yes | binary/compiled software, containe… | CycloneDX 1.6 | 2025-04 | unclear | https://www.reversinglabs.com/press-releases/reversinglabs-delivers-most-comprehensive-support-for-cyclonedx-xbom |
| Crypto Discovery Tool (CDT) | QuantumGate | unclear | static + filesystem + config + pac… | CBOM import/export | ? | no / unclear | https://quantumgate.ae/ |
| Cryptographic AI^2 | QryptoCyber | unclear | static + config + network | CBOM import/export | ? | no / unclear | https://qryptocyber.com/ |
| Cryptographic Discovery Tool | SISA | unclear | static + filesystem | n/s | ? | no / unclear | https://www.sisainfosec.com/ |
| InSight | VigilantOps | unclear | filesystem + package manager | CBOM import/export | ? | no / unclear | https://www.vigilant-ops.com/ |
| ISARA Advance | ISARA | unclear | n/s | n/s | ? | no / unclear | https://www.isara.com/products/isara-advance-cryptographic-inventory-and-risk-assessment-tool.html |
| NetRise Platform | NetRise | unclear | firmware/binaries | n/s | ? | no / unclear | https://www.netrise.io/ |
| Pcert | Datawarehouse | unclear | code/pipelines, HSM, TPM, truststo… | n/s | ? | no / unclear | https://datawh.info/en/pcert/ |
| PostQ | CyberSeQ | unclear | network + code + filesystem + runt… | CBOM | ? | no / unclear | https://www.cyberseq.io/ |
| PostQ Code Scanner | PostQ Software Labs | unclear | source repo | CycloneDX (ver. n/s) | ? | unclear | https://hub.docker.com/r/postqsoftwarelabs/postqcodescanner |
| PQC Scan | Duokey | unclear | certificates | n/s | ? | no / unclear | https://resources.duokey.com/ |
| PQCscan | Chelpis | unclear | static + network | n/s | ? | no / unclear | https://www.chelpis.com/ |
| QCecuring CBOM | QCecuring | unclear | unknown | unknown | ? | unclear | https://www.qcecuring.com/blog/qcecuring-vs-sandboxaq-aqtive-guard-cbom |
| QryptoCode / QryptoScan suite | QryptoCyber | unclear | code | CBOM claimed | ? | no / unclear | https://qryptocyber.com/qryptocode-code-encryption-scanning-cryptographic-inventory/ |
| QVision | PQStation | unclear | network + filesystem + runtime | n/s | ? | no / unclear | https://www.pqstation.com/#qvision |
| S-CAPE | Samsung SDS | unclear | code | n/s | ? | no / unclear |  |
| Tychon ACDI | Tychon | unclear | endpoints | n/s | ? | no / unclear | https://tychon.io/ |
| Black Duck / Snyk / Checkmarx / Veracode / Semgrep / GitHub Advanced Security / SonarQube (combined negative record) | Synopsys-Black Duck, Snyk, Checkmarx, Veracode, Semgrep, GitHub, Sonar | no-cbom | source | none found | ? | no | https://checkmarx.com/glossary/what-is-a-cbom/ |
| AQtive Guard | SandboxAQ | not-producer | CBOM import only | CycloneDX 1.4/1.6 | ? | no | https://docs.aqtiveguard.com/data-sources/cbom/ |
| ClaveQ Scan | Quantum Clave Ltd | runtime | network traffic monitoring | CBOM | ? | unclear | https://www.ipo.gov.uk/t-tmj/tm-journals/2026-031/UK00004418532.html |
| CryptoNext COMPASS (Analytics + Network Probe) | CryptoNext Security | runtime | Analytics aggregates CBOM/other da… | CBOM-formatted output | 2025-04 | no | https://cryptonext-security.com/news/cryptonext-security-launches-cryptonext-compass-discovery-the-first-truly-open-cryptographic-assets-discovery-and-analysis-solution |
| Entrust Cryptographic Security Platform (CBOM import/export) | Entrust | runtime | network/cloud-API/key-vault discov… | CBOM | 2026-09 | no | https://helpnetsecurity.com/2026/09/14/entrust-cbom-capabilities |
| Eon Insights | EonCore | runtime | network traffic + SCA via GitHub/G… | CycloneDX 1.6 | ? | no / unclear | https://eoncore.io/ |
| Essendi CD | EssendiIT | runtime | filesystem + network | none | ? | no / unclear | https://www.essendi.de/ |
| Fortanix Key Insight | Fortanix | runtime | cloud/on-prem key and data-service… | CBOM mentioned in blog, expor… | ? | no | https://fortanix.com/blog/simplifying-pqc-migration-with-fortanix-key-insight-in-the-post-quantum-era |
| PQC Scan | Noreg | runtime | network | n/s | ? | no / unclear | https://noreg-inc.eu/ |
| PQC-Tester | Wiz | runtime | domains | none | ? | no / unclear | https://www.wiz.io/ |
| Qinsight Atlas | Qinsight (Utimaco integration partner) | runtime | network, cloud, services, HSM key/… | CBOM | ? | no | https://integration-guides.utimaco.com/qinsight-atlas-and-u-trust-gp-hsm/1.0.0/product-overview |
| Sectigo CLM | Sectigo | runtime | certificates | n/s | ? | no / unclear | https://www.sectigo.com/ |


## Out of scope / not producers (for references and exclusion criteria)


#### unclear (10)
- [OxPirates/crypto-bom-scanner](https://github.com/OxPirates/crypto-bom-scanner) — Empty placeholder repo
- [QuantumSeal (mpaymenremora)](https://github.com/mpaymenremora/QuantumSeal) — Same README/concept as chuangtian/QuantumSeal
- [QuantumSeal (chuangtian)](https://github.com/chuangtian/QuantumSeal) — Same README/concept as mpaymenremora/QuantumSeal
- [XIPHER (Comcast, Jayati Dev)](https://www.usenix.org/conference/usenixsecurity26/presentation-12) — Talk only
- PQCScanner (CCSC Central Plains 2026) — NOT FOUND: web searches returned no paper/repo named PQCScanner
- [NBU-CBOM-GEN (codedeno/NBU_CBOM_TOOL)](https://github.com/codedeno/NBU_CBOM_TOOL) — Borderline scope: host filesystem/config inventory
- [pqc-infra-scanner (juankaukian)](https://github.com/juankaukian/pqc-infra-scanner) — Emits a self-defined CBOM, not CycloneDX
- [QShield (YashSwastik/QShiled)](https://github.com/YashSwastik/QShiled) — README says 'Hackathon project'
- [rollcall-assay (crates.io; smhasan94/rollcall)](https://crates.io/crates/rollcall-assay) — Name-reservation/placeholder crate for a planned 'assay' CBOM feature of the firmware SBOM tool rollcall
- [quantumshield-pqc (PyPI)](https://pypi.org/project/quantumshield-pqc/) — Package does not exist under this name

#### no-cbom (17)
- [advanced-security/cbom-action (GitHub)](https://github.com/advanced-security/cbom-action) — Despite its name it does NOT output a CBOM in any standard
- [Quartz (Cisco, cisco-open/Quartz)](https://github.com/cisco-open/Quartz) — Paper-claimed CBOM generator whose public code emits no CBOM
- [Hirsch et al. LLM package classifier (collaborative LLMs)](https://arxiv.org/abs/2603.07204) — First-pass filter to locate crypto-relevant packages
- [SATAM / Architecture-Derived CBOMs (Hirsch & Raab)](https://arxiv.org/abs/2603.22442) — Method not tool
- [pqc-scanner (dugisan3rd)](https://github.com/dugisan3rd/pqc-scanner) — Wrapper of Syft/Grype/Semgrep
- [CBOM-scanning (msaufyrohmad, MCTMC Malaysia)](https://github.com/msaufyrohmad/CBOM-scanning) — Researcher scripts
- [Quantum-Safe-Scanner (GowithKeya)](https://github.com/GowithKeya/Quantum-Safe-Scanner) — Unmodified Google AI Studio app boilerplate README
- [Quantasyte CLI (mzon7)](https://github.com/mzon7/quantasyte-cli) — Commercial funnel product
- [PQC Cryptographic Inventory & Risk Analysis Tool (Cybergod11)](https://github.com/Cybergod11/pqc-crypto-inventory) — Student/defense-themed demo
- [cbom-platform (kasimvali9)](https://github.com/kasimvali9/cbom-platform) — Empty placeholder repository
- [q-periapt-cli (crates.io; billlza/q-periapt)](https://crates.io/crates/q-periapt-cli) — CBOM is a declaration of the hybrid-PQC library's own assets
- [CBOM Builder (foikwuogu)](https://github.com/foikwuogu/cbom-builder) — Academic-style
- [BnHany/QuantumLedger-CBOM](https://github.com/BnHany/QuantumLedger-CBOM) — Flask web app with static scanners
- [mini-pqc-scanner (Quantumcrafts)](https://github.com/oferzinger/mini-pqc-scanner) — Also wrapped by dugisan3rd/pqc-scanner. Source: Olewinski et al. ARES 2026 supplement matrix
- [Soheib1/PQC_SBOM_CBOM](https://github.com/Soheib1/PQC_SBOM_CBOM) — University coursework: PQC TLS server
- [test-srm-digi/cbom-analyser](https://github.com/test-srm-digi/cbom-analyser) — 404: repository does not exist / deleted or private
- [Wind River crypto-detector](https://github.com/Wind-River/crypto-detector) — Unmaintained since 2020

#### not-producer (10)
- [QBOM (csnp/qbom)](https://github.com/csnp/qbom) — QBOM = Quantum BOM, not cryptographic
- [DevSecOps BOM Toolkit (anshumaanzee)](https://github.com/anshumaanzee/devsecops-bom-toolkit) — Wrapper/reference collection for 9 BOM types by a DevSecOps blogger
- [actions-marketplace-validations/PQCA_cbomkit-action](https://github.com/actions-marketplace-validations/PQCA_cbomkit-action) — Auto-mirror
- [cbomkit/cbomkit-pipeline](https://github.com/cbomkit/cbomkit-pipeline) — Azure DevOps pipeline.yml
- [ccsArtifacts/rg_CBOM](https://github.com/ccsArtifacts/rg_CBOM) — Actually OneForLearning/Post-Quantum-Cryptography-Preparation
- [CelestinMrx/cbom-forge](https://github.com/CelestinMrx/cbom-forge) — Tutorial copy
- [ikshatelkar/cbom-crypto-risk-engine](https://github.com/ikshatelkar/cbom-crypto-risk-engine) — Consumer: ingests an existing CBOM
- [nihharikadubey/SecureScan-CBOM-and-MBOM-Analysis](https://github.com/nihharikadubey/SecureScan-CBOM-and-MBOM-Analysis) — Azure DevOps pipeline docs/YAML wrapping cbomkit_theia Docker image
- [qtonicquantum/pqc-readiness-checklist](https://github.com/qtonicquantum/pqc-readiness-checklist) — ARCHIVED, superseded by qtonicquantum/pqc-readiness-cli
- [sujith-eag/devsecops-pqc-audit](https://github.com/sujith-eag/devsecops-pqc-audit) — Docker image

#### runtime (22)
- [DynamicCBOM (SEG-UNIBE, Univ. Bern)](https://github.com/SEG-UNIBE/DynamicCBOM) — Experimental, same Bern group as BF-CBOM
- [CarnotEngine (Maverick0351a)](https://github.com/Maverick0351a/CarnotEngine) — Older than the 2026 batch
- [QRAMM TLS Analyzer (csnp/tls-analyzer)](https://github.com/csnp/tls-analyzer) — CSNP
- [AC Scanner (qubitac)](https://github.com/qubitac/AC-Scanner) — Black Hat Arsenal USA 2026 tool
- [QuantumSentry (namannavneet25)](https://github.com/namannavneet25/QuantumSentry) — PSB Cybersecurity Hackathon 2026 project
- [CryptaMap (aws-samples)](https://github.com/aws-samples/sample-CryptaMap) — AWS sample code
- [Janus (Keninjavelas)](https://github.com/Keninjavelas/Janus) — Self-described experimental research platform
- [Contrast Runtime Analyst](https://github.com/Contrast-Security-OSS/runtime-analyst) — CBOM from runtime telemetry of Contrast agents
- [keycensus (FlavioImbertDomingos)](https://github.com/FlavioImbertDomingos/keycensus) — Joins SBOMs to keys
- [aahil-khan/krow](https://github.com/aahil-khan/krow) — Krow: Next.js/Express
- [AntimonyTrisulfide/QuantWarden-CBOMAPI](https://github.com/AntimonyTrisulfide/QuantWarden-CBOMAPI) — FastAPI service building a custom
- [Arjya01/QSecure](https://github.com/Arjya01/QSecure) — Flask enterprise console
- [Crypto Detection Toolbox (nwa/pma/ssha)](https://gitlab.com/pqc-cdt) — Source: Olewinski et al. ARES 2026 supplement matrix
- [Mr-Suave/PNB-Hackathon](https://github.com/Mr-Suave/PNB-Hackathon) — Hackathon dashboard
- [oscrypto (QuantumVillage osquery crypto queries)](https://github.com/QuantumVillage/) — Source: Olewinski et al. ARES 2026 supplement matrix
- [PQC TLS Scanner (QuantaSeek)](https://github.com/Mehrn0ush/QuantaSeek) — Source: Olewinski et al. ARES 2026 supplement matrix
- [pqcscan (Anvil Secure)](https://github.com/anvilsecure/pqcscan) — Network prober, 109 stars. Source: Olewinski et al. ARES 2026 supplement matrix
- [sagar-16rs/PNB-CYBER-SECURITY-](https://github.com/sagar-16rs/PNB-CYBER-SECURITY-) — Streamlit TLS/X.509 scanner
- [sagar-16rs/QUANTUM-SCANNER](https://github.com/sagar-16rs/QUANTUM-SCANNER) — Earlier copy of the same Streamlit TLS scanner
- [simelbey-code/acdi-cbom-demo](https://github.com/simelbey-code/acdi-cbom-demo) — ACDI CBOM Light: single Node server.js external TLS assessment demo
- [TheNightCoder21/PNB_CySec_PQC.Auditor](https://github.com/TheNightCoder21/PNB_CySec_PQC.Auditor) — QuantumShield: FastAPI attack-surface recon + port/TLS/API scanners emitting CycloneDX 1.5 CBOM
- [Zeek PQC extension (zeek-pqc)](https://packages.zeek.org/) — Benchmarked by Olewinski et al.: 3/3 handshake scenarios. Source: Olewinski et al. ARES 2026 supplement matrix


## Competing and related studies (CBOM-specific)

Details, exact numbers and positioning bullets are in `papers/notes_*.md`. "Read" means we have the full text.

| Study | Venue / date | What it does | Tools evaluated | Cites BF-CBOM? | Relation to our RQs |
|---|---|---|---|---|---|
| Olewinski, Sandler, Ebinger: *An Empirical Analysis of Open-Source Tools for Cryptographic Asset Discovery for PQC Readiness Assessment*, doi:10.1007/978-3-032-35586-7_19 (read) | ARES 2026 Workshops, LNCS 16902, pp. 342–360 | 49-tool capability matrix (27 commercial / 20 OSS / 2 unlicensed); small ground-truth benchmark (30 assets); OS and network scenarios | CryptoScan, CBOMkit-hyperion, theia, cdxgen, CBOM-Lens, cbom-action, cbomkit-action (source); CipherIQ (OS); zeek-pqc (network). Counts only | No (BF-CBOM listed in their supplement) | RQ1 partial overlap (few tools, toy benchmark, language-mismatched). RQ1.5 anecdotal ("valid but not mergeable"). Independent support for the orchestration thesis |
| Nocera, Scanniello: *CBOM Generation and Consumption: A Mining Study from GitHub*, doi:10.1007/978-3-032-36587-3_33 (read) | SEAA 2026, LNCS 16864, pp. 482–499 | README-based mining of 162 → 64 CBOM tool repos; popularity, dependents, timeline. Artifact: doi:10.6084/m9.figshare.32124022 | none run | Yes ([3]) | RQ1 landscape overlap on supply side (claims, not outputs). Their "Dec 2019 first tool" is cdxgen's repo date; CBOM came in 2024-04 |
| Näther, Hirsch: *Hidden Ciphers and Where to Find Them* (Crypsy, Cryben), arXiv 2608.04857 (read) | preprint, submitted to ICICS 2026, 2026-08 | New generator plus synthetic benchmark (197 occurrences) | CBOMkit (sonar-cryptography v1.6.1), Go only | No | New generator; Cryben is a possible external sanity anchor for RQ3 (licence: ask the authors) |
| Moffie et al.: *Cryptoscope*, arXiv 2503.19531 (read) | preprint, 2025-03 | IBM Java CBOM generator; R 92% / P 97% on 97 assets in 7 projects | misuse-detector comparison only (CamBench) | No | Another closed generator. Criticises CryptobomForge |
| Hess (IBM Zurich): *The Anatomy of CBOMs* (read) | MAgiCS workshop slides, 2026-05-10 | Spec practice; static-analysis limits; dedup (nginx 8,104 → 314 assets) | IBM tools only | n/a | Motivation for RQ2 (naming ambiguity, dedup keys) |
| Foroncelli et al.: *Towards CBOM Compliance*, doi:10.1007/978-3-032-19567-8_10 (read) | SSR 2025, LNCS 16466, pp. 202–221 | Policy/compliance engine on CBOMkit CBOMs (NIST SP 800-131A) | CBOMkit only | No | Consumer; free-form names break policies (RQ2). Its claim that CBOMkit is "the only source-code analyser" is wrong |
| Shukla, Lodha: *FuzzyExtract*, doi:10.1145/3779208.3804889 (read) | AsiaCCS 2026 poster | Java-bytecode extraction; CryptoAPI-Bench counts | none | No | Closed generator; CBOM format not stated |
| Kundu, Kompella: *Quantum-Resistant Security: PQC Readiness and Research Challenges*, doi:10.1109/DAC63849.2025.11132566 (read) | DAC 2025 (invited) | Cisco Quartz: dependency graphs plus observability; claims CBOM generation | none | No | Public code (cisco-open/Quartz) emits no CBOM and wraps Wind River crypto-detector |
| Yamamuro et al.: SoK on discovery and inventory tools, doi:10.5220/0014227100004061 | ICISSP 2026 | Conceptual taxonomy of information sources and methods | none | ? | Taxonomy reference for RQ1 |
| Hirsch & Raab: SATAM, arXiv 2603.22442; Hirsch et al.: LLM package classifier, arXiv 2603.07204 | 2026 | Architecture-derived CBOMs; LLM ensemble (5 LLMs ≈ 1.6 independent) | — | No | The LLM correlation result matters for RQ3 (correlated "voters") |
| CBOM Profile v1.0-RC (cbomprofile.org, Applied Quantum) | 2026-09, comments until 2026-10-31 | Conformance profile for CBOMs; canonical asset identity planned for Q4 2026 | — | — | Measuring instrument for RQ1.5; watch for RQ2 |
| sbom-tools (CBOM validate/score/diff), cyclonedx-cli, CycloneDX 1.7 Cryptography Registry, OWASP Authoritative Guide to CBOM, ECMA-424 | tools/spec | — | — | — | Instruments for RQ1.5/RQ2. Registry coverage has never been measured |
| Own precursor: CBOMbench (Bern BSc thesis / SEG student project t0006) | — | CBOMkit, cdxgen, DeepSeek on GitHub repos | — | — | Cite as precursor |
| Also: Schmitt/Wiesmaier GI Sicherheit 2024; Campbell Preprints 2026; TNO 2025 market survey (anonymised); NIST SP 1800-38B/C; IETF draft-dev-xipher-cbom-extension-00 (Comcast) | | | | | background |

## Novelty assessment per RQ (2026-10-06)

Sources: `papers/notes_novelty_cbom.md` (CBOM-specific) and `papers/notes_novelty_analogs.md` (SBOM and static-analysis analogues). The search was web-based, so **recheck Semantic Scholar and DBLP manually before submission.**

| RQ | CBOM prior work | SBOM / static-analysis analogues | Novelty risk | Claimable gap |
|---|---|---|---|---|
| **RQ1** landscape and what tools actually emit | Nocera (claims, no runs); Olewinski (7 OSS tools on a toy benchmark); Yamamuro SoK (taxonomy) | Run-all-generators studies are established for SBOM: Wang TOSEM 2026 (6 tools, 3,287 repos), Mind the Gap (arXiv 2609.19920), Balliu 2023, Cofano 2024, Mirakhorli 2024 (84 tools). None includes a CBOM producer | **method low–medium, subject high** | First output-centric CBOM landscape; adopt Wang's design (repeat runs, tool-version stability) |
| **RQ1.5** conformance to CycloneDX | Only anecdotes (Olewinski: 3 outputs "valid but not mergeable"; single-tool issues); instruments: CBOM Profile, sbom-tools | Wang TOSEM 2026: field presence only. Its CycloneDX "mandatory" set is just bomFormat/specVersion, which every tool meets 100%: an "illusion of compliance". Mind the Gap: valid-but-empty SBOMs without error; divergences classified as representation / design choice / tool error. **Neither does L1 schema validity, L2 semantics or L4 evidence** | **low (substantially novel)** | Ladder levels L1, L2 and L4 are untouched for any BOM type; L3 is done only for package fields. Use Wang's "100% mandatory" result as the hook. Count "valid but empty CBOM" as its own row |
| **RQ2** normalisation and matchability | Problem named (IBM slides, Foroncelli); no study | Wang (name/version/PURL matching, threshold τ plus Spearman robustness); Beyond Compliance (arXiv 2607.04614: two-level key, versionless PURL then PURL plus version; Go agreement 76.8% → 32.6%); sbom-unifier (arXiv 2608.30708: PURL-keyed merge, field-priority fill) | **medium–high novelty** | No canonical ID exists for crypto assets. Transfer: exact canonical family key plus weighted parameter/location scores; two-level agreement (family vs family plus parameters); threshold ablation; log how often each normalisation rule fires; residual-disagreement taxonomy |
| **RQ3** scoring without ground truth | Nothing for CBOM generators | **Andersson & Monperrus, arXiv 2604.24085** (4 Go misuse detectors, 328 repos; exact file/line/rule matching; consensus as precision proxy only descriptive, 5 samples × 3 rules, no validation; 1.3% shared by all four tools). MASC/TOPS (Ami et al., doi:10.1109/SP46214.2022.9833582 / 10.1145/3796221): mutation-based, recall only; 13/14 detectors fail on crypto parameters. Lenarduzzi 2023 (0.4% agreement, two-rater precision, no link between agreement and precision). µSE (inherited flaws, i.e. correlated tools). Dawid–Skene / Platanios (unsupervised accuracy) | **low** (the scoring result itself is the contribution; no CBOM work exists) | Andersson stops at descriptive overlap without a usable ranking. Credibility guard (not a novelty requirement): count agreement per engine, not per tool name, and show on a small adjudicated sample (50–100 disputed/unique findings) that the score preserves the tool ranking. Report per information type (family / parameters / location) |

Detailed notes: `papers/notes_analogs_rq3.md` (with a 15-bullet RQ3 design) and `papers/notes_analogs_rq1-2.md` (ladder mapping, matching design). *Mapping Quantum Threats* (arXiv 2509.24623) is **not** CBOM prior work: it inventories protocols, runs no tools and has no BOM content.

## Fleet extension: selection criteria and RQ plan (draft)

**Inclusion criteria for running a tool** (PI decision 2026-10-06). Conformance and evidence locations are *not* criteria: they are measured.
1. **Serious / noteworthy, with no one-off or vibe-coded repos.** At least one of:
   - (a) a peer-reviewed paper or thesis;
   - (b) backing by an organisation, with versioned releases;
   - (c) used or evaluated in an independent study (e.g. Olewinski ARES 2026);
   - (d) demonstrable adoption (dependents, downloads, sustained multi-contributor activity).
2. Accessible: free, runs offline, does static source analysis; research access may be obtained by request.
3. Claims to produce a CBOM (any format).
4. Covers Java and/or Python.
5. Independent engine; wrappers are merged with their engine.
6. Headless/Docker.
7. No EULA clause forbidding publication of benchmarks (or written permission).

**Fleet under these criteria:**

| Tool | Qualifies via | Engine | Java/Python | Status |
|---|---|---|---|---|
| CBOMkit / sonar-cryptography | org (IBM→PQCA), adoption | SonarQube semantic plugin | both | in fleet |
| cdxgen | org (CycloneDX/OWASP), adoption | atom/Joern + keystore parsing | Java (+ JS) | in fleet |
| CryptobomForge | org (Santander), Black Hat EU 2023 talk | CodeQL → IBM draft CBOM | CodeQL languages | in fleet |
| LLM worker(s) | own paper | LLM | both | in fleet |
| **CSNP CryptoScan** | org (CSNP/QRAMM), releases, evaluated by Olewinski et al. | regex | both (shallow) | **add** |
| **SCANOSS crypto-finder** | company (SCANOSS), frequent releases | OpenGrep + call-graph reachability | both (rules need API key) | **add if SCANOSS grants a research key** |
| **Mondoo xgrep** | company (Mondoo) | tree-sitter + taint | both | **add only with written permission** (benchmark-disclosure clause) |
| qScan (quantakrypto) | startup, OpenSSF badge, 11 releases, 14★ | patterns | both | borderline: decide on adoption evidence |
| Cryptoscope (IBM Research) | paper | own dataflow | Java | not public: ask the authors |
| Crypsy | paper (preprint) | rules/AST | Go/Ruby only | out (language), unless a Go slice is added |

Dropped under the seriousness bar: sachhg/pqc-scan, pqcheck, qrp-mcp and the rest of the tier-D long tail. **Consequence:** the fleet has about 5–7 independent engines. That is enough for n-way matching, and it keeps the overlap pilot relevant.

**RQ plan:**
- **RQ1:** What is the landscape, and what do tools actually emit? Survey (this repo) plus real runs.
- **RQ1.5:** Do tools that claim CycloneDX conform? Conformance ladder:
  - L0: parseable JSON, `bomFormat`/`specVersion` present.
  - L1: valid against the 1.6/1.7 JSON schema.
  - L2: semantically meaningful: `cryptographic-asset` carries `cryptoProperties`, `assetType` matches content.
  - L3: comparable: canonical names (1.7 registry/OID), parameters in fields.
  - L4: traceable: `evidence.occurrences` with file/line.
- **RQ2:** How do we normalise, and which information becomes matchable? Ladder: algorithm family → variant/parameters → location; plus dedup keys and IBM-draft mapping.
- **RQ3:** Can tools be scored without external ground truth? Agreement and latent-truth models on engine-deduplicated tools, plus adjudication of a stratified sample, validated once against small external ground truths.

## Method (for the paper's methods section)

Searched 2026-10-06 in four steps.

**1. Discovery, four channels in parallel.**
- GitHub/GitLab/Codeberg plus registries.
- Academic: arXiv API, Crossref, web search. Semantic Scholar was rate-limited and DBLP blocked us.
- Ecosystem lists: CycloneDX Tool Center, PQCA, NIST NCCoE, awesome-lists.
- About 40 commercial vendors.

**2. Per-tool deep dive.** README, docs, releases and registry metadata. For the long tail, the source was grepped for `cryptographic-asset`, `cryptoProperties` and `specVersion`. GitHub metadata came via the ungh.cc proxy.

**3. Gap checks.**
- Santander PQCTools CADI index (found SCANOSS).
- CycloneDX tool-center `tools.json`.
- TNO report.
- SBOM/SCA tools, vendor OSS orgs, EU/BSI projects.

**4. Recall checks against two independent 2026 datasets.**
- Nocera/Scanniello's 64 tools: 40 already covered. The 24 missing were triaged, yielding 1 new long-tail producer.
- Olewinski et al.'s 49-tool matrix: only commercial or network tools were new.

**Negative results.** No CBOM export found as of 2026-10 for:
- SBOM/SCA tools: Syft, Grype, Trivy, ORT, OWASP dep-scan, Microsoft sbom-tool.
- Commercial SAST/SCA: Snyk, Black Duck, Checkmarx, Veracode, Semgrep, GitHub Advanced Security. CodeQL ships only crypto queries and SARIF.
- Fraunhofer Codyze/CPG.
- GitLab has an open epic only.

## Open items / caveats

- **Spec-version claims come mostly from READMEs and code, not from runs.** Run each candidate worker once and push the output through the RQ1.5 ladder before quoting numbers.
- **Tier E cells are largely marketing-level evidence.** The same goes for the matrix entries copied from Olewinski et al.
- **Crypsy's code and Cryben are review-stage material without a licence.** Ask Näther and Hirsch before reuse.
- **Full texts still worth reading:** the SBOM analogues and Andersson 2604.24085 (DOIs in the chat and in `papers/notes_novelty_analogs.md`).
- **PQCScanner (CCSC Central Plains 2026) was not located.** XIPHER exists only as a talk plus an IETF draft.
