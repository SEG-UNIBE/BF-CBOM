# Vocabulary and adapter specs: change log

The shared vocabulary (`algorithms.yaml`) and the adapter specs (`../adapters/*.yaml`) are frozen (CLAUDE.md
refinement 14): they were developed on the pilot corpus (the development set); the main corpus is held out. Every
change bumps `version` in `algorithms.yaml`, adds an entry here with the fingerprint `python -m analysis.freeze`
prints and the reason, and the tests fail otherwise (`tests/test_vocab_frozen.py`). Newest entry first.

## 2026-10-23.2 (2026-10-23) fingerprint `b4be7318db06f0f483d9e3d132680585ea9f6cc24f7b29f7a09ea20421f24907`
Spellings the corpus-pilot-medium review found unmapped or misread, each traceable to a standard or an official API
(the rule of this file); nothing added because it makes tools agree, nothing that folds what the registry keeps apart
(Curve25519 and Ed25519 stay two curves; a tool's field values stay as written).
- `XDH`, Java's standard algorithm name for Diffie-Hellman over Curve25519 or Curve448 (JDK 11 `KeyAgreement` /
  `KeyPairGenerator`), unmapped before although 5 voter groups write it: an addition with primitive key-agree and a
  generic marker (`generic_over`) over the algorithms X25519 and X448, which it attaches to without merging them
  (`Vocabulary.specifics` now reads algorithms in `generic_over`, not only families).
- `ANSI-KDF`: the spellings `X963KDF` (pyca `cryptography.hazmat.primitives.kdf.x963kdf.X963KDF`, also as an API
  prefix), `X9.63-KDF`, `ANSI-X9.63-KDF`, `ANSI-KDF-X9.63` (CBOMkit; the registry's own pattern), `KDF-X9.63`.
  `ECDH-X963KDF` gave ECDH with key size 963 before.
- `3DES`: `DES3`, pycryptodome's module name (`Crypto.Cipher.DES3`, until now an API prefix only); as a name it parsed
  to single DES (qelens 32 records, CipherSift).
- `OCB`: `OCB3` (RFC 7253 names the mode OCB3; pyca `AESOCB3`); `3` was left over before.
- `ISO7816-4` padding: `ISO7816-4Padding` (JCA), `ISO7816d4Padding` (BouncyCastle), `ISO7816` (ISO/IEC 7816-4).
- java-jwt's factory names (`com.auth0.jwt.algorithms.Algorithm`): `Algorithm.RSA256/384/512` (RSASSA-PKCS1 with the
  digest), `Algorithm.ECDSA256/384/512` (ECDSA with the curve and digest, as JOSE ES256/384/512),
  `Algorithm.HMAC256/384/512`; `Algorithm.RSA256` gave RSA with key size 256 before.
- `NTRU-Prime`: `Streamlined NTRU Prime`, `sntrup` (the scheme's name in the NIST PQC round 3 submission; CBOMkit).

## 2026-10-23.1 (2026-10-23) fingerprint `90d5164e6c48ae1efcc06a5c231667ed9b541e7a13810553c6eab3cc120d9db0`
Four rules of the name parser, prompted by the review of corpus-pilot-medium (development set; `docs/findings.md`
"Export review 2026-10-23"). The rules live in `analysis/normalize/vocab.py` and `engine.py` (not fingerprinted), their
data in four new sections here; this entry is the record of the code change. Nothing a tool wrote is touched: every
rule bounds what OUR reading of a name adds.
- **Shapes before tokenising.** A UUID anywhere in a name carries nothing (a CryptobomForge component named by one
  gave `EC` with key size 15); an OID is one token, read through the new `oids:` table (RFC 8017, 5480, 5758, 3279,
  8410, 3370, 5754, 8018, 3565, 5084; the registry's curve OIDs come with the curves): qelens' `OID(1.2.840.113549.1.1.1)`
  is RSA, cdxgen's `ec-1.2.840.10045.3.1.7.p12` has curve secp256r1 and no key size 840; a URI names its algorithm in
  the fragment (RFC 6931: `http://www.w3.org/2001/04/xmldsig-more#rsa-sha256`), so 2001 is no key size. The `oid`
  field fills gaps the same way (`derived:oid`); `oid` joins the noise words.
- **A key size from a name must be plausible.** New `key_sizes:` per family or group, each with its standard (AES
  128/192/256 by FIPS 197, the RSA group 512..16384 by 8, DES 40/56/64, ...; a curve family takes a registry curve's
  size; a keyed family without an entry a multiple of 8 up to 16384), and the number must stand next to the family it
  belongs to: `ISO7816-4Padding` gives AES no key size 7816, `issue_369_rsa_pem.pub` RSA none, `Algorithm.RSA256`
  none, `rsa signpkcs1v15` none (40 of 292 key-size disagreements on pilot-medium were such artefacts). A tool's own
  parameterSetIdentifier is never checked (CBOMkit's `AES-96` stays its finding).
- **Parameters a primitive has.** New `admits:` per CycloneDX primitive: a value our own reading (a name parse, an
  API context, a derivation) gives a record for a parameter its primitive does not have is dropped
  (`engine.py:_admit`); a tool's field or label never is. CipherSift writes one component per part of
  `TLS_AES_128_GCM_SHA256` (families SHA-2 and AES); its SHA-2 component carried key size 128 and mode GCM, `ES512`
  under SHA-2 a curve and a derived key size 521 (on pilot-medium 396 key sizes, 356 curves, 39 paddings and 21 modes
  on hash records). On a hash, a parsed digest that refines the algorithm is the algorithm (SHA-2 + SHA-256 -> SHA-256).
- **Cipher-suite names** (new `cipher_suites:`): IANA `TLS_<kx>[_<auth>][_EXPORT]_WITH_<cipher>_<mac>`, TLS 1.3
  `TLS_<aead>_<hash>` (RFC 5246 A.5, RFC 8446 B.4) and OpenSSL's `[<kx>-][<auth>-]<cipher>-<mac>` (openssl-ciphers(1))
  yield the bulk cipher with its parameters and `protocol: TLS`; key exchange, authentication and MAC are no values of
  that record (CycloneDX models a suite as a protocol property referencing algorithms), `SHA` is SHA-1 (RFC 5246
  A.5), a NULL cipher leaves the MAC hash. Before, `TLS_DHE_DSS_EXPORT_WITH_DES40_CBC_SHA` gave FFDH with key size 40,
  mode CBC and digest SHA (CryptoScan's "Weak Cipher Suite", 228 pilot-medium records) and `TLS_RSA_WITH_3DES_EDE_CBC_SHA`
  RSASSA-PKCS1 without the 3DES.

## 2026-10-20.2 (2026-10-20) fingerprint `fb39aefee3a5a878efbca29fef7e47839caaec8fcc2f3f99795c0d582d867476`
- `cryptobomforge.yaml` (version 2): declares IBM's draft `1.4-cbom-1.0` only. The CycloneDX entry (1.6, then 1.7 in
  2026-10-20.1) covered the worker's own empty placeholder CBOM for a CodeQL run without results; that placeholder is
  gone (cryptobom-forge writes its own empty draft document for that case, checked in the pinned image; refinement 15:
  every stored CBOM is the tool's). Stored placeholder runs are redone with `--rerun cryptobomforge`. The notes drop a
  stale sentence (the detectionContext windows are read, as line and line_end). The shared vocabulary is unchanged.

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
