# Vocabulary and adapter specs: change log

The shared vocabulary (`algorithms.yaml`) and the adapter specs (`../adapters/*.yaml`) are frozen (CLAUDE.md
refinement 14): they were developed on the pilot corpus (the development set); the main corpus is held out. Every
change bumps `version` in `algorithms.yaml`, adds an entry here with the fingerprint `python -m analysis.freeze`
prints and the reason, and the tests fail otherwise (`tests/test_vocab_frozen.py`). Newest entry first.

## 2026-10-20.1 (2026-10-20) fingerprint `0feb566720a73894da61d5dfc5f8fe9d369b0c4a9cbb8d8914641acb04608a7f`
CycloneDX versions harmonised where BF-CBOM writes the CBOM itself; the tools' own versions are untouched (a tool's
choice of 1.6, 1.7 or IBM's draft is part of what it ships and what RQ1 measures).
- `_llm.yaml`: the LLM workers assemble CycloneDX 1.7 (common/llm_cbom.py ASSEMBLY_VERSION and PROMPT_VERSION
  2026-10-20.1): registry values in algorithmFamily and ellipticCurve, the model's other answers in
  bf-cbom:algorithm-family and bf-cbom:curve, which the spec now reads. The dictionary entry for assetType `key` (LLM
  CBOMs assembled before 2026-10-16) is gone: no stored run has that layout (results are disposable).
- `cryptobomforge.yaml`: the worker's empty placeholder CBOM (CodeQL returned no results) is CycloneDX 1.7.
- `cdxgen.yaml`: declared version and notes corrected for the pinned 12.8.5 (it writes 1.7; the spec still described
  11.7.0 and 1.6), and its Go locations are read: the property cdx:golem:crypto:sourceLocation (`property_locations`,
  `<path>:<line>:<col>`) was ignored, so cdxgen's Go records had no location (refinement 15: our analysis reads every
  location format a tool uses).

## 2026-10-19.2 (2026-10-19) fingerprint `d5276ebc4f7d58767ec86a5e75b1440a9b7263c18ba368e8cf068a7b0beeaaf3`
Three new adapter specs for the observer tools (common/tools.yaml `role: observer`): `qelens.yaml`, `cryptarium.yaml`,
`qscan.yaml`, written on their CBOMs of the five micro projects (development data; fixtures in
tests/fixtures/normalize/). Each only declares the format and inherits the generic CycloneDX reading: no dictionary,
no extractor. Their oddities stay what they are and are described in the specs' notes: qelens' category names
(`HASHING (see usage)`) are `undefined`, cryptarium's CBOM has no locations, qScan writes the algorithm name into
parameterSetIdentifier. The shared vocabulary is unchanged.

## 2026-10-19.1 (2026-10-19) fingerprint `187dc9cd9193cb720976cc21b84465efa37415561dad05efb5059987c108235c`
Prompted by the review of e1-pilot-corpus (development set) and e1-icpc-corpus (held out; the items it prompted are
marked ICPC): the records a name produces are now classified (`resolution`: mapped, ambiguous, api, non-crypto,
undefined) and only `mapped` records are algorithm items.
- `kind: api | non-crypto` on dictionary and whole-name entries (analysis/normalize/vocab.py KINDS) for labels a tool
  invented: a library or key-management reference without algorithm (Q-DARPAN `jca`, CryptoScan's KMS/Vault
  references and `Hardcoded Key Size`, CipherRadar's `jca-*` rules), or a finding outside cryptography that a tool
  reports as an asset (securin-xbom's audit rules: SQL injection, XXE, reflection, deserialization, command injection,
  ...). Code identifiers (`Cipher.getInstance`, `crypto/x509.ParseCertificate`, `getJavaAlgorithm`) are recognised
  by shape in the engine, for every tool, never by a list of one tool's outputs (a first draft listed CipherSift's
  API names in its spec; withdrawn the same day: it enumerated the development set and suppressed the real conflict
  between `hmac.new` and CipherSift's family field SHA-2). A name without family that nobody understands is
  `undefined`; such records never become algorithm items (before: the folded raw name was an item nobody could
  confirm). Principle (CLAUDE.md Rules): nothing is forced into a "right" class; what a tool wrote is the finding.
- securin-xbom: the name is read as a rule id (`rule:name`, module path dropped), so `use-of-sha1` parses; its
  generic audit rules are `non-crypto` by the registry's own taxonomy (`rule_crypto_segments: [cbom, crypto,
  cryptography]`: a rule whose module path has none of these segments), not by a list of ours (a first draft listed
  14 rule names; withdrawn). Its TLS configuration rules and `gcm-detection` are read as they parse.
- All seven judgement entries (`interpretive: true`) removed: CryptoScan's Certificate Validation Bypass and Weak
  Cipher Suite (-> TLS), Subject Alternative Name and ACME/Let's Encrypt (-> CERT), CipherRadar's
  cipher-spec-inventory and Certificate Validation Disabled (-> TLS), the LLM workers' Entropy (-> seed). Their
  effect on the pilot ladder was nil (2026-10-18); what a tool reports is the finding. With no judgement entry left
  the ladder level L4lit equals L4 and is gone (the engine keeps the `interpretive` tag; a test fails if a spec uses
  it). Every remaining dictionary entry is a tool's own spelling or label read as it says, with its source noted.
- Generic markers (`generic_over`): a bare `SHA` (CipherRadar `Algorithm reference: SHA`, 238 ICPC records; `HMAC-SHA`)
  is a hash of unspecified family (addition `SHA`, group HASH-other); generic identities attach to the specific
  algorithms' clusters and never merge them (analysis/levels.py), and refine instead of conflicting.
- `material_hierarchy`: credential > password, token, key, shared-secret; key > private-key, public-key, secret-key.
  CipherRadar's `Hardcoded secret: ...` (dictionary: credential) next to its field `secret-key` is no conflict any
  more (14,786 pilot conflicts were ours).
- Vocabulary: XChaCha20; JOSE ECDH-1PU(+A128KW/A192KW/A256KW), C20P, XC20P (ICPC, DeepSeek); S256 (PKCE method,
  api) and PLAINTEXT (non-crypto); protocols OpenPGP (PGP, GPG; ICPC) and OTR (SCANOSS, 79 pilot records on
  ecdsa/ed25519 Verify lines: a tool misidentification the mapping makes visible); materials OctKey -> secret-key,
  KeyPair, JWK, JWKS, JWK Set -> key; the digest length of a hash without that variant (BLAKE2b-256) is its
  parameter set instead of an unmapped token.
- Lists in a field (`SHA-256, RIPEMD-160` in an LLM digest property) are `ambiguous` candidates, not unknown values.
- SCANOSS: the `kv_name` extractor reads `certificate:<line>:<line>:api=...:cryptoFunction=...:library=...` names
  (asset type, API as supporting evidence, function); 324 pilot records were partially unmapped before.

## 2026-10-18.1 (2026-10-18) fingerprint `a88c46c9ad7e78cf21eddb03fcad46fcd5926704998efd4a7bf86b11c6c91ca2`
- Frozen for the first time (WP2 of "Completing Phases 1-3").
- Tool idioms out of the shared vocabulary into the tool specs (L4): CipherRadar's rule names `weak-rand` and
  `math-random-security`, pqcheck's `MATH-RAND`; `Weak PRNG` dropped (no tool emits it). Standard identifiers stay
  shared however few tools use them (JOSE, SSH, JCA, OS and library APIs).
- A bare `rand` / `RAND` no longer maps (C `rand()` is a PRNG, OpenSSL `RAND` a CSPRNG: ambiguous, so unmapped);
  `OpenSSL RAND` as a two-word API name stays CSPRNG.
- `interpretive: true` on dictionary entries that are judgements rather than spellings (CryptoScan: Certificate
  Validation Bypass, Weak Cipher Suite -> TLS; Subject Alternative Name, ACME/Let's Encrypt -> CERT; CipherRadar:
  cipher-spec-inventory, Certificate Validation Disabled -> TLS; LLM workers: Entropy -> seed). They count at L4
  only; the ladder reports L4lit (without them) next to L4.
- `implies` table added (refinement 2): PBKDF2 -> HMAC, PBES2 -> PBKDF2 + HMAC, PBES1 -> PBKDF1, TOTP/HOTP -> HMAC,
  Ed25519 -> SHA-512, Ed448 -> SHAKE256, ECIES -> ECDH, X25519MLKEM768 -> X25519 + ML-KEM-768; the digest rule lives
  in the code (`Vocabulary.implied`). Applied only with `--closure`.

## 2026-10-16.1 (2026-10-16)
- Seeded from the distinct values of 124 CBOMs of 11 tools (e1-pilot-corpus). Not fingerprinted.
