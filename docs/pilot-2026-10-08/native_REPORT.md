# Agreement-without-ground-truth test, native corpus (Go / C / JS-TS), 2026-10-08

Scripts: work/compare/native/{normalize,cluster,gt_validate,sample,analyze}.py, judgments in handcheck_judgments.py (seed 20261008).
Outputs: normalized.jsonl (6738 findings), clusters.jsonl (3035), cluster_report.txt, gt_validation.txt, handcheck_sample.jsonl, handcheck.csv (93 rows), handcheck_results.txt, missed_grep_stats.json.

## Normalization decisions
- Kept only algorithm assets in primary-language source files (Go .go, C .c/.h, JS .js/.ts/...). Dropped: certificate/protocol/key-material assets (xgrep 180, cipherradar 636, scanoss 375, cbomkit 4), non-source files (cipherradar 633: PEM, shell, docs; xgrep 11), library/import components (pqcheck 1280, cipherradar 365, q-darpan 489), exact duplicates (152).
- Unmappable family -> "unknown" (matches anything): SCANOSS "OTR" 79 (systematic FP rule), CipherRadar rule ids EVP-Digest/EVP-DigestSign/EVP-Encrypt/EVP-PKEY/cipher-spec-inventory 17, pqcheck KEYGEN 1. EC/ECDSA/ECDH/EdDSA/X25519 are one family "EC" (so ECDSA-vs-ECDH mislabels still match).
- Clusters: same file, |dline|<=2, same family; chained runs >6 lines split into 3-line windows (35 splits); unknowns attach to nearest cluster.

## k distribution (all voters / without failed-grade cipherradar-C + scanoss-JS)
Go 1249/233/119/437 (k=1/2/3/4+); C 634/37/17/0 (core: 74/30/0); JS 271/35/3/0 (core 270/38/0). Overall 71% of clusters are solo.
Pairwise Jaccard (Go): pqcheck-q-darpan 0.55, xgrep-pqcheck 0.51, xgrep-q-darpan 0.48 (tree-sitter/types call-site tools agree); cbomkit-cipherradar 0.09, cipherradar-scanoss 0.17 (phi -0.33).
The two OpenGrep tools are NOT positively correlated overall (CipherRadar's huge string/constant solo set dominates), but 55/291 k=2 clusters are confirmed only by that pair, and those are the worst confirmations (3/8 real uses vs 12/17 other pairs).

## Hand-check (precision = real crypto use / sampled clusters)
| stratum | n | real use | use+right family | use or related mention |
|---|---|---|---|---|
| k=1 | 23 | 9 (39%) | 7 (30%) | 21 (91%) |
| k=2 | 25 | 15 (60%) | 15 (60%) | 24 (96%) |
| k>=3 | 25 | 24 (96%) | 24 (96%) | 25 (100%) |
By k_core: 46% / 69% / 95%. Non-test only: 44% / 47% / 94%.
Per tool SOLO vs CONFIRMED real-use precision: xgrep 1/4 vs 25/29; cbomkit 4/4 vs 7/7 (but 2/4 solos wrong family: Ed25519 keygen as SHA-512; solo cbomkit = rand.Reader-as-PRNG on lines others call ECDSA); cipherradar 0/6 vs 34/43; pqcheck 2/4 vs 18/20; q-darpan 1/1 vs 19/20; scanoss 1/4 vs 22/29.
Low-k findings are mostly crypto *mentions* (registry tables, switch cases, OID/cipher-suite strings, JWK fixtures, test-case constants), not false hits on non-crypto code.

## Micro GT
All 3 micros: k>=2 clusters 100% correct (33/33); k=1 11/13. Agreement score does not reproduce GT ranking: micro-go everything ~1.0 (uninformative; recall 8-10/11 not reflected); micro-js agreement ranks scanoss last (0.33) though it has precision 1.00; micro-c gives failed-grade CipherRadar (precision 0.40) an agreement score of 0.80, nearly level with SCANOSS (0.83, precision 1.00), because its "unknown" EVP-* findings piggyback on others' clusters.
GT assets missed by every tool: JS ECDSA sign (alg via constant); C RSA keygen (EVP_RSA_gen), EC keygen (EVP_EC_gen), ECDSA via EVP_DigestSign, RSA-OAEP.

## Missed by all
Grep of obvious APIs: Go 521 hits, 1 uncovered (a string). C openssh 181/223 uncovered, curl 11/19. JS jose 150/209 (dist bundles 0 clusters; src 25/32 WebCrypto calls uncovered: subtle.sign/verify/deriveBits/wrapKey/generateKey with algorithm in a variable), parse-server 0/11.
Sample of 20: 7 real uses missed (openssh EVP_PKEY_keygen for ECDSA; jose subtle.deriveBits, subtle.decrypt AES-CBC, subtle.sign HMAC, subtle.digest, generateKeyPair x2), 1 companion line of a covered use, 8 key-plumbing (EC_KEY_dup, RSA_new, importKey...), 4 not uses (RSA_free/EC_KEY_free, string).
