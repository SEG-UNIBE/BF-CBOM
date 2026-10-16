# Normaliser fixtures

Small excerpts (a few components each, occurrences trimmed to two or three) of real CBOMs from experiment
`e1-pilot-corpus` (16 pinned projects, run on 2026-10-16). Each file names its source snapshots in
`metadata.properties` (`bf-cbom:fixture-origin`). They pin the per-tool quirks the adapter specs in
`analysis/adapters/` handle; `tests/test_normalize.py` uses them.

| File | Tool (version) | What it exercises |
|---|---|---|
| `cryptoscan.json` | CryptoScan 1.4.0 | per-occurrence `symbol` (`KeySize = 2048`, `iterations = 5`, `TLSv1.2`, `ed25519`), a multi-algorithm category name, a certificate check |
| `cbomkit-jar.json` | cbomkit-action v2.3.0 | `dependencies`: RSA-OAEP -> SHA-256 (digest), salt -> HKDF (key family), salt sizes |
| `cipherradar.json` | CipherRadar efa5d9d | `algorithmFamily` contradicting the name (SHA-1 labelled SHA-2), `algorithmRef` / `signatureAlgorithmRef`, a crypto library import |
| `qdarpan.json` | qdarpan 0.1.0 | `qdarpan:family`, crypto library components (`jca`, `crypto/rsa`) |
| `securin-xbom.json` | xbom 0.1.0 | out-of-spec primitive `pk`, related material of type `prng` |
| `deepseek.json` | deepseek-chat (our LLM worker) | `assetType: key`, padding `PSS`, mode `KW`, `bf-cbom:digest` |

Only open-source tools (and our own worker) appear here. xgrep's terms reportedly forbid publishing its results and
Crypsy's adaptation has no licence yet, so their tests use hand-written CBOMs in the tools' format.
