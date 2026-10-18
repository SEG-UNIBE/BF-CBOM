# Findings so far

Results gathered while building BF-CBOM 2.0, moved out of `CLAUDE.md` on 2026-10-18. They come from the development
set (the pilot corpus `corpus/pilot-2026-10-08.yaml`, the micro projects `corpus/micro`) and from runs that will be
rerun before the paper's experiments; the date says which code produced a number. The micro projects are tiny and
AI-written: their numbers are an inclusion check, not a ranking.

## Pilot of 2026-10-08
A colleague's one-day pilot: 15 generators on 16 pinned projects plus the micro projects, an inclusion bar and
location-aware cross-matching with hand-checked samples. Reports and the landscape survey: `docs/pilot-2026-10-08/`.
- **Agreement tracks precision, but careful tools are right alone.** Precision by number of agreeing tools k = 1 / 2 /
  3+: 48 / 88 / 100 % on Java and Python, 39 / 60 / 96 % on Go, C and JS. CBOMkit, Q-DARPAN and pqcheck are about
  100 % precise even alone, so the "right alone" penalty is real; Dawid-Skene weighting and the validation of
  exclusive findings (E4) are essential. Only about 18 % of the clusters had two or more tools.
- **Matcher rules** (the call-site matcher, retired 2026-10-18; its line window lives on in the exact matcher): same
  file, |Δline| ≤ 2, family sets intersect, nearest-neighbour linking; an unknown family never matches (as a wildcard
  it inflated weak tools). Opengrep/semgrep rule tools share the voter group `grep-rules`.
- **Formats:** CryptobomForge emits IBM's draft `1.4-cbom-1.0`, not CycloneDX (the ICPC paper's claim needs a
  correction). cdxgen reports no file or line for crypto findings outside JS/TS.
- **Shared blind spot of all tools:** algorithm names passed through variables or constants (the third insertion style
  of Phase 4).
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
of the crypto queries, a CBOMkit image without Go (`notes.md` "Coworker hand-over"). An example for the paper of why
the audit matters (refinement 15).

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
- **cdxgen** by design: algorithms with locations only for JS. With `-t` it reports 5 / 6 algorithms on micro-python /
  micro-go without locations, which count at repository level. Java gives nothing with any invocation: atom 2.5.6 tags
  Java crypto APIs `crypto`/`crypto-algorithm` but has no `crypto-generate` sinks, and Java's reachable-flow search
  uses only generic sources and sinks (framework input, CLI, network, ...), so micro-java has 0 reachable flows. On
  micro-python it finds 4 crypto-to-crypto flows (`ec.SECP256R1()` -> `ec.generate_private_key(...)`, ...).

### 2026-10-18, scoring on the normalised records
The `micro` scoring moved from the call-site extraction onto the normalised records (`score_records`) and added the
repository level (refinement 1). Every call-site cell stayed equal except two, both explained:
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
  in IBM's draft (no schema), the two 1.6 ones valid.
- **Meaningful** (standard fields / any source): primitives mostly in standard fields except cdxgen (0 / 70 %) and
  securin-xbom (42 / 96 %); key sizes largely outside the standard for pqcheck (12 / 44 %) and cdxgen (0 / 31 %); OIDs
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
  and keeps at most one asset per tool; the exact matcher pools them.

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
cumulative (`experiments/README.md` "How much agreement does normalisation create?"). Every tool's share is
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

**Matcher x level (exact matcher; RaQuN needs the PyQuN package, not installed on the laptop).** Pilot corpus, 433
algorithms per repository: the share reported by at least two voter groups rises from 38 % at L0 to 50 % at L4
(k >= 4 from 15 to 26 %); of the groups with two voters reporting a key size, 95 % agree at L2 (42 groups) and 72 % at
L4 (76), curve 61 -> 57 % (18 -> 51), digest agreement exists from L3 (2 groups) and is 67 % at L4 (39); of the
groups with two located voters, 63 % share a call site at L0 (210 groups), 50 % at L4 (217): as clusters grow, more
voter pairs are located but not at the same place. ICPC corpus, 289 algorithms: k >= 2 from 37 % to 47 %, k >= 4 from
20 % to 37 %; key-size agreement 79 % (19) at L2 to 36 % (39) at L4, location 78 % (144) to 73 % (135). The exact vs
RaQuN comparison per level is produced when RaQuN is available (VM).
