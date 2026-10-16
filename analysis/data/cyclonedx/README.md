# CycloneDX reference files (vendored)

Unmodified copies from the CycloneDX specification repository, pinned to one commit so that the
normaliser (and later the RQ1 schema validation) give the same answer on every machine.

- Source: https://github.com/CycloneDX/specification, directory `schema/`
- Commit: `ad83dae28b236b8760286399da4daba77592de86` (master on 2026-10-16)
- Licence: Apache-2.0 (`LICENSE`, copied from the same commit)

| File | Used for | sha256 |
|---|---|---|
| `cryptography-defs.json` | CycloneDX 1.7 cryptography registry (`lastUpdated` 2026-02-24): algorithm families with name patterns and primitives, elliptic curves with OIDs and aliases. Canonical vocabulary of `analysis/normalize/vocab.py`. | `681c1bb9…6939` |
| `cryptography-defs.schema.json` | 1.7 enums `algorithmFamiliesEnum` and `ellipticCurvesEnum` (referenced by `bom-1.7.schema.json`) | `027b059a…5b44` |
| `bom-1.6.schema.json`, `bom-1.7.schema.json` | the official JSON schemas; the normaliser checks every value it reads against the version a CBOM declares (`analysis/normalize/conformance.py`) | `18f57f74…5b37d`, `73308ede…76ce` |
| `spdx.schema.json`, `jsf-0.82.schema.json` | referenced by the bom schemas; needed for full validation (RQ1) | `4b345e23…b47b359`, `8bae002c…0aae` |

Crypto fields (`cryptoProperties`, component type `cryptographic-asset`) exist in CycloneDX only since 1.6, so
older schemas are not vendored. IBM's pre-standard `1.4-cbom-1.0` (CryptobomForge) is a separate dialect without
an official schema here.

To bump: download the files of a newer commit, update the commit and hashes above and the versions recorded in
`analysis/data/algorithms.yaml`, rerun the tests and `make analysis ARGS="normalize <exp>"`.
