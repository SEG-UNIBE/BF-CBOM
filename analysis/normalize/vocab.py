"""Shared vocabulary of the normaliser: canonical names, per-field canonicalisation, name and API parsing.

Canonical names come from the CycloneDX 1.7 cryptography registry (`analysis/data/cyclonedx/cryptography-defs.json`)
plus the hand-reviewed `analysis/data/algorithms.yaml` (groups, additions, aliases, whole names, API table). Spellings
only one tool uses live in that tool's adapter spec (`analysis/adapters/<tool>.yaml`), not here.

`Vocabulary.parse` reads composite names, scanner symbols and rule texts into dimensions:
"AES-256-GCM" -> family AES, key_size 256, mode GCM; "SHA256withECDSA" -> family ECDSA, digest SHA-256;
"RSA/ECB/OAEPWithSHA-256AndMGF1Padding" -> family RSAES-OAEP, mode ECB, padding OAEP, digest SHA-256.
It never guesses: tokens it does not know are returned as `unmapped`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"
VOCAB_FILE = DATA / "algorithms.yaml"
REGISTRY_FILE = DATA / "cyclonedx" / "cryptography-defs.json"

# Dimensions a parse or an API lookup can set.
DIMS = (
    "family", "algorithm", "group", "primitive", "functions", "mode", "padding", "curve", "key_size", "digest",
    "param_set", "iterations", "protocol", "protocol_version", "material", "asset_type",
)  # fmt: skip
NO_INFO = frozenset({"other", "unknown", "none", "null", "n/a", ""})
# Families that name a key type or a whole group without a scheme: a scheme of the same group refines them.
UMBRELLA_FAMILIES = frozenset({"RSA", "EC", "CSPRNG", "PBE"})
UMBRELLA_ALGORITHMS = frozenset({"SHA-2", "SHA-3", "SHAKE", "BLAKE2"})
# Same curve in another form (Montgomery vs twisted Edwards); tools use the names interchangeably.
EQUIVALENT_CURVES = frozenset({frozenset({"Curve25519", "Ed25519"}), frozenset({"Curve448", "Ed448"})})
_CURVE_CATEGORY_ORDER = ("secg", "nist", "x962", "brainpool")
_MAX_WINDOW = 6
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
_UUID_SUFFIX = re.compile(r"@[0-9a-fA-F-]{8,}.*$")
_PARENS = re.compile(r"\([^)]*\)")


def fold(text) -> str:
    """Case- and separator-insensitive key: "SHA-256" == "sha256" == "Sha_256"."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def tokenize(text: str) -> list[str]:
    """Split on separators, camelCase and letter/digit boundaries: "HmacSHA512" -> Hmac, SHA, 512."""
    out: list[str] = []
    for part in _SEPARATORS.split(text):
        if part:
            out.extend(p for p in _CAMEL.split(part) if p)
    return out


@dataclass(frozen=True)
class FamilyInfo:
    name: str
    group: str | None
    primitive: str | None
    registry: bool
    param: str  # key | curve | digest | none


@dataclass(frozen=True)
class Term:
    kind: str  # family | algorithm | curve | mode | padding | protocol | protocol_version | material | name
    value: str
    data: tuple = ()  # protocol_version: (protocol, version)


@dataclass
class Parsed:
    values: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)  # dim -> the text that produced it
    unmapped: list = field(default_factory=list)  # tokens nobody knows
    candidates: list = field(default_factory=list)  # families of an unresolved list ("RSA, ECC, AES")
    known: bool = False  # recognised: at least one value, or an explicit "carries no algorithm" entry

    @property
    def empty(self) -> bool:
        return not self.values


class Vocabulary:
    def __init__(self, vocab_file: Path = VOCAB_FILE, registry_file: Path = REGISTRY_FILE):
        data = yaml.safe_load(Path(vocab_file).read_text(encoding="utf-8")) or {}
        registry = json.loads(Path(registry_file).read_text(encoding="utf-8"))
        self.version = str(data.get("version") or "?")
        self.registry_version = str(registry.get("lastUpdated") or "?")
        self._load_families(data, registry)
        self._load_algorithms(data)
        self._load_curves(data, registry)
        self.mode_alias = self._enum(data.get("modes") or {})
        self.padding_alias = self._enum(data.get("paddings") or {})
        self.primitive_alias = self._enum(data.get("primitives") or {})
        self.function_alias = self._enum(data.get("functions") or {})
        self.material_alias = self._enum(data.get("materials") or {})
        self.asset_type_alias = self._enum(data.get("asset_types") or {})
        self.protocol_alias = self._enum(data.get("protocols") or {})
        self.protocol_versions = {
            fold(k): (str(v[0]), str(v[1])) for k, v in (data.get("protocol_versions") or {}).items()
        }
        self.names = {fold(k): dict(v or {}) for k, v in (data.get("names") or {}).items()}
        self.keystores = {fold(t) for t in data.get("keystores") or []}
        self.formats = {fold(t) for t in data.get("formats") or []}
        self.noise = {fold(t) for t in data.get("noise") or []}
        self.number_keywords = {
            fold(word): dim for dim, words in (data.get("number_keywords") or {}).items() for word in words
        }
        self.apis = sorted(
            ((self._api_key(k), dict(v or {})) for k, v in (data.get("apis") or {}).items()),
            key=lambda kv: -len(kv[0]),
        )
        self._build_token_index()

    # ----- loading -----

    def _load_families(self, data: dict, registry: dict) -> None:
        group_of: dict[str, str] = {}
        for group, members in (data.get("groups") or {}).items():
            for m in members:
                group_of[str(m)] = str(group)
        param_of: dict[str, str] = {}
        for sem, members in (data.get("param") or {}).items():
            for m in members:
                param_of[str(m)] = str(sem)
        self.families: dict[str, FamilyInfo] = {}
        for entry in registry.get("algorithms") or []:
            name = str(entry["family"])
            variants = entry.get("variant") or []
            prim = variants[0].get("primitive") if variants else None
            self.families[name] = FamilyInfo(name, group_of.get(name), prim, True, param_of.get(name, "none"))
        for name, info in (data.get("additions") or {}).items():
            self.families[str(name)] = FamilyInfo(
                str(name),
                group_of.get(str(name)),
                (info or {}).get("primitive"),
                False,
                param_of.get(str(name), "none"),
            )
        self.registry_families = frozenset(n for n, f in self.families.items() if f.registry)
        self.family_alias: dict[str, str] = {fold(n): n for n in self.families}
        for canonical, aliases in (data.get("families") or {}).items():
            for a in aliases or []:
                self.family_alias[fold(a)] = str(canonical)

    def _load_algorithms(self, data: dict) -> None:
        self.algorithms: dict[str, dict] = {}
        self.algorithm_alias: dict[str, str] = {}
        for name, info in (data.get("algorithms") or {}).items():
            info = dict(info or {})
            self.algorithms[str(name)] = info
            self.algorithm_alias[fold(name)] = str(name)
            for a in info.get("aliases") or []:
                self.algorithm_alias[fold(a)] = str(name)
        self.hashes = frozenset(n for n, i in self.algorithms.items() if i.get("hash"))

    def _load_curves(self, data: dict, registry: dict) -> None:
        by_oid: dict[str, list[tuple[str, str]]] = {}
        forms: dict[str, str | None] = {}
        singles: list[str] = []
        for cat in registry.get("ellipticCurves") or []:
            for c in cat.get("curves") or []:
                name = str(c["name"])
                forms[name] = c.get("form")
                if c.get("oid"):
                    by_oid.setdefault(str(c["oid"]), []).append((str(cat["name"]), name))
                else:
                    singles.append(name)
        self.curve_alias: dict[str, str] = {}
        for members in by_oid.values():
            ranked = sorted(
                members,
                key=lambda m: (_CURVE_CATEGORY_ORDER.index(m[0]) if m[0] in _CURVE_CATEGORY_ORDER else 99, m[1]),
            )
            canonical = ranked[0][1]
            for _, name in members:
                self.curve_alias.setdefault(fold(name), canonical)
        for name in singles:
            self.curve_alias.setdefault(fold(name), name)
        for canonical, aliases in (data.get("curves") or {}).items():
            self.curve_alias[fold(canonical)] = str(canonical)
            for a in aliases or []:
                self.curve_alias[fold(a)] = str(canonical)
        self.curve_sizes: dict[str, int] = {}
        for name in set(self.curve_alias.values()):
            m = re.search(r"(\d{3})", name)
            if m and forms.get(name, "Weierstrass") == "Weierstrass" and re.match(r"(secp|brainpoolP|P-)", name):
                self.curve_sizes[name] = int(m.group(1))

    @staticmethod
    def _enum(section: dict) -> dict[str, str]:
        out: dict[str, str] = {}
        for canonical, aliases in section.items():
            out[fold(canonical)] = str(canonical)
            for a in aliases or []:
                out[fold(a)] = str(canonical)
        return out

    @staticmethod
    def _api_key(text: str) -> str:
        t = re.sub(r"\(.*$", "", str(text)).replace("<init>", "")
        return re.sub(r"[#/:]+", ".", t).strip(".").lower()

    def _build_token_index(self) -> None:
        """Token-sequence keys -> terms, in priority order (later kinds never overwrite earlier ones)."""
        idx: dict[str, Term] = {}

        def add(key: str, term: Term) -> None:
            if key and key not in idx:
                idx[key] = term

        for k, (proto, ver) in self.protocol_versions.items():
            add(k, Term("protocol_version", ver, (proto, ver)))
        for k in self.names:  # JOSE/SSH/JCA identifiers also inside longer names ("RS256.pub.pem")
            if k not in self.noise:  # generic words ("key", "none") only count as a whole name
                add(k, Term("name", k))
        for k in self.keystores:
            add(k, Term("keystore", "CERT"))
        for k in self.formats:
            add(k, Term("format", k))
        for k, v in self.algorithm_alias.items():
            add(k, Term("algorithm", v))
        for k, v in self.family_alias.items():
            add(k, Term("family", v))
        for k, v in self.curve_alias.items():
            add(k, Term("curve", v))
        for k, v in self.mode_alias.items():
            add(k, Term("mode", v))
        for k, v in self.padding_alias.items():
            add(k, Term("padding", v))
        for k, v in self.protocol_alias.items():
            add(k, Term("protocol", v))
        for k, v in self.material_alias.items():
            if k not in ("digest", "signature", "tag", "key"):  # too generic inside names
                add(k, Term("material", v))
        self.token_index = idx
        self._lexicon = sorted(
            {k for k in idx if k.isalpha() and len(k) >= 2}
            | {k for k in self.noise if k.isalpha() and len(k) >= 3}
            | {"sha", "md", "rc", "with", "and", "hmac", "pbe"},
            key=len,
            reverse=True,
        )

    # ----- lookups -----

    def family_info(self, family: str | None) -> FamilyInfo | None:
        return self.families.get(family) if family else None

    def group_of(self, family: str | None) -> str | None:
        info = self.family_info(family)
        return info.group if info else None

    def family_of_algorithm(self, algorithm: str | None) -> str | None:
        info = self.algorithms.get(algorithm) if algorithm else None
        return info.get("family") if info else None

    def curve_size(self, curve: str | None) -> int | None:
        return self.curve_sizes.get(curve) if curve else None

    def standard_name(self, text: str) -> str | None:
        """The algorithm (or family) a WHOLE name stands for in the vocabulary, without splitting it into parts:
        "DESede" -> 3DES, "HS256" -> HMAC, "SHA256" -> SHA-256, "AES-256-GCM" -> None (level L2, analysis/levels.py)."""
        key = fold(clean_name(text))
        if key in self.names:
            values = self._expand(dict(self.names[key]))
            return values.get("algorithm") or values.get("family")
        return self.algorithm_alias.get(key) or self.family_alias.get(key)

    def relation(self, dim: str, current, new) -> str | None:
        """How a later value relates to the current one: `same`, `refines` (more specific: EC -> ECDSA,
        SHA-2 -> SHA-256), `coarser` (the reverse, or an equivalent spelling) or None (a real conflict)."""
        if current == new or (isinstance(current, tuple) and set(current) == set(new or ())):
            return "same"
        if dim == "family":
            gc, gn = self.group_of(current), self.group_of(new)
            if gc and gc == gn:
                if current in UMBRELLA_FAMILIES and new not in UMBRELLA_FAMILIES:
                    return "refines"
                if new in UMBRELLA_FAMILIES:
                    return "coarser"
        if dim in ("algorithm", "digest"):
            fc, fn = self.family_of_algorithm(current), self.family_of_algorithm(new)
            if fc and fc == fn:
                if current == fc or current in UMBRELLA_ALGORITHMS:
                    return "refines"
                if new == fn or new in UMBRELLA_ALGORITHMS:
                    return "coarser"
        if dim == "curve" and frozenset((current, new)) in EQUIVALENT_CURVES:
            return "coarser"
        return None

    def canonical(self, dim: str, raw):
        """Canonical value of one field, or None (unknown spelling or a CycloneDX no-information value)."""
        if raw is None:
            return None
        if dim in ("key_size", "iterations"):
            return _to_int(raw)
        if dim == "functions":
            vals = raw if isinstance(raw, list | tuple) else [raw]
            found = [self.function_alias.get(fold(v)) for v in vals]
            return tuple(sorted({f for f in found if f})) or None
        text = str(raw).strip()
        if text.lower() in NO_INFO:
            return None
        key = fold(text)
        if dim == "family":
            return self.family_alias.get(key) or self.family_of_algorithm(self.algorithm_alias.get(key))
        if dim == "algorithm":
            return self.algorithm_alias.get(key)
        if dim == "digest":
            alg = self.algorithm_alias.get(key)
            return alg if alg in self.hashes else None
        if dim == "curve":
            return self.curve_alias.get(key) or self.curve_alias.get(fold(text.rsplit("/", 1)[-1]))
        if dim == "group":
            groups = {g.group for g in self.families.values() if g.group} | {"TLS", "CERT"}
            return next((g for g in groups if fold(g) == key), None)
        alias = {
            "mode": self.mode_alias,
            "padding": self.padding_alias,
            "primitive": self.primitive_alias,
            "material": self.material_alias,
            "asset_type": self.asset_type_alias,
            "protocol": self.protocol_alias,
        }.get(dim)
        if alias is not None:
            return alias.get(key)
        if dim == "protocol_version":
            m = re.fullmatch(r"v?(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
            return m.group(1) if m else None
        return text  # oid, purl, confidence, param_set: kept as reported

    def api(self, text: str | None) -> Parsed:
        """Values of the longest API prefix that `text` starts with (`javax.crypto.Mac#getInstance` -> mac)."""
        if not text:
            return Parsed()
        key = self._api_key(text)
        for prefix, values in self.apis:
            if key == prefix or key.startswith(prefix + "."):
                vals = self._expand(dict(values))
                return Parsed(values=vals, raw={d: prefix for d in vals}, known=True)
        return Parsed()

    # ----- parsing -----

    def parse(self, text) -> Parsed:
        return _parse_cached(self, str(text) if text is not None else "")

    def _parse(self, text: str) -> Parsed:
        cleaned = _clean(text)
        if not cleaned:
            return Parsed()
        whole = self.names.get(fold(cleaned))
        if whole is not None:
            vals = self._expand(dict(whole))
            return Parsed(values=vals, raw={d: cleaned for d in vals}, known=True)
        parts = [p for p in re.split(r"\s*[,;]\s*", cleaned) if p] if re.search(r"[,;]", cleaned) else [cleaned]
        if len(parts) > 1:
            subs = [self._parse_one(p) for p in parts]
            fams = sorted({s.values.get("family") for s in subs if s.values.get("family")})
            if len(fams) <= 1:
                merged = Parsed(known=any(s.known for s in subs))
                for s in subs:
                    for k, v in s.values.items():
                        merged.values.setdefault(k, v)
                        merged.raw.setdefault(k, s.raw.get(k))
                    merged.unmapped += s.unmapped
                return merged
            return Parsed(candidates=fams, known=True, unmapped=[u for s in subs for u in s.unmapped])
        return self._parse_one(cleaned)

    def _segment(self, token: str) -> list[str] | None:
        """Split a run-together word into known pieces ("HMACSHA" -> hmac, sha); None if it does not split fully."""
        word = fold(token)
        out: list[str] = []
        i = 0
        while i < len(word):
            piece = next((p for p in self._lexicon if word.startswith(p, i)), None)
            if piece is None:
                return None
            out.append(piece)
            i += len(piece)
        return out if len(out) > 1 else None

    def _parse_one(self, text: str) -> Parsed:
        tokens: list[str] = []
        for tok in tokenize(text):
            f = fold(tok)
            if f and not f.isdigit() and f not in self.token_index and f not in self.noise:
                seg = self._segment(tok)
                if seg:
                    tokens.extend(seg)
                    continue
            tokens.append(tok)
        found: list[tuple[Term, str]] = []
        numbers: list[tuple[int, str | None, str]] = []
        unmapped: list[str] = []
        pending: str | None = None
        i = 0
        while i < len(tokens):
            for w in range(min(_MAX_WINDOW, len(tokens) - i), 0, -1):
                key = "".join(fold(t) for t in tokens[i : i + w])
                if key in self.number_keywords:  # "KeySize = 2048", "key_size=2048"
                    pending = self.number_keywords[key]
                    i += w
                    break
                term = self.token_index.get(key)
                if term is not None and not (w == 1 and key.isdigit()):
                    found.append((term, "-".join(tokens[i : i + w])))
                    i += w
                    break
            else:
                tok = tokens[i]
                f = fold(tok)
                if f.isdigit():
                    numbers.append((int(f), pending, tok))
                    pending = None
                elif f in self.number_keywords:
                    pending = self.number_keywords[f]
                elif f and f not in self.noise:
                    unmapped.append(tok)
                i += 1
        return self._assemble(found, numbers, unmapped)

    def _assemble(self, found: list[tuple[Term, str]], numbers: list, unmapped: list[str]) -> Parsed:
        out = Parsed(unmapped=list(unmapped))
        vals, raw = out.values, out.raw

        def put(dim: str, value, src: str) -> None:
            if value is not None and dim not in vals:
                vals[dim] = value
                raw[dim] = src

        for term, src in found:  # whole identifiers found inside the text (JOSE, SSH, JCA)
            if term.kind == "name":
                for dim, value in self._expand(dict(self.names[term.value])).items():
                    put(dim, value, src)
        algos = [(t, s) for t, s in found if t.kind in ("algorithm", "family")]
        secondary = [(t, s) for t, s in algos if t.kind == "algorithm" and self.algorithms[t.value].get("secondary")]
        algos = [a for a in algos if a not in secondary]
        is_hash = [t.kind == "algorithm" and t.value in self.hashes for t, _ in algos]
        primary = next((a for a, h in zip(algos, is_hash, strict=True) if not h), None)
        hashes = [a for a, h in zip(algos, is_hash, strict=True) if h]
        if primary is None and hashes:
            primary, hashes = hashes[0], hashes[1:]
        if primary is None and secondary:
            primary = secondary[0]
        if primary is not None:
            term, src = primary
            if term.kind == "algorithm":
                put("algorithm", term.value, src)
                put("family", self.family_of_algorithm(term.value), src)
            else:
                put("family", term.value, src)
                # "EdDSA-Ed25519": a later variant of the same family refines the algorithm
                for t, s in algos:
                    if t.kind == "algorithm" and self.family_of_algorithm(t.value) == term.value:
                        put("algorithm", t.value, s)
                        break
            if vals.get("family") == "PBE" and any(t.value == "HMAC" for t, _ in algos):
                vals["family"] = "PBES2"
            elif vals.get("family") == "PBE" and hashes:
                vals["family"] = "PBES1"
        if hashes:
            put("digest", hashes[0][0].value, hashes[0][1])
        for term, src in found:
            if term.kind in ("curve", "mode", "padding", "material", "protocol"):
                put(term.kind, term.value, src)
            elif term.kind == "protocol_version":
                put("protocol", term.data[0], src)
                put("protocol_version", term.data[1], src)
        algo_info = self.algorithms.get(vals.get("algorithm") or "", {})
        for dim in ("curve", "primitive", "param_set"):
            if algo_info.get(dim) is not None:
                put(dim, str(algo_info[dim]), raw.get("algorithm", ""))
        if "family" not in vals and "curve" in vals:
            put("family", "EC", raw["curve"])
        if "family" not in vals and any(t.kind == "keystore" for t, _ in found):
            put("group", "CERT", next(s for t, s in found if t.kind == "keystore"))
        self._numbers(numbers, put, out)
        self._refine_rsa(vals)
        out.known = bool(vals) or any(t.kind == "name" for t, _ in found)
        return out

    def _numbers(self, numbers: list, put, out: Parsed) -> None:
        """Bare numbers: key size of a keyed family, the variant of SHA-2/SHA-3, else a parameter set."""
        fam = out.values.get("family")
        info = self.family_info(fam)
        sem = info.param if info else "none"
        for n, keyword, tok in numbers:
            if keyword:
                put(keyword, n, tok)
            elif sem in ("key", "curve") and "key_size" not in out.values and 8 <= n <= 16384:
                put("key_size", n, tok)
            elif fam in ("SHA-2", "SHA-3") and out.values.get("algorithm") in (None, "SHA-2", "SHA-3"):
                name = f"SHA-{n}" if fam == "SHA-2" else f"SHA3-{n}"
                if name in self.algorithms:
                    out.values["algorithm"] = name
                    out.raw["algorithm"] = tok
                else:
                    out.unmapped.append(tok)
            elif info is not None and sem == "none" and "param_set" not in out.values:
                put("param_set", str(n), tok)
            else:
                out.unmapped.append(tok)

    @staticmethod
    def _refine_rsa(vals: dict) -> None:
        """RSA plus a padding or a digest in the same name names a scheme (registry families)."""
        if vals.get("family") != "RSA":
            return
        padding = vals.get("padding")
        if padding == "OAEP":
            vals["family"] = "RSAES-OAEP"
        elif padding == "PSS":
            vals["family"] = "RSASSA-PSS"
        elif vals.get("digest"):
            vals["family"] = "RSASSA-PKCS1"
        elif padding == "PKCS1v15":
            vals["family"] = "RSAES-PKCS1"
        if vals["family"] != "RSA" and vals.get("algorithm") == "RSA":
            vals["algorithm"] = vals["family"]

    def _expand(self, values: dict) -> dict:
        """Fill the family of an algorithm given by a whole-name or API entry."""
        if values.get("algorithm") and not values.get("family"):
            fam = self.family_of_algorithm(values["algorithm"])
            if fam:
                values["family"] = fam
            info = self.algorithms.get(values["algorithm"], {})
            for dim in ("curve", "primitive", "param_set"):
                if info.get(dim) is not None:
                    values.setdefault(dim, str(info[dim]))
        if "functions" in values and not isinstance(values["functions"], tuple):
            values["functions"] = tuple(sorted(values["functions"]))
        return values


def _to_int(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    m = re.fullmatch(r"\s*(\d{1,6})\s*(bits?)?\s*", str(raw), flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def clean_name(text: str) -> str:
    """Drop `@<uuid>` suffixes and parenthesised details: "X.509 Certificate (example.com)" -> "X.509 Certificate"."""
    return _clean(text)


def _clean(text: str) -> str:
    t = _UUID_SUFFIX.sub("", str(text))
    t = _PARENS.sub(" ", t)
    return " ".join(t.split()).strip(" -_:")


@lru_cache(maxsize=200_000)
def _parse_cached(vocab: Vocabulary, text: str) -> Parsed:
    return vocab._parse(text)


@lru_cache(maxsize=4)
def load_vocabulary(vocab_file: str | None = None, registry_file: str | None = None) -> Vocabulary:
    return Vocabulary(
        Path(vocab_file) if vocab_file else VOCAB_FILE, Path(registry_file) if registry_file else REGISTRY_FILE
    )
