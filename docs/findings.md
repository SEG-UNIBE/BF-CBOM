# Findings so far

Results gathered while building BF-CBOM 2.0. Research-question numbers in a dated section follow the numbering of that date: since
2026-10-30 there are five (volume, conformity, normalisation, agreement, detection as RQ1-RQ5), and the bar exam is
a preparatory step; before, it was RQ1 and the others RQ2-RQ6. They come from the development
set (the pilot corpus `experiments/corpus/pilot-2026-10-08.yaml`, the micro projects `experiments/corpus/micro`) and from runs that will be
rerun before the paper's experiments; the date says which code produced a number. The micro projects are tiny and
AI-written: their numbers are an inclusion check, not a ranking.
Spec names in dated sections are the names at the time: on 2026-10-22 e0-micro became bar-micro, e0-stability
stability-pilot, e1-pilot-corpus corpus-pilot-small, e1-pilot-60 corpus-pilot-medium, e1-icpc-corpus corpus-icpc and
e4-mutation-smoke mutation-smoke.

## Pilot of 2026-10-08
A colleague's one-day pilot: 15 generators on 16 pinned projects plus the micro projects, an inclusion bar and
location-aware cross-matching with hand-checked samples. Reports and the landscape survey: `docs/pilot-2026-10-08/`.
- **Agreement tracks precision, but careful tools are right alone.** Precision by number of agreeing tools k = 1 / 2 /
  3+: 48 / 88 / 100 % on Java and Python, 39 / 60 / 96 % on Go, C and JS. CBOMkit, Q-DARPAN and pqcheck are about
  100 % precise even alone, so the "right alone" penalty is real; Dawid-Skene weighting and the validation of
  exclusive findings (E4) are essential. Only about 18 % of the clusters had two or more tools.
- **Matcher rules** (the call-site matcher, retired 2026-10-18; its line window lives on in the exact matcher): same
  file, |Δline| ≤ 2, family sets intersect, nearest-neighbour linking; an unknown family never matches (as a wildcard
  it inflated weak tools). Opengrep/semgrep rule tools shared the voter group `grep-rules` (split 2026-10-20: their rule sets share no rule, `analysis/README.md` "Voter groups").
- **Formats:** CryptobomForge emits IBM's draft `1.4-cbom-1.0`, not CycloneDX (the ICPC paper's claim needs a
  correction). cdxgen reports no file or line for crypto findings outside JS/TS.
- **Shared blind spot of all tools:** algorithm names passed through variables or constants (the third insertion style
  of the mutation harness).
- **CBOMkit:** module-indexing bug (needs a build file and `src/`; Kafka: 7 of 2,900 files scanned), invented key
  sizes, Python covers pyca only; large repos need about 45 minutes and a 16 GB heap.

## Micro projects (e0-micro)

### 2026-10-15 (superseded)
Strict recall out of 11 (Java / Python / Go / JS / C): CBOMkit 11 / 5 / 0 / 0 / 0; DeepSeek rewrite 11 / 8 / 11 / 11 /
11; Q-DARPAN 10 / – / 9 / – / 7; CipherRadar 11 / 8 / 9 / 9 / 4; pqcheck – / 8 / 8 / – / –; securin-xbom 6 / 7 / 4 /
4 / 2; xgrep 11 / 8 / 10 / 9 / –; cbomkit-jar 11 / 5 / 8 / – / –; CryptoScan 0 / 0 / 5 / 6 / 0; cdxgen and
CryptobomForge 0 everywhere. Precision 100 % except CryptoScan (7/11 Go) and CipherRadar (12/13 Go, 3/5 C).
The CryptoScan, CryptobomForge and CBOMkit-server (Go) numbers were **our setup errors**, not the tools: CryptoScan
without `--include-quantum-safe` and on a `demo/` folder it skips, CryptobomForge on CodeQL's security suite instead
of the crypto queries, a CBOMkit image without Go (all fixed 2026-10-17). An example for the paper of why
every tool needs a check of its invocation before a zero is accepted.

### 2026-10-17, after the hand-over fixes
Lenient call-site recall out of 11; all runs ok.

| Tool | Java | Python | Go | JS | C |
|---|---|---|---|---|---|
| CipherSift (benchmark-informed) | 11 | 11 | 11 | – | – |
| DeepSeek | 11 | 11 | 11 | 11 | 9 |
| xgrep | 11 | 10 | 10 | 9 | – |
| CBOMkit, cbomkit-jar | 11 | 5 | 8 | – | – |
| CipherRadar | 11 | 8 | 9 | 9 | – |
| Q-DARPAN | 11 | – | 9 | – | 7 |
| CryptoScan | 9 | 10 | 10 | 10 | 1 |
| securin-xbom | 9 | 8 | – | – | – |
| pqcheck | – | 8 | 8 | – | – |
| cdxgen | 0 | 0 | 0 | 4 | – |
| CryptobomForge | – | 0 | – | – | 0 |
| SCANOSS (2026-10-18, open rules) | – | – | 10 | 3 | 7 |

- CryptoScan strict: 7 / 6 / 6 / 6 / 0. SCANOSS precision 100 %, matching the coworker's Go 10/11 and the pilot's
  grades (C pass, JS fail).
- This reproduces the colleague's micro table (their pilot scorer matches our lenient column) except CBOMkit Python
  (5 vs 6), CryptoScan Go (10 vs 9) and CryptobomForge (by design: no window shrinking).
- **cdxgen** by design: algorithms with locations only for JS (corrected 2026-10-20: and Go, where 12.8.5 writes the
  location into the property `cdx:golem:crypto:sourceLocation`, which our cdxgen spec ignored until then; on the pilot
  export 29 Go records of golang-jwt gained file and line). With `-t` it reports 5 / 6 algorithms on micro-python /
  micro-go without locations, which count at repository level. Java gives nothing with any invocation: atom 2.5.6 tags
  Java crypto APIs `crypto`/`crypto-algorithm` but has no `crypto-generate` sinks, and Java's reachable-flow search
  uses only generic sources and sinks (framework input, CLI, network, ...), so micro-java has 0 reachable flows. On
  micro-python it finds 4 crypto-to-crypto flows (`ec.SECP256R1()` -> `ec.generate_private_key(...)`, ...).

### 2026-10-18, scoring on the normalised records
The `micro` scoring moved from the call-site extraction onto the normalised records (`score_records`) and added the
repository level, reported next to the call-site level. Every call-site cell stayed equal except two, both explained:
- **CryptobomForge** 0 -> 8/11 on Python and C (lenient; strict 8 / 7): its IBM-draft windows are read now. One
  over-credit: on micro-python an AES report whose window is centred on the 3DES line also covers the AES-GCM line.
  The windows are CodeQL's `contextRegion` (the finding ±2 lines), so the exact line could be read as the window's
  centre (Python 7/11); open.
- **CryptoScan** strict 7 / 6 / 6 / 6 / 0 -> 9 / 10 / 10 / 9 / 1 (= lenient): it names components `ECC` and `SHA-2`,
  which the old regex family table left unknown and the shared vocabulary maps. JS lenient 10 -> 9: on the PBKDF2 line
  it reports SHA-2 (the digest), which the old table had counted as an unknown family.
- **Repository level** (out of 11): most tools reach 10-11 where they run. Lower: cdxgen 0 / 6 / 7 / 4 (Java / Python
  / Go / JS; no locations, so its only non-zero micro number), securin-xbom Java 7, Q-DARPAN C 7, CryptobomForge C 6,
  CryptoScan C 4. Repository level can be lower than lenient: an unknown family counts at a call site only, and call
  sites accept a family the asset's name contains (SHA-2 for HMAC-SHA512).
- The exact matcher (2026-10-18) left all 65 (project, tool) cells unchanged.

### 2026-10-22, cdxgen on micro-go: a fixture without go.sum
e0-micro and e0-stability (VM, 2026-10-21) give cdxgen 5/11 on micro-go, one below the bar. Its CBOM is read
completely (six assets, one location each, all at their lines); the rest was checked in throwaway runs of cdxgen
12.8.5 on micro-go:
- **Our setup error: micro-go had no `go.sum`.** It imports `golang.org/x/crypto/pbkdf2`, so the module did not build
  (`missing go.sum entry`), cdxgen's `go list -deps` failed and its Go analyser (golem 2.5.1, type-checked through
  go/packages) could not import pbkdf2. With the `go.sum` (added 2026-10-22; `go build` and `go vet` pass) it reports
  `PBKDF2` at line 56: 6/11 strict and lenient with our scorer. golem's `go list` downloads the modules `go.sum` pins
  even with `--no-install-deps` (the workers have network; without network the import fails again).
- **The invocation is already cdxgen's best:** `--evidence` implies `--deep`; `--profile research` turns golem's
  data-flow on but adds no asset; the dedicated `cbom` command finds no Go asset at all (it never runs golem).
- **By design, not ours:** golem's catalog has no entry for `ecdsa.GenerateKey` / `SignASN1`, `cipher.NewGCM` /
  `NewCBCEncrypter`, `des.NewTripleDESCipher` and `rsa.EncryptOAEP`, and the CBOM keeps one location per asset
  (golem's raw output also has SHA-256 at the OAEP and PBKDF2 lines and a CSPRNG, which the CBOM leaves out or lists
  without a location).

### 2026-10-22, cdxgen in Go: the worker could not create Go's module cache
The VM rerun of e0-micro (2026-10-22, with the `go.sum`) gave cdxgen 0/11 on micro-go, not 6. Its CBOM holds only the
`golang.org/x/crypto` library read from `go.mod`. The Go exports agree: cdxgen found Go assets only in golang-jwt
(29 in every run), the one Go repository without a third-party dependency; age, smallstep/crypto, vault and micro-go
gave none in e0-stability, e1-pilot-corpus and e0-micro.
- **Our setup error: the container could not write Go's module cache.** The cdxgen worker runs as root with
  `cap_drop: [ALL]`, so root has no `DAC_OVERRIDE`, and the image's GOPATH `/opt/app-root/go` lies under a
  non-writable `/opt`. `go list -deps` failed (`go: could not create module cache: mkdir /opt/app-root: permission
  denied`) and golem type-checked nothing in a module with a dependency. The throwaway runs above had all
  capabilities, which is why they gave 6/11. Reproduced with the 1.0.3 image: compose's capabilities 0 crypto assets
  on micro-go, all capabilities 8, all capabilities without network 6 (the stdlib ones); Python and Java give the
  same output either way.
- **Fixed:** every job gets its own GOPATH in its work directory (`workers/cdxgen/main.py:go_env`, with
  `-modcacherw` so the cleanup can delete it, in the run notes as `cdxgen env:`); with compose's capabilities
  micro-go gives 8 crypto assets again, 6/11 at call sites with our scorer (the old worker: 0). Every cdxgen Go run before 2026-10-22 is rerun. Go modules are now
  downloaded per run, so large Go repos (vault) take longer.

### 2026-10-22, micro ground truth v2: generated from the snippet catalog, with decoys
On e0-micro of 2026-10-22, 18 of the 226 misses had a record of the item's family within two lines: the hand-written
ground truth caused them, not the tools.
- **Flaws:**
  - Neighbouring items: the AES key and the AES-GCM encryption were one use split one line apart, and CBOMkit,
    CipherRadar, CryptobomForge, pqcheck, securin-xbom and SCANOSS reported on the neighbour's line.
  - The naming line didn't count: Python's OAEP line 32 was only `related`.
  - The hit lines followed a different rule per language (Python one line, Java two, C five).
  - `des-cbc` was DES in Java and 3DES elsewhere.
  - The ground truth used a stale scheme (its families table no longer existed) next to the E4 catalog that had grown
    out of it.
- **v2** (`mutation/micro.py`, `uv run python -m mutation.micro`):
  - Java, Python and Go only; the JavaScript and C projects went.
  - The 14 algorithm snippets of each catalog (the TLS context left out), each in a function of its own with a
    neutral name.
  - 4 decoys per project: a comment and a log message naming algorithms, a CRC32 checksum and a Base64 encoding.
- **Rules,** decided before any tool output on the new projects was scored, and stated in the ground truth's header:
  - An item's lines are its function's body.
  - Strict: a record of its group there; for a keygen item a key record too. Lenient: also a record of unknown family.
  - The digest a call names (HMAC's SHA-2) is accepted: neither a hit nor a false site.
  - Decoys: any crypto record is a false positive.
  - The bar: 7 of 14.
- **Throwaway runs of the 1.0.3 images on the generated projects** (not stored results):
  - **A generated-file header blinds scanners.** A first header line `Generated by ...; do not edit` made xgrep skip
    the whole file: 0 components, 14 without the header. Scanners skip generated code, so the files carry no header.
  - **Import lines.** CryptoScan also reports the imports, which counted as false sites (precision 0.82 on Python,
    0.72 on Go). An import of an item's API is crypto code, so the import lines are now accepted for the groups of
    the items using them. This rule was set after that run; it applies to every tool alike and comes before the VM
    scoring.
  - **Results:** CryptoScan 13 / 12 / 13 of 14 (Java / Python / Go; misses PBKDF2, Ed25519 and HMAC, HMAC), xgrep 14
    / 13 / 14 (misses SHA3-256), no decoy hit, precision 1.00 for both.
- **Scorer tests** on the pilot's CBOMs keep the frozen old ground truth (`tests/fixtures/pilot/ground_truth-2026-10-08.yaml`).
  There CBOMkit's micro-python goes from 5 to 6 of 11 with the keygen rule (its EC key record), the pilot's own 6/11.
- **Not comparable:** the micro numbers up to 2026-10-21 (`micro_2026-10-17` in `common/tools.yaml`) are on the old
  projects. e0-micro reruns.

**Decoys moved into the catalogs (2026-10-22, later the same day).** The four decoy kinds are now `decoys:` entries of `mutation/catalog/<language>.yaml`, the one source for the micro projects and for the mutation harness, which plants them next to the calls (`decoys: equal` in a spec's mutation block) as known negatives for RQ4's precision. Their bodies took placeholders for locals and lost the `return` statements (a decoy inserted before a host's first statement must not end the host); the Java and Go micro files are unchanged, the Python one and the ground truth were regenerated (ground truth 2026-10-22.2, catalogs 2026-10-22.1).

## Normaliser on e1-pilot-corpus (2026-10-16)
116 ok CBOMs of 11 tools, about 41k records, 2 s.
- Algorithm records with a tool-reported family: 87-100 % per tool (CryptobomForge 7 %: mostly `UNKNOWN`).
- **What the real outputs look like** (encoded in the adapter specs): occurrence evidence is often more specific than
  the component (CryptoScan `symbol`: `rsa-2048`, `KeySize = 2048`, `TLSv1.2`); names mix registry style, JCA, JOSE,
  SSH, API names, rule ids, lists and code (`SHA-" + this.strength`); cdxgen and pqcheck emit dependency inventories
  (skipped, not findings); locations come as `path:line`, `path#line`, temp-dir paths, IBM-draft windows, a `SrcFile`
  property.
- **Spec issues:** DeepSeek 429 `enum` (our own worker, fixed the same day), Q-DARPAN 294 `deprecated` (`curve` in
  1.7), securin-xbom 93 `enum` (`pk`, `prng`), CryptobomForge `dialect` (IBM draft).
- **Genuine tool conflicts, kept:** CipherRadar labels 298 SHA-1 findings SHA-2, RS256 as HMAC, TripleDES as DES.
- CBOMkit on micro-java agrees with the ground truth's `expected` field by field except the AES-GCM key size (128 vs
  256), the ECDSA curve and the PBKDF2 iterations (missing).
- **Our bugs found while normalising, both fixed:** `common/llm_cbom.py` passed the model's values through
  (`assetType: key`, paddings `PSS`/`OAEPWith...Padding`, upper-case modes, mode `KW`), so our own LLM workers wrote
  invalid CycloneDX; the assembly now maps every value onto the 1.6 enums and keeps the original in
  `bf-cbom:<field>`. CryptobomForge's locations sit only in `cryptoProperties.detectionContext`, which the old
  call-site scorer did not read (micro 0/11).

## Normalisation ladder (e1-pilot-corpus)
Per repository, the algorithms a tool reports that another voter group confirms. The numbers up to 2026-10-18
afternoon come from the first ladder, which changed both the key function and the unit per level (the share at L3
was over parsed algorithms, at L0 over raw names) and was not monotone; the fixed-unit ladder of the evening is
below.
- **2026-10-16, first ladder:** pooled 48 % at L0, 53 % L1, 60 % L2, 75 % L3, 75 % L4: the tool-specific part (L4) adds
  nothing overall; the gain comes from standard spellings and parsing composite names. Per tool L0 -> L4: DeepSeek 28
  -> 64 %, cbomkit-jar 57 -> 93 %, CipherRadar 53 -> 85 %, xgrep 67 -> 92 %, CryptoScan 37 -> 53 %, securin-xbom 87 ->
  96 %. Shares are not strictly monotone: at a given level one tool's value may already be canonical while another's
  is still a raw name.
- **2026-10-18, after moving tool idioms into the tool specs (WP2):** only those tools move, and only below L4:
  CipherRadar L2 73 -> 69 %, L3 87 -> 85 %; pqcheck L2 89 -> 86 %, L3 90 -> 88 %; DeepSeek L2 43 -> 42 % (`rand`
  unmapped); pooled L3 75 -> 74 %, L4 75 % unchanged. L4lit equals L4 for every tool: the interpretive entries set
  protocols and groups, never algorithms.
- **Parameters, pooled (L2 / L3 / L4):** key size 29 / 50 / 63 % of 295 pairs, mode 38 / 77 / 78 %, padding 26 / 45 /
  49 %, curve 30 / 67 / 75 %, digest 0 / 15 / 33 % (digests sit in names, never in standard fields).
- **Held-out check, vocabulary 2026-10-18.1** (names and occurrence symbols left unmapped or partial, by count): pilot
  corpus 0.2 % (67 of 35,506), ICPC 2.0 % (976 of 47,771); on ICPC securin-xbom 33 % (214 of 640), DeepSeek 16 % (493
  of 3,026), CryptobomForge 7 %, the rest ≤ 1 %. The frozen vocabulary is not edited for these.

## RQ1 on e1-pilot-corpus (2026-10-18)
- **Whole-document validity:** every CBOM of cbomkit-jar, cdxgen, CipherRadar, CryptoScan, pqcheck, Q-DARPAN and xgrep
  validates. CBOMkit server 3 of 6 (`uniqueItems` in `dependencies`), DeepSeek 2 of 15 (the assembly before
  2026-10-16), securin-xbom 2 of 8 (`relatedCryptoMaterialProperties.type`, `primitive` enums), CryptobomForge 4 of 6
  in IBM's draft (no schema); the two 1.6 ones were valid, but they were the worker's own empty placeholder for a
  CodeQL run without results, not the tool's output (removed 2026-10-20; cryptobom-forge writes its own empty draft
  document, and those runs are redone).
- **Meaningful** (standard fields / any source): primitives mostly in standard fields except cdxgen (0 / 70 %) and
  securin-xbom (42 / 96 %); key sizes largely outside the standard for pqcheck (12 / 44 %) and cdxgen (0 / 31 %; that row counted any `parameterSetIdentifier`, digest lengths included, and went 2026-10-22); OIDs
  only from CBOMkit, Q-DARPAN and xgrep.
- **Traceability** spot check with `--fetch` on uCrop: CryptoScan's 20 records all exist and are in range, none strong
  (regex hits on `nb_iterations=5` and `I##cn`, reported as PBKDF and certificate findings); CipherSift's and
  securin-xbom's single records strong.

## Matchers on e1-pilot-corpus (2026-10-18)
- **Exact matcher** at L3, repository level: 418 algorithms, 56 % reported by one voter group, 17 % two, 8 % three,
  19 % four or more (identical to the earlier algorithms-based count in every cell, per language too); 10,616 call-site
  groups, of which 143 algorithms have two voter groups at one site.
- **RaQuN vs exact** (16 repos, 18,237 records both grouped): exact 418 groups, RaQuN 695 (k = 1..7, 9: 391 / 137 / 56 /
  46 / 29 / 28 / 6 / 2); co-membership pair Jaccard 54 % pooled (0.43-0.84 per repo); 88 % of RaQuN's groups lie inside
  one exact group, 65 % of the exact groups inside one RaQuN group. RaQuN splits an algorithm into its parameter sets
  and keeps at most one asset per tool; the exact matcher pools them. These RaQuN numbers come from PyQuN before
  v1.0.0, whose greedy matching took the candidates unsorted, and from elements that still included `undefined`
  names and our `group`; they are not comparable with later reports (2026-10-21).

## Scores on e1-pilot-corpus (2026-10-18)
Algorithm dimension at L3, confirmation rate with 95 % CI over repos (items): securin-xbom 96 % [81, 100] (23),
cbomkit-jar 93 % [88, 100] (68), xgrep 90 % [83, 96] (134), pqcheck 88 %, Q-DARPAN 87 %, CipherRadar 85 % (124), CBOMkit
server 67 % [23, 100] (15), DeepSeek 59 % [46, 69] (198), CryptoScan 52 % [43, 60] (176), CryptobomForge 43 % (7);
cdxgen had no algorithm records in those CBOMs (runs before `-t`). Dawid-Skene agrees on the tails (Q-DARPAN support
83 %, securin-xbom 81 %; CryptoScan 28 %, DeepSeek 31 %) but reorders the middle. The careful-tools-alone effect is
visible (CBOMkit server: few items, wide CI); E4 decides what exclusivity means.
With the fixed-unit matcher of the evening (scoring items are the clusters at L3, `score.item_id`) the ranking is
the same except that xgrep (91 %) and pqcheck (90 %) swap places with nothing, and every rate moves by a few points:
securin-xbom 96, cbomkit-jar 93, xgrep 91, pqcheck 90, CipherRadar 88, Q-DARPAN 88, CBOMkit server 71, DeepSeek 64,
CryptoScan 52, CryptobomForge 50 %.

## Ladder and matching on the fixed unit (2026-10-18, evening)
The ladder now keeps the unit fixed (an algorithm = its L4 identity) and varies only the matching relation, which is
cumulative (`analysis/README.md` "How much agreement does normalisation create?"). Every tool's share is
non-decreasing by construction. Pilot corpus, confirmed share of a tool's algorithms at L0 / L1 / L2 / L3 / L4lit /
L4 (number of algorithms): pooled 56 / 60 / 67 / 73 / 77 / 77 % (937); CBOMkit server 27 / 60 / 60 / 67 / 67 / 67
(15), cbomkit-jar 68 / 74 / 84 / 93 / 94 / 94 (68), CipherRadar 70 / 74 / 81 / 87 / 89 / 89 (115), CryptoScan 32 /
33 / 43 / 50 / 60 / 60 (225), DeepSeek 46 / 48 / 56 / 60 / 64 / 64 (198), pqcheck 71 / 81 / 87 / 88 / 90 / 90 (90),
Q-DARPAN 79 / 81 / 81 / 87 / 89 / 89 (62), securin-xbom 87 / 87 / 91 / 96 / 96 / 96 (23), xgrep 74 / 77 / 81 / 90 /
92 / 92 (134), CryptobomForge 29 / 43 / 43 / 43 / 43 / 43 (7). Reading: the L0 column is the share of a tool's
algorithms for which another tool uses a verbatim identical spelling; the standard vocabulary and parsing add 17
points pooled, the tool specs 4 (CryptoScan 10: its occurrence symbols), the interpretive entries 0. Parameters
pooled L2 / L3 / L4: key size 28 / 35 / 74 % of 295 pairs, mode 45 / 62 / 78 % (60), padding 30 / 48 / 67 % (67),
curve 25 / 54 / 86 % (196), digest 0 / 4 / 38 % (217). Held-out ICPC corpus: pooled 60 / 65 / 74 / 79 / 81 / 81 %
(845 algorithms); CipherSift 39 -> 86 % (the standard vocabulary alone adds 38 points: it writes registry names),
CryptoScan 33 -> 70, securin-xbom 49 -> 59, DeepSeek 58 -> 72, xgrep 87 -> 99, CBOMkit 84 -> 93.

**Matcher x level (exact matcher; RaQuN needed the PyQuN package, then not installed on the laptop).** Pilot corpus, 433
algorithms per repository: the share reported by at least two voter groups rises from 38 % at L0 to 50 % at L4
(k >= 4 from 15 to 26 %); of the groups with two voters reporting a key size, 95 % agree at L2 (42 groups) and 72 % at
L4 (76), curve 61 -> 57 % (18 -> 51), digest agreement exists from L3 (2 groups) and is 67 % at L4 (39); of the
groups with two located voters, 63 % share a call site at L0 (210 groups), 50 % at L4 (217): as clusters grow, more
voter pairs are located but not at the same place. ICPC corpus, 289 algorithms: k >= 2 from 37 % to 47 %, k >= 4 from
20 % to 37 %; key-size agreement 79 % (19) at L2 to 36 % (39) at L4, location 78 % (144) to 73 % (135). The exact vs
RaQuN comparison per level is produced when RaQuN is available (VM).

## Export review 2026-10-19
The first VM exports (`e0-stability`, `e1-pilot-corpus`, `e1-icpc-corpus`, runs of 2026-10-18/19) were checked for
plausibility before anyone cites them. The numbers below come from those exports and from the re-report with the
code of 2026-10-19 (per-key clusters, `resolution`, macro rates); the runs themselves are to be redone on the VM,
so they are a snapshot, not results.
- **Runs that were not what they looked like.** DeepSeek's account ran out of credit on 2026-10-18 ~21:46 UTC: all 15
  pilot runs and 7 E0 runs failed with 402, and two E0 runs on MinerU were stored `ok` with 75 of 100 and 139 of 144
  requests failed, so E0's DeepSeek stability (Jaccard mean 0.86, min 0.25, 44 unstable algorithms) measured the
  outage; each failed run fired 300 requests because API errors took the split-and-retry path. cdxgen was OOM-killed
  at its 8 GiB limit on Chat2DB, bisq, bisq2 and kafka with the runs stored `ok` (peaks at the limit). CBOMkit's two
  pilot errors (keycloak, ansible) were "Git clone from http://gitserver/... failed" in under a minute while
  cbomkit-jar scanned both: the git server's 256 MiB limit against a full JGit clone. All three are fixed in the
  setup.
- **CBOMkit's two packagings agree exactly** wherever both ran: 130 of 130 (repo, tool) cells on ICPC and all 9 E0
  repos give identical algorithm sets; the server variant takes ~4.9 GB resident and is slower.
- **Tool crashes to report, not fix:** CryptoScan 1.4.0 panics in `patterns.MatchWithContext` on allure2, bisq,
  docling and keycloak (4 of 39 repos; deterministic: E0's three repetitions on 15 repos were identical). cdxgen
  12.8.5 crashes in `parsePyLockData` on docling's lock file. CipherRadar's 30-minute timeout on uCrop is a hang, not
  size (a tiny repo, 359 MB resident). cdxgen timed out on keycloak and took 22 minutes on Sa-Token.
- **DeepSeek saw only part of the big repos:** the 100-chunk cap cut bisq2 to 100 of 312 chunks, bisq 100 of 198,
  parse-server 100 of 182, smallstep 100 of 113; the whole ICPC run cost 0.15 USD (E0 1.20 USD), but repetitions 2/3
  and the ICPC rerun were ~98 % cache hits, so a fresh run costs more. The model answered as `deepseek-flash`.
- **Resources.** Peak VM RAM 21.0 GiB (ICPC, budget 64, one wave), 17.0 (E0), 20.8 (pilot, budget 52) of 60.5 GiB
  with 64 CPUs; measured per-tool peaks (max over all runs, GiB): cdxgen 8.0 (the limit), cbomkit 7.6, cbomkit-jar
  6.8, cryptobomforge 2.7, xgrep 2.6, cipherradar 1.5, pqcheck 1.4, scanoss 1.3, ciphersift 1.3, securin-xbom 1.2,
  qdarpan 0.9, deepseek 0.7, cryptoscan 0.7; medians far below. The wall time is cdxgen's serial queue (80 min summed
  on ICPC = the whole run) and DeepSeek's in E0 (71 min).
- **Item inflation, now removed.** The 2026-10-18 clusters merged through any shared key: on ICPC 48 of 312 repo
  groups were duplicates (the same member set under several algorithms), authlib had one cluster spanning EC, ECDH,
  ECDSA, Ed25519, X25519 and X448 and one spanning HMAC and five SHA variants with ten voters (bridged by names like
  CipherSift's `hmac.new`, CryptoScan's `SHA-2` and DeepSeek's `HS256`), cbomkit had 85 items for 61 algorithms, and
  location items exceeded located records (CryptoScan 7,685 items for 5,284 records). Confirmation by strict identity
  was 80 % against 81 % by cluster (CipherRadar 71 vs 79, CipherSift 84 vs 87, CryptobomForge 90 vs 93). With per-key
  clusters (2026-10-19) the pilot has 343 repo groups with 342 distinct member sets, cbomkit 50 items, and location
  items at most the located records (CryptoScan 12,548 of 13,208).
- **Names that were items.** Share of a tool's L4 items without a known family on ICPC (2026-10-18 code): securin-xbom
  41 % (rule ids, among them SQL injection, XXE, reflection, deserialization), DeepSeek 23 %, CryptoScan 19 %,
  Q-DARPAN 16 % (`jca`), CipherSift 13 % (API names), CipherRadar 8 %, CryptobomForge 7 %, CBOMkit 5 %. With
  `resolution` (2026-10-19; code identifiers are `api` by shape, securin-xbom's non-crypto rules by its registry's
  own path, no tool-specific lists, no judgement entries), pilot algorithm records: CipherSift 80 % mapped / 20 % api
  (its names are often the API call), securin-xbom 59 % mapped / 30 % non-crypto / 11 % undefined (TLS configuration
  rules we do not interpret), Q-DARPAN 90 / 10 % api, CryptobomForge 90 % mapped / 10 % undefined (its `UNKNOWN`
  components), CryptoScan 94 % mapped / 5 % api, the rest 99-100 % mapped. ICPC: securin-xbom 45 % mapped / 37 %
  non-crypto / 18 % undefined, Q-DARPAN 64 / 36 % api, CipherSift 93 / 7 % api, DeepSeek 91 % mapped / 4 % api / 4 %
  undefined / 1 % non-crypto (`PLAINTEXT`), CryptobomForge 94 / 6 % undefined. These shares are
  findings about the tools, not defects to repair: CipherSift's `hmac.new` records keep their recorded conflict
  (name HMAC, field SHA-2; 120 family conflicts on the pilot). CipherRadar's 14,786 material conflicts, by contrast,
  were our own dictionary (`Hardcoded secret: ...` -> credential against its field `secret-key`): 0 with the
  hierarchy.
- **Ladder after the change** (confirmed share of a tool's algorithms, L0 / L1 / L2 / L3 / L4, algorithms; the former
  L4lit level is gone with the last judgement entry; tailored pairs excluded, so CipherSift is absent on the pilot).
  Pilot, pooled 61 / 70 / 83 / 90 / 90 % (914): CBOMkit 70 -> 100 (50), cbomkit-jar 66 -> 96 (67), cdxgen 0 -> 100
  (10), CipherRadar 69 -> 90 (134), CryptobomForge 39 -> 100 (28), CryptoScan 35 -> 72 (229), pqcheck 79 -> 99 (86),
  Q-DARPAN 86 -> 100 (55), SCANOSS 67 -> 96 (100), securin-xbom 83 -> 96 (23), xgrep 72 -> 98 (132); confirmed
  through members alone 61 / 70 / 73 / 80 / 87 %; clusters spanning several identities 65 / 68 / 75 / 63 / 63. ICPC
  (held out), pooled 70 / 76 / 90 / 96 / 96 % (729): CipherSift 45 -> 100 (95), CryptoScan 46 -> 86 (85), DeepSeek
  74 -> 95 (116), CipherRadar 73 -> 90 (96), securin-xbom 83 -> 100 (48), xgrep 87 -> 100 (76), CBOMkit 86 -> 97
  (59); members alone 70 / 76 / 86 / 92 / 95 %. Parameters pooled L2 / L3 / L4, pilot: key size 23 / 31 / 80 % (249
  pairs), mode 42 / 61 / 85 (66), padding 46 / 70 / 85 (46), curve 12 / 58 / 83 (159), digest n/a / 22 / 42 (123;
  the standard has no digest field). The shares are higher than on 2026-10-18 because undefined and API names are no
  longer items nobody can confirm.
- **Scores after the change** (algorithm dimension, L3, mean of the per-repository rates [95 % CI], pooled; a rank
  needs ≥ 3 repos and ≥ 10 items). ICPC: CipherSift 100 [100, 100], CryptobomForge 100, pqcheck 100, Q-DARPAN 100,
  xgrep 100, securin-xbom 100, CBOMkit 98 [96, 100] (pooled 97), DeepSeek 98 [96, 100] (95), CipherRadar 86 [71,
  96] (90), CryptoScan 82 [64, 92] (86); cdxgen 100 % unranked (3 repos, 6 items). Pilot: CBOMkit 100, cdxgen 100 (5
  repos, 10 items), CryptobomForge 100, Q-DARPAN 100, pqcheck 99 [96, 100], cbomkit-jar 98 [94, 100], xgrep 98 [92,
  100], securin-xbom 96 [88, 100], SCANOSS 96 [91, 100], CipherRadar 89 [82, 94], CryptoScan 78 [63, 88] (pooled
  72). Location (ICPC): Q-DARPAN 100,
  xgrep 100, CryptobomForge 100, pqcheck 93, securin-xbom 93 (pooled 82), CBOMkit 90 (78), DeepSeek 84 (47),
  CipherSift 70 (42), CipherRadar 65 (38, exclusivity 54 %), CryptoScan 57 (32): the macro and pooled rates differ
  most where a few repositories hold most of a tool's records.
- **Low-expectation repositories** (ICPC, 6 repos the spec marks `expect_crypto: low`): crypto assets reported,
  securin-xbom 26 (8 algorithms; 19 in one repo), CryptoScan 25 (12), CipherRadar 25 (19), DeepSeek 20 (15),
  CipherSift 10 (6), xgrep 7, pqcheck 6, Q-DARPAN 3, CryptobomForge 3, CBOMkit 2, cdxgen 1 (no algorithm). A
  false-positive proxy, not ground truth (now a table in every summary).
- **RQ1 details.** securin-xbom: 17 of 23 CBOMs invalid (enum `relatedCryptoMaterialProperties.type`, 222 errors),
  xgrep 4 invalid (`type: dependencies`), Q-DARPAN 14 CBOMs with deprecated fields; CryptobomForge's 2 "CycloneDX
  1.6" documents were empty shells our adapter wrote. Two readings of "located" existed (cdxgen 84 % with a file
  vs 9 % with a line): both are columns now. CipherRadar's traceability was dominated by 14,825 "Hardcoded secret"
  records (14,469 on bisq2, i18n keys): key material has its own rows now.
- **Micro:** CryptoScan finds only EC and RSA of the 11 micro-c assets although C is in its languages (OpenSSL EVP
  API): a check of its invocation and patterns is open.

## Interventions in how the tools run, and their effects (e1-pilot-corpus, 2026-10-20)
BF-CBOM runs every tool as shipped, with its documented best invocation. One intervention is allowed:
when a tool crashes on a specific file and the crash ends the whole scan, that file is left out through the tool's
*own* exclusion mechanism, by its exact path, and the run records it (`workaround` column of `runs.csv`; the paper
reports the crash). A tool that runs but finds less is never steered (that is a finding about the tool), and nothing
else in its input changes (decided 2026-10-20). Numbers: the VM run of 2026-10-20 (12 repos, run 1; items are voters'
L4 identities per repository, CipherSift and observers left out).
- **CipherRadar `.cradarignore` (kept).** The Java pass dies with a nil pointer on `new ECGenParameterSpec(
  <identifier>)` and writes no CBOM. The worker lists the Java files with that trigger in `.cradarignore`,
  CipherRadar's own ignore file: 1 file in jjwt (`EcSignatureAlgorithm.java`), 9 in keycloak (6 of them tests).
  Without it CipherRadar fails on 2 of the 4 Java repos and loses all 23 of its items there (8 + 15, of its 127).
  With it, it misses what those files hold: other voters report 20 (jjwt) and 56 (keycloak) mapped records there,
  but every algorithm among them except MGF1 (keycloak) also occurs elsewhere in the repo, so at repository level
  the exclusion costs CipherRadar at most one item; at call-site level it cannot report those files' sites.
- **CryptoScan crash bisection (removed).** 1.4.0 panics in `patterns.MatchWithContext` (index out of range) on
  some files, and one such file ends the whole scan without a CBOM. The worker used to bisect with `--exclude`, but
  1.4.0's `--exclude` matches a file by its basename only and a directory by its relative path, where `dir/**` skips
  the subdirectories and still scans the files directly in `dir` (`pkg/scanner/scanner.go`): a nested file cannot
  be excluded by its path. On keycloak the true culprit stayed in every scan, 86 files "panicked" alone and the
  200-scan limit failed the run. The tool offers no exact-path exclusion, so the run now fails with the panic head
  in the error. Effect: keycloak on the pilot (1 of 12 repos; other voters report 27 algorithms there); on the
  first ICPC run (2026-10-19) allure2, bisq, docling and keycloak (4 of 39 repos). The alternatives were rejected:
  excluding by basename also hides every other file of that name, and deleting files from our clone changes the
  tool's input.
- **CBOMkit module indexing (left as the tool does it).** Not a crash: CBOMkit indexes a directory as a module only
  when it has a build file and a `src/` directory, so on Kafka-like layouts it scans a handful of files (the pilot:
  7 of 2,900 on Kafka). The pilot added an empty `src/` at the repo root; cbomkit-jar keeps that as the opt-in
  `CBOMKIT_ENSURE_SRC=1`, off in every run. Effect: CBOMkit and cbomkit-jar report nothing on kafka, where the
  other voters report 15 algorithms; keycloak carries the same risk note but is partly indexed (65 assets). Kept
  off: the empty directory is a file added to the scanned repo, it repairs the tool's indexing rather than letting
  it run, and the jar would differ from the server image by our hand.
- **Invocation choices that change results** (the tools' documented options, not interventions): CryptoScan always
  gets `--include-quantum-safe` (without it its CBOM leaves out AES, SHA-2, HMAC and PBKDF2); cdxgen gets `-t
  python` / `-t go` (without it, it never analysed Python or Go: our setup error of 2026-10-15); CipherRadar runs
  from a working directory outside the clone, so a `.cradar.yml` committed in a scanned repo cannot change the
  scan.

## Export review 2026-10-23 (corpus-pilot-medium)
The first full export of `corpus-pilot-medium` (60 repos, 15 tools, run 2026-10-22, exported 2026-10-23 after the
report's co-membership comparison stopped listing pairs) was checked for anomalies before anyone cites it. The
numbers below are from that export and its re-render with vocabulary 2026-10-23.2 (both on the laptop, so every place
is `unread`: the mirror is on the VM); the runs are redone on the VM with the caps below, so this is a snapshot.
- **Our setup killed tools.** CipherRadar died at its 4 GiB cap (rc -9, no CBOM) on pycryptodome,
  bunkerweb and pyca/cryptography; cdxgen was OOM-killed at 16 GiB on 8 of 60 repos (DefectDojo, OctoBot, FairEmail,
  bunkerweb, lnbits, scapy, xipki, zaproxy) with the runs stored `ok`, 5 of them with 0 assets. Caps now 8 and
  24 GiB (the VM peaked at 40.3 of 60.5 GiB with every tool at once), and a stored `ok` run with OOM kills reads as
  `oom` for the report as for `--retry-failed` (`common/experiment.py:stored_status`): not normalised, listed in the
  summary. SCANOSS's timeout on casdoor (1'772 s at 0.8 GiB) stays a result.
- **The name parser invented parameters (fixed, vocabulary 2026-10-23.1).** Numbers in UUIDs, OIDs, URIs and file
  names became key sizes: a CryptobomForge component named by a UUID gave `EC` with key size 15, the W3C URI
  `.../2001/04/xmldsig-more#rsa-sha256` 2001, `ISO7816-4Padding` 7816 on AES, `ec-1.2.840.10045.3.1.7.p12` 840 (that
  OID is P-256), `issue_369_rsa_pem.pub` 369; 40 of the 292 groups disagreeing on key size held such an artefact.
  Every parsed value landed on a record whatever its primitive: CipherSift writes one component per part of
  `TLS_AES_128_GCM_SHA256` (families SHA-2 and AES) and `ES512`, so its hash records carried 396 key sizes, 356
  curves, 39 paddings and 21 modes. `DES3` (pycryptodome) was single DES. A cipher-suite name gave the wrong part:
  `TLS_DHE_DSS_EXPORT_WITH_DES40_CBC_SHA` (CryptoScan "Weak Cipher Suite", 228 records) was FFDH with key size 40,
  mode CBC and digest SHA; `TLS_RSA_WITH_3DES_EDE_CBC_SHA` RSASSA-PKCS1 without the 3DES. After the four parser rules
  (shapes, plausible key sizes next to their family, parameters admitted by the primitive, the suite grammar;
  `analysis/data/CHANGELOG.md`) no name-parsed key size is outside its family's standard set, no hash record carries a
  parsed cipher parameter, and the suites are their bulk cipher (DES 40 CBC, 3DES CBC, RC4 128, AES 128 GCM) with
  `protocol: TLS`. CBOMkit's `AES-96-GCM`, `AES-48`, `3DES-64` keep their sizes through its own
  parameterSetIdentifier field (its finding), while the name parse no longer repeats them.
- **Effect on the tables** (before -> after, exact matcher): items 1'425 -> 1'407 (the 8 `oom` CBOMs are out, xipki's
  2'016 cdxgen assets among them), the L4 headline share 0.81 -> 0.81, RaQuN's cross-check 0.56 -> 0.58; key-size
  agreement inside the groups 0.85 -> 0.89 at L4 and 0.74 -> 0.82 at L3; digest 0.74 -> 0.72 on fewer groups (92 ->
  72: the suites' `SHA` digests are gone, the remaining HMAC disagreements are real: `SHA` vs SHA-256 vs SHA-512);
  curve 0.61 -> 0.61 (179 groups split Curve25519 vs Ed25519, xgrep's `curve25519` on Ed25519 against the registry's
  two curves: kept, folding them would be our judgement). CipherSift's parameter items: key size 262 -> 209, curve
  298 -> 269, digest 319 -> 227, its rates within 0.01. `vocab.csv`: 787 -> 815 unmapped (the UUID names now map to
  nothing instead of to EC), 172 -> 157 partial.
- **Additions (vocabulary 2026-10-23.2),** each traceable to a standard or an official API: `XDH` (Java's standard name;
  5 voter groups wrote it unmapped) as a generic marker over X25519 and X448; `X963KDF`/`ANSI-KDF-X9.63` -> ANSI-KDF;
  `DES3` -> 3DES (63 records); `OCB3`; padding `ISO7816-4`; java-jwt's `Algorithm.RSA256`-style names; `Streamlined
  NTRU Prime`. Not done, as judgements: Curve25519 = Ed25519, qelens' category labels (`HASHING (see usage)`, 436
  records), qScan's `unknown (certificate)`, cdxgen's `hmacSHA` (its digest rate 0.00 is what it writes).
- **Two report fixes.** The summary's "Per language" table counted 1'455 items where "Matcher x level" counted 1'425:
  30 groups whose only non-attached records were observers' (voters' generic `EC` attached to qScan's `ECDH`); the
  groups table now carries `member_voter_groups` and the two tables agree. RQ2's "Places per tool" ends with
  `outside code`, the share of a tool's located algorithm records whose file is not a source file of the repo's
  language: CryptoScan 0.83 (one DefectDojo scan fixture, `unittests/scans/jfrog_xray_unified/very_many_vulns.json`,
  holds 13'759 of its places), qScan 0.74, CipherRadar 0.71, qelens 0.28, every other tool <= 0.04. Decided
  2026-10-23: no scope filter and no test-path heuristic (the tools are benchmarked on the repositories as they are,
  and test code cannot be told apart soundly); the share is for the discussion. 169 of the 4'795 voter items rest on
  data files only, 541 more only on test or vendored paths; for CryptoScan 62 of its 128 unconfirmed items are data
  files.
- **Tool findings to keep as they are.** CryptoScan 1.4.0 panics on 9 of 60 repos (`slice bounds out of range`;
  ayza, mina-sshd, ballcat, simple-java-mail, MaxKey, surpass, pyca/cryptography, scapy, xipki); CBOMkit's Python
  parser fails on OctoBot and bandit (both packagings); cbomkit-jar on refinery (protobuf); cryptarium names 631 of
  its 804 records `UNKNOWN`; CipherSift puts curve Ed25519 on Ed448; CBOMkit's two packagings agree on every repo
  both ran. Peak VM RAM 40.3 GiB; wall time 2 h 26 min, queue wait median 532 s (cdxgen's and CBOMkit's serial
  queues).

## Mutation smoke (`mutation-smoke`, VM run 2026-10-22, reviewed 2026-10-23)
The harness end to end on the VM: the RSA-2048 key generation in three styles plus three decoys into pyjwt, jjwt and
golang-jwt (18 mutants, 21 variants, 252 runs, 18 min). Every run ok; hosts 57 (pyjwt), 89 (golang-jwt) and 1'261
(jjwt); every mutant placed, none skipped. The criterion held: CBOMkit (both packagings) and CryptoScan report the
inserted call at its line (±2) in every language and style, and hit no decoy; `mutation-scores.csv` has its F1
columns. Read with the vocabulary of 2026-10-23.2:
- **At the site in every language and style:** cbomkit, cbomkit-jar, CipherSift, CryptoScan, xgrep, qScan (Java's
  indirect insertion aside), securin-xbom (Java, Python), pqcheck (Python, Go), SCANOSS (Go), CryptobomForge (Python),
  Q-DARPAN (Go; Java direct and wrapper, not indirect), cdxgen (Go only: its Java and Python CBOMs name no algorithm,
  as in the pilots). Q-DARPAN's and qScan's miss of the Java *indirect* style (the algorithm name passed through a
  variable) is the data-flow split the indirect style was built for.
- **qelens** finds the call on the span (the import line) but not at the call line, as in the pilots (0.59 of its
  places are import lines). **cryptarium** has no locations and the originals already contain RSA (JWT libraries), so
  nothing can be told (`masked`).
- **CipherRadar** reports `RSA-2048` at the planted line in all three repositories but files it as `algorithmFamily:
  RSASSA-PKCS1`, its own field, so the record's identity is the signature scheme, not the planted unit `RSA`:
  `found_group` (same group) is true, `found_site` false, recall site 0.00 / recall group 1.00. A tool label, not a
  matching defect (never force a tool's output into the right class). It also reports algorithms on the comment
  decoy (MD5, DES, SHA-256 in a comment) and the log-message decoy (RSA) in every language: the planted negatives
  catch a pattern tool's string matches. CryptoScan hits one decoy (Go, "Certificate Expiration Check" next to the
  log message).
- **The bridge** over 29 (tool, language) cells: Spearman 0.6 (Go), 0.3 (Java), undefined in Python (every judged
  tool at F1 1.00 or 0.00), 0.1 pooled; with three mutants per language this is a smoke, not a result.
- **Fixed on review:** `new_records` counted every record a tool cannot be matched by identity (CipherRadar's
  library-import markers, 1'700 on jjwt) as new on every mutant; records are now matched by unit, or by raw name when
  they have none, so the column reads 1-5 for most tools (CipherRadar 33 on the Java mutant: the inserted JCA imports
  and calls trigger several of its patterns). `found_*` and `false_positive` were unaffected except that an
  identity-less record moved by the insertion no longer counts as a new one on a decoy.
- **What the pilot adds** (`experiments/mutation-pilot.yaml`): nine repositories, three per language (the floor for a
  bootstrap interval), every catalog snippet once per repository, so the per-(tool, language, style) cells of RQ4 are
  all filled; the smoke's single snippet (RSA, present in every JWT library, hence `masked` for the repository level)
  says nothing about the other fourteen.

## Export review 2026-10-24 (bar-micro, corpus-pilot-small, corpus-icpc, corpus-pilot-medium, mutation-pilot)
The five experiments were rerun whole on the VM on 2026-10-23 with vocabulary 2026-10-23.2 and the caps above
(cdxgen 24 GiB, CipherRadar 8 GiB, every tool at once; peak 44.8 of 60.5 GiB on pilot-medium) and exported on
2026-10-24 with the repositories' sizes recorded at pin time, so every run-time table has `ρ size` and `s / 1k files`.
stability-pilot and mutation-smoke were not part of this round.
- **The bar (bar-micro: 14 calls and 4 decoys per language).** 11 of 15 tools pass, each in every language it was
  scored in, so no `tool_languages:` narrowing follows: cbomkit, cbomkit-jar, cipherradar, ciphersift, cryptobomforge,
  cryptoscan, pqcheck, qdarpan, scanoss, securin-xbom, xgrep. Bar failed: cdxgen (0 / 0 / 6 of 14: its Java and
  Python CBOMs name no algorithm, Go exactly the throwaway run of 2026-10-22), cryptarium (0 everywhere: no
  locations), qelens (5 of 14 in Python, on import lines), qscan (5 of 14 in every language: quantum-vulnerable
  crypto only). The decoys catch one tool: CipherRadar reports crypto on the comment and the log message in every
  language (2 of 4); nobody else hits one. CipherSift's precision below 1 (0.77 Java, 0.71 Go) comes from places off
  the items' lines, not from decoys. The verdicts stand in the export's summary as the spec line to copy; the user
  writes them into the specs by hand, nothing in code or the registry needs them (decided 2026-10-29: no
  `micro_<date>` record for this run).
- **cdxgen at 24 GiB.** OOM-killed 8 times (kafka on pilot-small; 2 ICPC repos; 5 pilot-medium repos), each stored
  with output and now read as `oom`: listed in the summary, no CBOM read from it, `--retry-failed` redoes it. The
  tool failed the bar anyway, so no cap is raised.
- **Headline agreement at L4** (exact matcher / RaQuN cross-check): corpus-pilot-small 0.78 / 0.59 on 250 items,
  corpus-icpc 0.78 / 0.58 on 162, corpus-pilot-medium 0.81 / 0.58 on 1'410, the mutation pilot's nine originals
  0.91 / 0.72 on 226. On pilot-medium cbomkit's 342 items go from 0.71 confirmed under the identical name (L0) to
  0.96 at L4. The summary's "Per language" `all` row now equals the matcher table's items (1'410; it counted 1'455
  before the fix of 2026-10-23).
- **Mutation pilot (RQ4 on real code):** 9 repositories, 270 mutants (135 planted calls, 135 decoys; 45 of each per
  language), every run ok (4'185 runs; 405 call × tool cells unsupported by language). F1 per language (Java /
  Python / Go): CipherSift 1.00 / 0.98 / 1.00, inflated by construction (tuned on the micro projects, the same
  catalog; marked `*` in the table); xgrep 0.92 / 0.93 / 0.85; CryptoScan 0.89 / 0.89 / 0.86; cbomkit (both
  packagings) 0.99 / 0.64 / 0.75 (Python: pyca only, as declared); SCANOSS 0.98 in Go; Q-DARPAN 0.78 Java, 0.82 Go;
  securin-xbom 0.70 / 0.78; pqcheck 0.78 / 0.77 (Python / Go); CryptobomForge 0.62 Python. CipherRadar finds
  0.84-0.87 of the calls (recall by group 0.91-1.00: the RSA keygen filed as RSASSA-PKCS1, as in the smoke) but hits
  22-24 of 45 decoys in every language, precision 0.53-0.63, F1 0.56-0.73: the pattern tool's price. cdxgen 0.00 in
  Java and Python, 0.55 in Go; the three observers 0.00-0.39. The bridge: Spearman between confirmation rate and F1
  0.6 (Go, 12 cells), 0.6 (Java, 10), 0.3 (Python, 13), 0.4 pooled over 35 cells; Kendall τ 0.5 / 0.5 / 0.3 / 0.4.
- **Fixed on review:** `experiment list` and `status` counted an OOM-killed `ok` run as ok (now `oom`, as in the
  report); `s / 1k files` was computed over the micro projects (one file each) and read as start-up cost (now only
  with `ρ size`, 3 or more repositories whose sizes differ); the RQ4 "Not judged" line gives the count per status;
  `mutation-scores.csv` has a `tailored` column.
- **Open:** the "Spellings per identity" table counts cdxgen's certificate file names as spellings (`CA_ecdsa.pem`,
  `ca_ecdsa_256.pub`, ...: 118 of the 240 ECDSA spellings), because `vocab.csv` carries no asset type and the name
  parser reads `ecdsa` in a file name. The records are right (a certificate asset whose name says ECDSA); the table
  should count algorithm assets only, or show certificates apart.
- **Decided 2026-10-29:** the observer role goes (the bar replaces it; `role: observer`, `--observers-vote`, the
  `role` columns and the `°` mark are a cleanup); the specs other than bar-micro get the 11 tools by hand.

## Replica test 2026-10-30 (corpus-pilot-medium with and without replicas)
corpus-pilot-medium (60 repos, 10 tools) was run with replicas (`REPLICAS="cbomkit=3,cipherradar=3,scanoss=2"`, on
2026-10-30, with the tool versions of 2026-10-30 and the earlier caps: CBOMkit 16, CipherRadar 8 GiB) and compared
with the run without replicas of 2026-10-29. That baseline ran the older tool versions (also cbomkit-jar, and a 6 GB
git server), so among the replicated tools only CBOMkit compares cleanly, and among the controls CipherSift, Q-DARPAN
and pqcheck.
- **Wall time 1 h 46 min → 56 min.** CBOMkit's three containers ended at minute 56 with 109 busy minutes between
  them (36 each if shared evenly): its longest job (decred/dcrd, 34 min) started at minute 23, because the queues
  followed the corpus order. Largest first would have ended the run at about 43 min (a replay of the measured
  durations); the queues now hold the largest repositories first.
- **The git server served one request at a time.** fcgiwrap ran as one process, which serves a request until its
  response ends; with nginx not buffering, a CBOMkit full clone held it for minutes. About 15 jobs that started
  between minutes 1.5 and 2.1 of the replica run finished together between minutes 5.2 and 6.5, while three CBOMkit
  containers cloned DependencyTrack, FairEmail and ESAPI in full: Q-DARPAN on jsch took 200 s (2 s without
  replicas), pqcheck on cowrie 218 s (5 s), xgrep on dependency-track 277 s (17 s). A job's time includes its clone,
  so the wait counted as run time, for every tool; the baseline shows the same at minute 5, on a smaller scale. The
  git server now runs 16 fcgiwrap processes.
- **Replicas leave the times alone.** The median ratio of a (repository, tool) pair's time with replicas over
  without, for jobs started after minute 7, with a bootstrap 95 % CI: CBOMkit 1.03 (0.99-1.06); the unreplicated
  tools with an unchanged version CipherSift 1.05, Q-DARPAN 1.10, pqcheck 0.92. CipherRadar (1.12) and SCANOSS
  (1.19) changed version too (OpenGrep 1.33.1, crypto-finder 0.43.0) and sit in the range of the unreplicated tools
  whose version changed (1.06-1.12).
- **Replicas leave the results alone.** CBOMkit and CipherRadar have the same records and items on all 58 pairs,
  CipherSift and Q-DARPAN on all of theirs; the other differences follow the new versions (pqcheck, at a new pinned
  commit: fewer records in 31 of 40 repos, the same items; SCANOSS loses Poly1305 in 3 Go repos; CryptobomForge
  gains EdDSA on XX-Net).
- **xgrep 0.101.0 scans key and certificate files.** Its records went from 5'711 to 38'206 and its items from 525 to
  836; the share of its records outside code from 0.03 to 0.49 (`.pem`, `.crt`, `.p12`, `.crl`, `.pub`, `.der`,
  `.key`, `.jks`; bunkerweb, pyca/cryptography, XX-Net, xipki and pritunl foremost), its certificates from 239 to
  3'703. It takes 3.3 times as long.
- **CryptoScan 1.5.0** finished on all 60 repositories (1.4.0 panicked on 9) and lost 5 items on lnbits (Blowfish,
  CMAC, Poly1305, XChaCha20, XChaCha20-Poly1305).
- **CipherRadar reached its 8 GiB limit on bunkerweb** (2'269 limit hits, peak 7.9 GiB, the run ok): that peak is
  the limit's, not the tool's, hence its 12 GiB since. CBOMkit peaked at 12.4 of 16 GiB (thecodearcher/limen), the
  VM at 41.6 of 121.5 GiB; no OOM kill.
- **The same five runs fail in both:** CBOMkit's Python parse errors on OctoBot (a UTF-8 byte-order mark) and bandit
  (example files that do not parse), CipherRadar's timeouts on pycryptodome and pyca/cryptography, SCANOSS's on
  casdoor. CBOMkit's error now names the backend's message (`cbomkit_scan_error: Parse error at line 1 column 8`).
