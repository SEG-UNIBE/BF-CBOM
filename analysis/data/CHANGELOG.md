# Vocabulary and adapter specs: change log

The shared vocabulary (`algorithms.yaml`) and the adapter specs (`../adapters/*.yaml`) are frozen (CLAUDE.md
refinement 14): they were developed on the pilot corpus (the development set); the main corpus is held out. Every
change bumps `version` in `algorithms.yaml`, adds an entry here with the fingerprint `python -m analysis.freeze`
prints and the reason, and the tests fail otherwise (`tests/test_vocab_frozen.py`). Newest entry first.

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
