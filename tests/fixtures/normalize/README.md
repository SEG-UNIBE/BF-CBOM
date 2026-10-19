# Normaliser fixtures

Small excerpts (a few components each, occurrences trimmed to two or three) of real CBOMs from experiment
`e1-pilot-corpus` (16 pinned projects, run on 2026-10-16), and complete CBOMs of the observer tools on one micro
project each. Each file names its source snapshots in
`metadata.properties` (`bf-cbom:fixture-origin`). They pin the per-tool quirks the adapter specs in
`analysis/adapters/` handle; `tests/test_normalize.py` uses them.

| File | Tool (version) | What it exercises |
|---|---|---|
| `cryptoscan.json` | CryptoScan 1.4.0 | per-occurrence `symbol` (`KeySize = 2048`, `iterations = 5`, `TLSv1.2`, `ed25519`), a multi-algorithm category name, a certificate check |
| `cbomkit-jar.json` | cbomkit-action v2.3.0 | `dependencies`: RSA-OAEP -> SHA-256 (digest), salt -> HKDF (key family), salt sizes |
| `cipherradar.json` | CipherRadar efa5d9d | `algorithmFamily` contradicting the name (SHA-1 labelled SHA-2), `algorithmRef` / `signatureAlgorithmRef`, a crypto library import |
| `qdarpan.json` | qdarpan 0.1.0 | `qdarpan:family`, crypto library components (`jca`, `crypto/rsa`) |
| `securin-xbom.json` | xbom 0.1.0 | out-of-spec primitive `pk`, related material of type `prng` |
| `ciphersift.json` | CipherSift v1.1.1 (on `corpus/micro`, 2026-10-17) | family from `ciphersift:family` where `algorithmFamily` is absent, API names (`crypto/ecdsa.GenerateKey`, `hmac.new`), a Go module library |
| `scanoss.json` | crypto-finder 0.37.1, open rules (on `corpus/micro` go and c, 2026-10-17) | registry and API spellings (`HMAC-sha512`, `RSA-OAEP-sha256`, `des_ede3_cbc`, `OpenSSL-RAND`, `PBKDF2---`), AES key length in bytes |
| `qelens.json` | qelens cbom 0.1.2 (on `corpus/micro/python`, 2026-10-19; complete) | absolute paths under the clone, category names (`HASHING (see usage)`), a lockfile dependency |
| `cryptarium.json` | cryptarium 0.3.0 (on `corpus/micro/c`, 2026-10-19; complete) | a CBOM without locations, `UNKNOWN` names, AES with primitive `ae` |
| `qscan.json` | qScan 0.12.0 (on `corpus/micro/python`, 2026-10-19; complete) | `location: <file>:<line>`, names `<algorithm> (<primitive>)`, the algorithm in parameterSetIdentifier |
| `deepseek.json` | deepseek-chat (our LLM worker) | `assetType: key`, padding `PSS`, mode `KW`, `bf-cbom:digest` |
| `cryptobomforge-empty.json` | cryptobom-forge 1.1.0 (on a CodeQL SARIF with zero results, 2026-10-20; complete) | the tool's own empty draft document (no `components` key): 0 records, no error, non-CycloneDX in RQ1 |

The xgrep test uses hand-written CBOMs in the tool's format.
