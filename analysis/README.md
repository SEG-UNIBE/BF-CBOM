# The analysis: what we normalise, what we do not, and why

The analysis turns the tools' CBOMs into comparable records (`normalize/`), matches them (`levels.py`,
`matchers/`) and scores the tools without ground truth (`score.py`). Every step of it is a place where we could
make the tools look more alike than they are. This page states the policy that keeps us from doing so, shows the
L0-L4 ladder on one example, and gives the argument for the two vocabulary layers the paper has to defend. The
command reference, the file formats and the column lists are in `experiments/README.md`; the plot recipes in
`report/README.md`; the numbers in `docs/findings.md`.

## The principle

**What a tool reports is the finding.** We do not force a tool's output into the "right" class. The tools'
heterogeneity, their odd labels, their self-contradictions and their non-crypto findings are results the paper
reports, not defects to repair before the analysis starts. Normalisation is unavoidable for matching, so it is
done in steps (the ladder), and the raw reality (L0, L1) is reported next to the best effort (L4). Nothing is ever
guessed: a name nobody can read stays `undefined` and never becomes an item that another tool could confirm.

## Units: asset, record, item

A tool's *findings* are the cryptographic uses its CBOM reports (the word of RQ2 and RQ3). Every table counts
them in one of three units. They differ by orders of magnitude for the same output, so a number is only
readable with its unit (the normalisation table counted records until 2026-10-20 and showed CryptoScan's 199 assets
as 13,996).

| Unit | What it is | Identified by | Counted in |
|---|---|---|---|
| **asset** | one crypto asset component of a CBOM, as the tool wrote it | `component_index` in the CBOM | RQ2 (what the tools report), RQ1 meaningful, the RQ2 section (`units-run<i>.csv`, `coverage-run<i>.csv`, `conformance-run<i>.csv`), `normalize.json` |
| **record** | one occurrence of an asset: an asset found at five lines gives five records, an asset without occurrences one record without a location | `record_id` = `<tool>:<component>:<occurrence>`, its location `site_id` = `<tool>:<file>:<line>` | `records-run<i>.csv`, `units-run<i>.csv`, RQ1 traceability, the matchers' clusters and call-site groups (`members-*.csv`) |
| **item** | one algorithm in one repository (snapshot and variant): one distinct L4 identity, however many assets and records of a tool carry it; a tool reports the item when one of its `mapped` records has that identity | algorithm: (snapshot, unit); parameter: (snapshot, unit, value); location: (snapshot, unit, file:line) | the ladder (`levels-`, `items-run<i>.csv`), the scores, stability, the tools-per-item distribution, E4 |

- **Asset to record.** The normaliser writes one record per (component, occurrence) (`normalize/record.py`), and
  the values sit on the record, since occurrence evidence has the highest precedence. A component's records nearly
  always agree; where occurrences carry their own key size or curve (CryptoScan), one asset yields several items.
- **Record to item.** Only `mapped` records have an item (the `resolution` table below): an `api`, `ambiguous`,
  `non-crypto` or `undefined` record counts as an asset and a record, never as an item. Records of the same identity
  in one repository are one item of the tool, whether one asset with 800 occurrences carries them or 800 assets.
- **Packaging.** How a tool splits its findings into assets and records is its output format, not what it found:
  CBOMkit lists an algorithm once with its occurrences, CipherRadar writes one component per finding. Asset and
  record counts therefore describe a tool's output, item counts what it reports. Every tool's format: "Packaging per
  tool" below; an experiment's numbers: its `units-run<i>.csv`.
- **Harmonised packaging.** Nothing that compares tools sees their packaging: every comparison first collapses a
  tool's records into one canonical unit, so 800 occurrences or 800 components of one algorithm count once. The
  ladder, the exact matcher's repo-level groups and the scores count items, (item, value) pairs and (item, site)
  pairs (a site = file and line; the call-site groups chain located records within the window); RaQuN's elements are
  a tool's distinct attribute sets per repository, over the exact matcher's records (`matchers/raqun.py`: records
  with the same canonical values are one element, so AES-128 and AES-256 are two). Packaging shows only where a table describes the tool's own document:
  RQ2's asset counts as written, RQ1's meaningful shares, RQ2's names and values (shares of
  components, as a reader of the CBOM finds them), and, by design, `units-run<i>.csv`, which measures it.
- **Kinds of place.** Tools also differ in what a place is, and every record says which kind it names
  (`location_kind` in `records-run<i>.csv`, per tool in `units-run<i>.csv` and the summary's "Places per tool";
  `schema.py:location_kind`): `line` a single line of code that is not an import (in practice the call or the
  declaration), `window` a span of several lines, `import` a single line holding an import statement, `file` a file
  without a line, `none` no location, `unread` a single line whose file the snapshot files available to the
  analysis do not hold (the VM's mirror has them; `--fetch` for a copied export). A tool that reports the import
  line places the same use elsewhere than one that reports the call, which the ±2-line window absorbs only in part.
  Next to the kind, every located record says whether its file is a source file of the repository's language
  (`in_scope`; per tool `outside_code` in `units-run<i>.csv`, the share "outside code" of "Places per tool"): on
  corpus-pilot-medium 83 % of CryptoScan's, 74 % of qScan's and 71 % of CipherRadar's places were fixtures, test
  vectors, configuration or documentation (one DefectDojo scan fixture alone held 13'759 of CryptoScan's). The
  analyses count these places like any other, because the tools are benchmarked on the repositories as they are
  (decided 2026-10-23: no scope filter, no test-path heuristic); the share is for the discussion.
- **Two more words.** A *unit* is an L4 identity: the identity of an item (the `unit` column of
  `items-run<i>.csv`) and the expected value of an RQ4 mutant (`expected.unit`). A *site* is a record's file and line
  (call-site level, refinement 1). The micro ground truth's entries are *expected items* at site level, a known
  crypto call in a function of its own (`micro-items-run<i>.csv`; `experiments/corpus/micro/ground_truth.yaml`, generated from
  the snippet catalog, keeps the pilot's key `assets`).
- **Where "algorithm" stays.** Only as the CycloneDX asset type (an *algorithm asset*), as the `algorithm`
  dimension (next to key size, mode, padding, curve, digest and location) and as the record field `algorithm`. What
  is counted or compared is always an item.

### Packaging per tool

What one CBOM component is for each tool, and where its places in the code are. This is the tool's output format,
read as shipped (the adapter spec's `notes:` in `adapters/<tool>.yaml` say more), so this table changes only when a
tool is added or its pinned version changes, together with its spec; `tests/test_analysis.py` checks that every
integrated tool has a row. The numbers of an experiment are in its `units-run<i>.csv` and the summary's "Units per
tool": records per asset, assets per item, the share without a location.

| Tool | One component is | Its places |
|---|---|---|
| CBOMkit, cbomkit-jar | an algorithm (registry or JCA name); key material separate, linked through `dependencies` | every occurrence in `evidence.occurrences` |
| CryptoScan | a category (RSA, ECC, HMAC-SHA2, also a list such as `RSA, ECC, AES`) | every match an occurrence; its `symbol` is the matched text and carries its own values, so one asset can stand for several items |
| xgrep | an algorithm (registry-style name) | occurrences |
| SCANOSS | an (algorithm, parameters) pair | every match an occurrence |
| Q-DARPAN | an algorithm | up to 30+ places, written by the tool into properties `qdarpan:location.N` and copied into occurrences by our worker |
| qScan ° | an (asset type, algorithm, discriminator) triple | occurrences as `location: <file>:<line>` |
| CryptobomForge | one entry of CodeQL's crypto inventory (IBM's draft format) | `detectionContext` windows of 5-9 lines |
| CipherSift | a (rule, name, parameter, OID) combination | occurrences, the `symbol` is the API |
| CipherRadar | a finding | one occurrence (absolute path of its temporary clone) |
| pqcheck | a finding (with detector id and confidence); declared dependencies are separate library components, skipped | one occurrence |
| qelens ° | a finding, many of them on import lines | one occurrence |
| securin-xbom | a semgrep finding, named after the rule id | one occurrence |
| cdxgen | an algorithm name (lower case, with OID); also crypto files (certificates, keys) | only for JS/TS (occurrences) and Go (property `cdx:golem:crypto:sourceLocation`); Java and Python algorithms have none |
| Cryptarium ° | a distinct finding id | none: the CBOM has no locations (only the tool's JSON format has them) |
| DeepSeek, ChatGPT, Claude | a site the model reported (CycloneDX assembled by our worker, `common/llm_cbom.py`) | one occurrence, file and line checked against the clone |

Three patterns follow. Tools that list an algorithm once with its places (CBOMkit, CryptoScan, xgrep, SCANOSS,
Q-DARPAN, qScan, CryptobomForge) have about one asset per item, so their asset counts are close to what they report.
Tools that write one component per finding (CipherRadar, pqcheck, qelens, securin-xbom, Cryptarium, the LLM workers)
have one record per asset and, wherever an algorithm is used more than once, several assets per item. CipherSift
sits in between: one component per rule match combination, with occurrences. Hence: asset and record counts
describe a tool's output format, items what it reports, and every cross-tool comparison counts items.

## What an adapter spec may contain

One spec per tool (`adapters/<tool>.yaml`) reads that tool's CBOMs into records. Its contents, from most to least
defensible:

1. **Structure.** Where the tool puts things: standard fields, properties, occurrence symbols, location formats,
   named extractors. A reader for the tool's output format, verifiable against its documentation. Always allowed.
2. **The tool's own labels, read as they say.** `pk` is the tool's spelling of pke; `Hardcoded secret: ...` is a
   credential; `Weak PBKDF Usage` names PBKDF; `jca-cipher-getinstance` names an API. Each entry translates what the
   label literally states, carries a comment naming its source (the tool's rule list, pattern catalog or output),
   and adds nothing the label does not say.
3. **The tool's own metadata, read structurally.** When the tool classifies its findings itself, we read that
   classification instead of making our own: securin-xbom's rule ids carry the registry's topic path
   (`...security.audit.crypto.use-of-sha1` vs `...security.audit.sqli.jpa-sqli`), so `rule_crypto_segments: [cbom, crypto,
   cryptography]` makes every rule outside those segments `non-crypto`. One setting, no list, covers rules we have
   never seen.
4. **Judgements** about what a label implies (`interpretive: true`, "Certificate Validation Bypass means TLS"). The
   mechanism exists in the engine, and no spec uses it: a test fails if one does. The seven entries that existed
   were removed on 2026-10-19; their effect on the ladder had been nil.
5. **Lists of observed outputs.** Never. A list of one tool's API names or rule names, collected from the
   development set, is overfitting: the next repository produces strings the list does not know, and the
   categories then depend on what we happened to see. Code identifiers are recognised by shape in the engine
   (`Cipher.getInstance`, `crypto/x509.ParseCertificate`, `getJavaAlgorithm`), for every tool alike.

The shared vocabulary (`data/algorithms.yaml`) holds spellings traceable to a standard or an official API: the
CycloneDX 1.7 registry, JOSE (RFC 7518), SSH, JCA names, OS and library APIs. Both files are frozen before the
held-out corpora, versioned, and every change is recorded with its reason and provenance in `data/CHANGELOG.md`
(`python -m analysis.freeze`); the pilot corpus is the development set. A two-rater review of `vocab.csv` (Cohen's
κ) is an option for the paper, not decided.

### Observers

Three tools (qelens, cryptarium, qScan) failed the pilot's inclusion bar and run as **observers** (`role: observer` in
`common/tools.yaml`). Their CBOMs are normalised with specs of the same kind as everyone's, they have their own
ladder and score rows, and the voters confirm their findings; but an observer's record never confirms another tool,
never counts in k, exclusivity or Dawid-Skene, and an observer is never ranked. Every voter number is therefore the
same with and without them (a test checks it), and `--observers-vote` turns them into voters for the sensitivity
check. One predicate decides it everywhere (`common/tools_registry.py:votes`).

### Voter groups

A tool's finding counts as confirmed only when a tool of another voter group reports it too (the ladder, the
matchers' k, the scores). What makes two tools' votes dependent is shared detection logic: the same engine packaged
twice, the same rules, the same prompts and file selection. A shared pattern engine running rules written
independently is not: the rules decide what is found, the engine only runs them. The groups (`voter_group` in
`common/tools.yaml`, one per tool by default):

| Voter group | Tools | Shared detection logic | Evidence |
|---|---|---|---|
| cbomkit | CBOMkit, cbomkit-jar | the same sonar-cryptography engine, as a server image and as the action jar | the engine versions recorded in `tools.yaml` and the runs |
| one each | securin-xbom, CipherRadar, SCANOSS | only a pattern engine (semgrep; opengrep, its fork); the rules are their own | the rule-overlap check below |
| one each | DeepSeek, ChatGPT, Claude | our prompts and file selection, not the model | `--merge-llm` merges them (sensitivity) |
| one each | every other tool | none | its own engine |

Observers have groups of their own but confirm nobody (above).

**Rule-overlap check** (2026-10-20, `misc/rule_overlap.py`, the rules at the commits the workers pin), per language
both tools of a pair scan:

| Pair | Language | Rules | Identical patterns | Rules sharing a pattern | Near-identical rules | Shared ids, messages |
|---|---|---:|---:|---:|---:|---:|
| securin-xbom, CipherRadar | Java | 82, 75 | 0 | 0, 0 | 0 | 0, 0 |
| securin-xbom, CipherRadar | Python | 11, 34 | 0 | 0, 0 | 0 | 0, 0 |
| CipherRadar, SCANOSS | Go | 19, 187 | 3 | 3, 3 | 0 | 0, 0 |
| CipherRadar, SCANOSS | JavaScript | 18, 20 | 0 | 0, 0 | 0 | 0, 0 |

The three identical Go patterns are bare API names any rule author writes (`bcrypt.GenerateFromPassword($V, $V)`,
`tls.VersionTLS10`, `tls.VersionTLS11`); "near-identical" means a token Jaccard of at least 0.8 between two rules'
patterns. securin-xbom (Java, Python) and SCANOSS (Go, C, JavaScript, Rust with its open rules) scan no common
language, so they never meet. No rule set names another as its source, and CipherRadar runs a tree-sitter and a
YARA-X pass besides its rules. Until 2026-10-20 the three were one voter group `grep-rules`, a conservative choice by
the shared engine; the check showed that it rested on the engine alone, so they count apart since.

**Sensitivity.** Every report ranks the tools again under other groupings (`grouping-run<i>.csv`, the summary's
"Other voter groupings", Kendall's tau against the declared ranking): `per-tool` (every tool its own voter, so
CBOMkit and cbomkit-jar confirm each other), `llm-merged` (the LLM workers as one voter) and `engine-merged` (the
tools of one pattern engine family as one voter: `engine_family` in `tools.yaml`, the former `grep-rules`); next to
it leave-one-voter-out. **Planned with the mutation run (RQ4):** whether two tools miss the same inserted items more often than chance
(conditional dependence given the truth, the assumption behind independent voters and Dawid-Skene); tools with
strongly correlated misses share a blind spot and would be one voter. Agreement on unlabelled data cannot decide
this, since two tools also agree when both are right.

### What a record's name amounts to (`resolution`)

| Value | Meaning | Example | Algorithm item? |
|---|---|---|---|
| `mapped` | an algorithm identity | `SHA256withECDSA`, `A256GCM`, a field `algorithmFamily: AES` | yes |
| `ambiguous` | a list of candidates | CryptoScan's `RSA, ECC, AES, ChaCha20` | no |
| `api` | a code identifier or a tool's reference label, no algorithm | `Cipher.getInstance`, `jca`, `HashiCorp Vault Path` | no |
| `non-crypto` | a finding outside cryptography reported as an asset | securin-xbom's `...sqli.jpa-sqli` | no |
| `undefined` | a name nobody can read | `0`, `UNKNOWN`, `Sig` | no |

The shares of each tool's algorithm assets are RQ2 results (the summary's RQ2 section,
`coverage-run<i>.csv`; per record in `records-run<i>.csv`). Only `mapped` records enter the ladder and the scores.

**What our reading of a name may add** (vocabulary 2026-10-23.1, prompted by the corpus-pilot-medium review): the
parser never invents a parameter. A UUID in a name carries nothing; an OID is read through the `oids:` table (RFC
8017, 5480, 8410, ...); a URI names its algorithm in the fragment (RFC 6931); a bare number is a key size only next to
the family it belongs to and when that family may have it (`key_sizes:`, each set from its standard: AES 128/192/256,
the RSA group 512..16384 by 8), so `ISO7816-4Padding` is no AES key size and `issue_369_rsa_pem.pub` no RSA modulus; a
parsed or derived parameter the record's primitive does not have is dropped (`admits:`: a hash has no key size, mode,
padding or curve, so CipherSift's SHA-2 component named `TLS_AES_128_GCM_SHA256` keeps neither the 128 nor the GCM);
a cipher-suite name yields its bulk cipher with `protocol: TLS` (`cipher_suites:`; key exchange, authentication and
MAC are no values of that record, `SHA` is SHA-1 by RFC 5246 A.5). A tool's own field or label is never checked or
dropped: CBOMkit's `AES-96` in `parameterSetIdentifier` stays its finding.

## The ladder, by example

The unit of the ladder is fixed: an *item* is one canonical algorithm (its L4 identity) that a tool reports in a
repository. The levels only change *when two reports count as the same algorithm*, and the relation is
cumulative: a match found at a level is never lost at a higher one, so every share can only grow and each step's
gain is attributable to one normalisation step (`levels.py`). Five tools report the same AES use in one repository:

| Tool | Reports | Key at L0 | L1 | L2 | L3 | L4 |
|---|---|---|---|---|---|---|
| A | name `AES` | `AES` | `aes` | AES | AES | AES |
| B | name `aes` | `aes` | `aes` | AES | AES | AES |
| C | name `A256GCM` (JOSE identifier) | `A256GCM` | `a256gcm` | AES | AES | AES |
| D | name `AES/GCM/NoPadding` (JCA transformation) | `AES/GCM/NoPadding` | `aesgcmnopadding` | `aesgcmnopadding` | AES | AES |
| E | name `Cipher.getInstance`, tool property `family: AES` | (identifier, no key) | (no key) | (no key) | (no key) | AES |

- **L0, raw names.** Only verbatim identical names match. Nobody matches anybody here: five spellings, five
  clusters. A's L0 share is 0 %.
- **L1, case and separators ignored.** `AES` = `aes`: A and B match. The others still stand alone.
- **L2, standard vocabulary.** Whole names and CycloneDX 1.7 `algorithmFamily` values are looked up in the
  registry and the standard identifier tables (JOSE, SSH, JCA): `A256GCM` is AES-256-GCM by RFC 7518, so C joins
  A and B. D's transformation string is not a whole name in any standard table and stays apart.
- **L3, composite names parsed.** `AES/GCM/NoPadding` parses to family AES, mode GCM, padding NONE: D joins. A
  parse never guesses; a token it does not know is left unmapped and reported.
- **L4, the tool specs.** Everything tool-specific: E's spec says that this tool writes the family into a property
  of its own, so E joins. E's *name* is a code identifier and contributes no key at any level: nothing is read into
  `Cipher.getInstance`.

At L4 the cluster holds all five; A's share is 100 %, and the ladder for A reads 0 / 100 / 100 / 100 / 100 %, for
D 0 / 0 / 0 / 100 / 100 %. Pooled over the five tools: 0 / 40 / 60 / 80 / 100 %. The L0 to L4 gap is the agreement
that normalisation created; hypotheses are tested at L2 (standard vocabulary only) and L3, L4 is the upper bound.

Three cases the example leaves out:
- A sixth tool reporting `getJavaAlgorithm` (xgrep names the variable when it cannot resolve the algorithm) or
  `RSA, ECC, AES` (CryptoScan's category list) has no item: `api` and `ambiguous` records never match, at any level.
- A tool reporting `SHA-2` next to others reporting `SHA-256` and `SHA-512` is *attached* to both specific clusters
  from L2 on: it confirms them and is confirmed by them, but it never merges SHA-256 with SHA-512. Clusters are per
  key and never merge through a record with several keys (CipherSift's `hmac.new` parses to HMAC at L3 and carries
  SHA-2 in its field at L4: it sits in both clusters, which stay two). The summary reports how many confirmations
  rest on such attachments (`confirmed_strict`) and how many clusters span several identities
  (`multi_unit_clusters`).
- Parameters (key size, mode, padding, curve, digest) get the same ladder from L2 on, over (algorithm, value)
  pairs: a value counts at L2 only from a standard field, at L3 also when parsed from the name, at L4 from any
  source. Digest starts at L3 because CycloneDX has no field for the digest of a composite.

## Family and group

**`family` is the standard's own canonical level.** CycloneDX 1.7 introduced `algorithmFamily` with a registry of
names and OIDs (vendored in `data/cyclonedx/`). Normalising to it means normalising to the vocabulary the tools
claim to implement, not to a taxonomy of ours, and L2 of the ladder is exactly that: the standard vocabulary and
nothing else. The registry names schemes (RSAES-OAEP, ECDSA) but tools mostly report key types without a scheme
(`KeyPairGenerator.getInstance("RSA")`), unnamed random sources and a few algorithms the registry lacks. Rather than
dropping those records we add a name and disclose it: the additions (RSA, EC, CSPRNG, PRNG, PBE, FN-DSA,
NTRU-Prime, Hybrid-KEM, crypt and the generic `SHA` marker) are marked `registry: false` in the data, listed with
their reasons in `data/algorithms.yaml`, and the share of items that rest on an addition rather than a registry name
is a number the paper reports (it is dominated by RSA and EC). That the standard cannot name what tools most often
report is itself a finding about the standard.

**`group` is ours and carries no claim.** The coarse groups (RSA, EC, AES, SHA-2, HASH-other, HMAC, TLS, CERT, ...)
are the families table of the ICPC paper, kept for continuity. In the data they are a deterministic many-to-one
aggregation of families (`groups:` in `data/algorithms.yaml`), used for the micro ground truth and for figure
readability only; no agreement or ranking result depends on them, and every group-level number is an aggregation of
family-level numbers that can be recomputed. Where the paper needs a standard coarse view, CycloneDX `primitive`
(hash, block-cipher, signature, pke, kdf, mac, drbg, ...) is the standard's own axis, orthogonal to families (RSA
spans pke and signature), and serves fill-rate and distribution figures without any taxonomy of ours.

**Asset types are CycloneDX's.** `asset_type` is the `assetType` enum (algorithm, certificate, protocol,
related-crypto-material) plus `library` for the dependency components some tools emit, which are not crypto assets
and are counted apart; `material` and `primitive` are the standard's per-type enums without the no-information
values `other` and `unknown`; protocols fold to the `protocolProperties.type` enum or are `other` there (SSL,
OpenPGP, OTR). A test keeps `data/cbom-norm.schema.json` equal to the vendored 1.7 schema, and the RQ1 tables are
per asset type with that type's own field set.

## Mutants (RQ4)

A mutant's CBOMs are normalised like every other, with the same specs and vocabulary; nothing in the normaliser knows
about mutants. `analysis/mutation.py` compares each tool's records on a mutant with its records on the original and
asks whether a new record of the snippet's expected unit (its L4 identity, as at the top of the ladder) appeared at the
inserted call, on the inserted lines, in the file or in the repository. The expected units are written in the
vocabulary (`tests/test_mutation.py` checks them), so detection rests on the same normalisation as the agreement
numbers; a lenient column (`found_group`, the coarse group at the site) shows how much a stricter reading costs. A
planted decoy (a comment or a log message naming algorithms, a CRC32 checksum, a Base64 encoding; what each one tests:
`experiments/corpus/README.md`) is judged the other way round: any new crypto record on its inserted lines is a
`false_positive`, so `mutation-scores.csv` can give a precision and an F1 next to the recall. The
agreement analyses never see the mutants (they read the original variants only).
