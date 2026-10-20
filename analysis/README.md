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

### What a record's name amounts to (`resolution`)

| Value | Meaning | Example | Algorithm item? |
|---|---|---|---|
| `mapped` | an algorithm identity | `SHA256withECDSA`, `A256GCM`, a field `algorithmFamily: AES` | yes |
| `ambiguous` | a list of candidates | CryptoScan's `RSA, ECC, AES, ChaCha20` | no |
| `api` | a code identifier or a tool's reference label, no algorithm | `Cipher.getInstance`, `jca`, `HashiCorp Vault Path` | no |
| `non-crypto` | a finding outside cryptography reported as an asset | securin-xbom's `...sqli.jpa-sqli` | no |
| `undefined` | a name nobody can read | `0`, `UNKNOWN`, `Sig` | no |

The shares per tool are RQ2 results (`records-run<k>.csv`, `normalize.json`). Only `mapped` records enter the
ladder and the scores.

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

## Mutants (E4)

A mutant's CBOMs are normalised like every other, with the same specs and vocabulary; nothing in the normaliser knows
about mutants. `analysis/mutation.py` compares each tool's records on a mutant with its records on the original and
asks whether a new record of the snippet's expected unit (its L4 identity, as at the top of the ladder) appeared at the
inserted call, on the inserted lines, in the file or in the repository. The expected units are written in the
vocabulary (`tests/test_mutation.py` checks them), so detection rests on the same normalisation as the agreement
numbers; a lenient column (`found_group`, the coarse group at the site) shows how much a stricter reading costs. The
agreement analyses never see the mutants (they read the original variants only).
