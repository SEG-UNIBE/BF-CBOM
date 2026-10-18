"""Read one CBOM with its tool's adapter spec into normalised records (CBOM^norm).

Per component the spec's sources are evaluated in order of precedence; each yields values (`Val`) for some
dimensions, with the raw value, the exact source path and how it was mapped. The first value per dimension wins; a
later, more specific value of the same kind refines it (EC -> ECDSA); a later different one is kept as a conflict,
unless it is only supporting evidence (API context, rule id, dependencies). One record is made per (component,
location); a component without a location gives one record without location. Every value read from a standard field
is checked against the CycloneDX version the CBOM declares (`conformance.py`), so lenient reading never hides a
deviation.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass

from .conformance import document_issues, schema_index
from .locations import CODE_EXT, relative_location
from .record import NormalizedCbom, NormRecord
from .specs import AdapterSpec, SourceDef, load_adapter
from .vocab import NO_INFO, Parsed, Vocabulary, load_vocabulary, rule_id_to_text

_PATH_LINE = re.compile(r"^(.*?)(?::(\d+)(?::\d+)?|#L?(\d+))$")
CONTEXT_MAX = 300
_AE_MODES = frozenset({"GCM", "CCM", "OCB", "SIV", "GCM-SIV", "EAX"})
_WRAP_MODES = frozenset({"KW", "KWP"})
_REPO_MARKERS = ("/repo/",)
_PSEUDO = frozenset({"param", "ref", "size"})  # resolved once family and material are known
_KEY_MATERIAL = frozenset({None, "private-key", "public-key", "secret-key", "key"})
# A field value the vocabulary does not know is parsed as a name ("OAEPWithSHA-256AndMGF1Padding"); which other
# dimensions such a parse may add depends on the field it came from.
_REPARSE_EXTRA = {
    "family": frozenset(
        {"algorithm", "digest", "curve", "key_size", "mode", "padding", "protocol", "protocol_version"}
    ),
    "algorithm": frozenset({"family", "digest", "curve", "key_size", "mode", "padding"}),
    "padding": frozenset({"digest"}),
    "mode": frozenset({"key_size"}),
    "curve": frozenset(),
    "digest": frozenset(),
    "primitive": frozenset(),
    "material": frozenset({"family"}),
    "protocol": frozenset({"protocol_version"}),
}


@dataclass
class Val:
    dim: str
    value: object
    raw: object  # the value as found: a field value, or the whole text a parse read
    src: str  # JSON path or source label ("name", "evidence.occurrences[].symbol", "properties[x]")
    via: str  # field | vocabulary | dictionary | interpretive | parse | api | dependency | ref
    issues: tuple = ()
    known: bool = False  # recognised although it carries no value (a dictionary entry `{}`)
    fill_only: bool = False  # supporting evidence: fills gaps, never conflicts


@dataclass
class Loc:
    raw: str | None
    line: int | None = None
    line_end: int | None = None
    occ: dict | None = None
    issues: tuple = ()


Unmapped = tuple  # (src, raw text, leftover tokens); empty tokens: the whole value is unknown


def _get(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _props(comp: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in comp.get("properties") or []:
        if isinstance(p, dict) and p.get("name") is not None:
            out.setdefault(str(p["name"]), p.get("value"))
    return out


def _int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return int(value) if isinstance(value, str) and value.strip().isdigit() else None


def _flatten(components, out: list | None = None) -> list[dict]:
    out = [] if out is None else out
    for c in components or []:
        if isinstance(c, dict):
            out.append(c)
            _flatten(c.get("components"), out)
    return out


def _jsonable(v):
    return list(v) if isinstance(v, tuple) else v


def _no_info(raw) -> bool:
    return raw is None or (isinstance(raw, str) and raw.strip().lower() in NO_INFO)


_DIGEST_BITS = {"MD2": 128, "MD4": 128, "MD5": 128, "SHA-1": 160, "RIPEMD-160": 160, "Whirlpool": 512, "SM3": 256}


def _digest_matches(algorithm: str | None, bits: int) -> bool:
    if not algorithm:
        return False
    if algorithm in _DIGEST_BITS:
        return _DIGEST_BITS[algorithm] == bits
    return str(bits) in re.findall(r"\d+", algorithm)[-1:]


def _supporting(vals: list[Val]) -> list[Val]:
    for v in vals:
        v.fill_only = True
    return vals


class _Reader:
    def __init__(self, cbom: dict, tool: str, spec: AdapterSpec, vocab: Vocabulary, language: str | None,
                 repo_files: set[str] | frozenset[str] | None):  # fmt: skip
        self.cbom, self.tool, self.spec, self.vocab = cbom, tool, spec, vocab
        self.language = (language or "").lower() or None
        self.repo_files = repo_files
        self.bom_format = cbom.get("bomFormat")
        self.spec_version = str(cbom.get("specVersion") or "")
        self.doc_issues = document_issues(self.bom_format, self.spec_version)
        self.index = schema_index(self.spec_version) if self.bom_format == "CycloneDX" else None
        self.comps = _flatten(cbom.get("components"))
        self.by_ref = {c.get("bom-ref"): i for i, c in enumerate(self.comps) if c.get("bom-ref")}
        self.deps: dict[str, set[str]] = defaultdict(set)
        self.rdeps: dict[str, set[str]] = defaultdict(set)
        for d in cbom.get("dependencies") or []:
            if isinstance(d, dict) and d.get("ref"):
                for t in d.get("dependsOn") or []:
                    self.deps[d["ref"]].add(t)
                    self.rdeps[t].add(d["ref"])
        self._fields_cache: dict[int, list[Val]] = {}
        self._phase_a: dict[int, dict[str, object]] = {}
        # (src, raw) -> mapped {dim: value}, how, leftover tokens, count: the per-field mapping table
        self.mappings: dict[tuple[str, str], dict] = {}

    # ----- selection -----

    def select(self, comp: dict) -> tuple[bool, str]:
        f = self.spec.findings
        ctype = comp.get("type")
        if ctype in (f.get("component_types") or ["cryptographic-asset"]):
            return True, "crypto"
        if ctype in ("library", "framework"):
            mode = f.get("libraries", "none")
            tags = set(comp.get("tags") or [])
            if mode == "all" or (mode == "tagged" and tags & set(f.get("library_tags") or [])):
                return True, "library"
        return False, str(ctype)

    # ----- values -----

    def _canon(self, dim: str, value):
        canonical = self.vocab.canonical(dim, value)
        return canonical if canonical is not None else value

    def _dict_vals(self, hit, raw, label: str, dim: str = "name", issues: tuple = ()) -> list[Val]:
        if isinstance(hit, dict):
            # `interpretive: true` marks a judgement rather than a spelling (CryptoScan's "Certificate Validation
            # Bypass" read as protocol TLS): such values count at L4 only (analysis/levels.py)
            via = "interpretive" if hit.get("interpretive") else "dictionary"
            vals = [Val(d, self._canon(d, v), raw, label, via, issues) for d, v in hit.items() if d != "interpretive"]
            return vals or [Val(dim, None, raw, label, via, issues, known=True)]
        return [Val(dim, self._canon(dim, hit), raw, label, "dictionary", issues)]

    def _parsed(self, p: Parsed, text, label: str, *, quiet: bool, issues: tuple = (), via: str = "parse"):
        vals = [Val(d, v, text, label, via, issues) for d, v in p.values.items()]
        unmapped: list[Unmapped] = []
        if p.candidates:  # a list of algorithms ("RSA, ECC, AES"): known, but not one asset
            vals.append(Val("ambiguous", "|".join(p.candidates), text, label, "parse", known=True))
        elif not quiet and not _no_info(text):
            if not p.known:
                unmapped.append((label, str(text), ()))
            elif p.unmapped:
                unmapped.append((label, str(text), tuple(p.unmapped)))
        return vals, unmapped

    def field_vals(self, dim: str, raw, label: str, issues: tuple = ()) -> list[Val]:
        if raw is None:
            return []
        if dim in _PSEUDO:
            return [Val(dim, None, raw, label, "field", issues)]
        if dim == "parse":
            vals, _ = self._parsed(self.vocab.parse(raw), raw, label, quiet=True, issues=issues)
            return vals
        key = ",".join(map(str, raw)) if isinstance(raw, list) else raw
        hit = self.spec.lookup(dim, key)
        if hit is not None:
            return self._dict_vals(hit, raw, label, dim, issues)
        value = self.vocab.canonical(dim, raw)
        vals = [Val(dim, value, raw, label, "field", issues)]
        if value is None and dim in _REPARSE_EXTRA and isinstance(raw, str) and not _no_info(raw):
            found = dict(self.vocab.parse(raw).values)
            if dim not in ("family", "algorithm") and found.get("algorithm") in self.vocab.hashes:
                found.setdefault("digest", found["algorithm"])  # the hash inside a padding or mode name
            main = found.get(dim)
            extra = [Val(d, v, raw, label, "parse") for d, v in found.items() if d in _REPARSE_EXTRA[dim]]
            if main is not None:
                vals = [Val(dim, main, raw, label, "parse", issues)]
            elif extra:  # understood, but it belongs to another dimension (qdarpan:family "TLS" -> protocol)
                vals[0].known = True
            vals += extra
        return vals

    def _std_fields(self, ci: int, comp: dict) -> list[Val]:
        if ci not in self._fields_cache:
            vals: list[Val] = []
            for path, dim in self.spec.fields.items():
                raw = _get(comp, path)
                if raw is None:
                    continue
                issues = tuple(self.index.check(tuple(path.split(".")), raw)) if self.index else ()
                vals += self.field_vals(dim, raw, path, issues)
            if comp.get("type") in ("library", "framework"):
                vals.insert(0, Val("asset_type", "library", comp.get("type"), "type", "field"))
            self._fields_cache[ci] = vals
        return self._fields_cache[ci]

    def _text(self, arg: str, comp: dict, occ: dict | None, props: dict):
        if arg == "name":
            return comp.get("name"), "name"
        kind, _, key = arg.partition(":")
        if kind == "occurrence":
            return (occ or {}).get(key), f"evidence.occurrences[].{key}"
        if kind == "property":
            return props.get(key), f"properties[{key}]"
        if kind == "field":
            return _get(comp, key), key
        if kind == "rule":
            value = props.get(key)
            return (rule_id_to_text(str(value)) if value else None), f"properties[{key}]"
        return None, arg

    def evaluate(self, sdef: SourceDef, ci: int, comp: dict, occ: dict | None, props: dict):
        kind = sdef.kind
        unmapped: list[Unmapped] = []
        if kind == "fields":
            vals = list(self._std_fields(ci, comp))
        elif kind == "property":
            vals = []
            for pname, dim in sdef.arg.items():
                vals += self.field_vals(dim, props.get(pname), f"properties[{pname}]")
        elif kind == "parse":
            text, label = self._text(str(sdef.arg), comp, occ, props)
            if text is None or not str(text).strip():
                return [], []
            hit = self.spec.lookup("name", text)
            if hit is not None:
                vals = self._dict_vals(hit, text, label)
            else:
                vals, unmapped = self._parsed(self.vocab.parse(text), text, label, quiet=sdef.quiet)
            if str(sdef.arg).startswith("rule:"):
                vals = _supporting(vals)
        elif kind == "api":
            text, label = self._text(str(sdef.arg), comp, occ, props)
            vals, _ = self._parsed(self.vocab.api(text), text, label, quiet=True, via="api")
            vals = _supporting(vals)
        elif kind == "dependencies":
            vals = _supporting(self._dependency_vals(ci, comp))
        elif kind == "refs":
            vals, unmapped = self._ref_vals(ci, comp)
        elif kind == "extract":
            vals = EXTRACTORS[str(sdef.arg)](self, comp, occ, props)
        else:  # pragma: no cover - specs.py validates kinds
            vals = []
        if sdef.only:
            vals = [v for v in vals if v.dim in sdef.only]
        self._note(vals, unmapped)
        return vals, unmapped

    def _note(self, vals: list[Val], unmapped: list[Unmapped]) -> None:
        """Collect what each raw value mapped to (analysis/normalize/report.py turns this into vocab.csv)."""
        for v in vals:
            if v.dim in _PSEUDO or v.raw is None or v.via in ("dependency", "ref"):
                continue
            entry = self.mappings.setdefault(
                (v.src, str(_jsonable(v.raw))), {"mapped": {}, "via": set(), "tokens": (), "count": 0}
            )
            if v.value is not None and v.dim != "ambiguous":
                entry["mapped"].setdefault(v.dim, v.value)
            if v.dim == "ambiguous":
                entry["mapped"]["ambiguous"] = v.value
            entry["via"].add(v.via)
            entry["count"] += 1
        for src, raw, tokens in unmapped:
            entry = self.mappings.setdefault((src, raw), {"mapped": {}, "via": set(), "tokens": (), "count": 0})
            entry["tokens"] = tokens or ("*",)
            entry["count"] += 1

    # ----- links between components -----

    def phase_a(self, ci: int) -> dict:
        """Component-level family/algorithm/asset type/digest without links (input of dependencies and refs)."""
        if ci not in self._phase_a:
            self._phase_a[ci] = {}  # recursion guard
            comp = self.comps[ci]
            props = _props(comp)
            partials = []
            for sdef in self.spec.sources:
                if sdef.per_occurrence or sdef.kind in ("dependencies", "refs"):
                    continue
                vals, _ = self.evaluate(sdef, ci, comp, None, props)
                partials.append(vals)
            merged, *_ = merge(partials, self.vocab)
            out = {d: v.value for d, v in merged.items()}
            if not out.get("family") and out.get("algorithm"):
                out["family"] = self.vocab.family_of_algorithm(out["algorithm"])
            self._phase_a[ci] = out
        return self._phase_a[ci]

    def _dependency_vals(self, ci: int, comp: dict) -> list[Val]:
        ref = comp.get("bom-ref")
        if not ref:
            return []
        me = self.phase_a(ci)
        targets = [self.by_ref[t] for t in self.deps.get(ref, ()) if t in self.by_ref]
        sources = [self.by_ref[t] for t in self.rdeps.get(ref, ()) if t in self.by_ref]
        vals: list[Val] = []
        if me.get("asset_type") == "related-crypto-material" and not me.get("family"):
            fams = {
                self.phase_a(t).get("family")
                for t in targets + sources
                if self.phase_a(t).get("asset_type") == "algorithm" and self.phase_a(t).get("family")
            }
            if len(fams) == 1:
                vals.append(Val("family", fams.pop(), None, "dependencies", "dependency"))
        if me.get("asset_type") == "algorithm" and me.get("family") and me.get("algorithm") not in self.vocab.hashes:
            hashes = {self.phase_a(t).get("algorithm") for t in targets} & self.vocab.hashes
            if len(hashes) == 1:
                vals.append(Val("digest", hashes.pop(), None, "dependencies", "dependency"))
        return vals

    def _ref_vals(self, ci: int, comp: dict) -> tuple[list[Val], list[Unmapped]]:
        vals: list[Val] = []
        unmapped: list[Unmapped] = []
        for path, dim in self.spec.fields.items():
            if dim != "ref":
                continue
            ref = _get(comp, path)
            if not ref:
                continue
            found: list[Val] = []
            target = self.by_ref.get(ref)
            if target is not None and target != ci:
                for d, v in self.phase_a(target).items():
                    if d in ("family", "algorithm", "key_size", "curve", "digest") and v is not None:
                        found.append(Val(d, v, ref, f"{path}->ref", "ref"))
            if not found:  # unresolvable or empty target: the ref string itself ("crypto/alg/rsa-2048")
                found, um = self._parsed(self.vocab.parse(ref), ref, path, quiet=True)
                unmapped += um
            vals += found
        return vals, unmapped

    # ----- locations -----

    def locations(self, comp: dict, props: dict) -> list[Loc]:
        for entry in self.spec.locations:
            locs = (
                _property_pair(props, entry["properties"])
                if isinstance(entry, dict)
                else LOCATIONS[entry](self, comp, props)
            )
            if locs:
                return locs
        return []

    def place(self, loc: Loc) -> tuple[str | None, int | None, bool | None, bool | None]:
        """(file, line, in_scope, file_exists) of a reported location."""
        raw, line = loc.raw, loc.line
        if raw is None:
            return None, line, None, None
        text = str(raw).strip()
        if line is None:
            m = _PATH_LINE.match(text)
            if m and (m.group(2) or m.group(3)):
                text, line = m.group(1), int(m.group(2) or m.group(3))
        file = relative_location(text, repo_markers=_REPO_MARKERS) or None
        exists = None
        if file and self.repo_files is not None:
            exists = file in self.repo_files
            if not exists:
                cands = [f for f in self.repo_files if f.endswith("/" + file)]
                if len(cands) == 1:
                    file, exists = cands[0], True
        exts = CODE_EXT.get(self.language or "")
        in_scope = None if not file else (exts is None or os.path.splitext(file)[1].lower() in exts)
        return file, line, in_scope, exists

    # ----- one component -> records -----

    def records(self, ci: int, comp: dict) -> list[NormRecord]:
        props = _props(comp)
        pre = next((v.value for v in self._std_fields(ci, comp) if v.dim == "asset_type"), None)
        comp_partials: dict[int, tuple[list[Val], list[Unmapped]]] = {}
        for pos, sdef in enumerate(self.spec.sources):
            if sdef.when and pre not in (sdef.when.get("asset_type") or []):
                comp_partials[pos] = ([], [])
            elif not sdef.per_occurrence:
                comp_partials[pos] = self.evaluate(sdef, ci, comp, None, props)
        comp_issues = self.index.check(("type",), comp.get("type")) if self.index is not None else []
        out = []
        for oi, loc in enumerate(self.locations(comp, props) or [Loc(raw=None)]):
            partials, unmapped = [], []
            for pos, sdef in enumerate(self.spec.sources):
                vals, um = comp_partials[pos] if pos in comp_partials else self.evaluate(sdef, ci, comp, loc.occ, props)
                partials.append(vals)
                unmapped += um
            out.append(self._build(ci, oi, comp, loc, partials, unmapped, comp_issues, props))
        return out

    def _build(self, ci, oi, comp, loc, partials, unmapped, comp_issues, props) -> NormRecord:
        first, *_ = merge(partials, self.vocab)
        family = first["family"].value if "family" in first else None
        if family is None and "algorithm" in first:
            family = self.vocab.family_of_algorithm(first["algorithm"].value)
        material = first["material"].value if "material" in first else None
        algorithm = first["algorithm"].value if "algorithm" in first else None
        partials = self._expand_params(partials, family, material, algorithm)
        merged, conflicts, val_unmapped, issues = merge(partials, self.vocab)
        rec = NormRecord(
            record_id=f"{self.tool}:{ci}:{oi}",
            tool=self.tool,
            component_index=ci,
            occurrence_index=oi,
            component=comp.get("bom-ref"),
            name=comp.get("name"),
        )
        amb = merged.pop("ambiguous", None)
        if amb is not None and "family" not in merged and "algorithm" not in merged:
            rec.ambiguous = f"{amb.raw} ({amb.value})"
        for dim, v in merged.items():
            rec.values[dim] = v.value
            rec.raw[dim] = _jsonable(v.raw)
            rec.src[dim] = v.src
            rec.via[dim] = v.via
        for vals in partials:  # dimensions without a canonical value keep the first raw value seen
            for v in vals:
                if v.dim not in merged and v.dim not in _PSEUDO and v.dim != "ambiguous" and v.raw is not None:
                    rec.raw.setdefault(v.dim, _jsonable(v.raw))
                    rec.src.setdefault(v.dim, v.src)
        self._derive(rec)
        rec.raw = {k: v for k, v in rec.raw.items() if v is not None}
        rec.conflicts = {d: {"value": _jsonable(v.value), "src": v.src} for d, v in conflicts.items()}
        rec.unmapped = [
            {"src": s, "raw": r, **({"tokens": list(t)} if t else {})}
            for s, r, t in dict.fromkeys(val_unmapped + unmapped)
        ]
        rec.spec_issues = list(dict.fromkeys(comp_issues + issues + list(loc.issues)))
        rec.file, rec.line, rec.in_scope, rec.file_exists = self.place(loc)
        rec.line_end = loc.line_end
        rec.location = str(loc.raw) if loc.raw is not None else None
        for arg in self.spec.context:
            text, _ = self._text(arg, comp, loc.occ, props)
            if text not in (None, ""):
                text = str(text).strip()
                rec.context = text if len(text) <= CONTEXT_MAX else text[: CONTEXT_MAX - 1] + "…"
                break
        return rec

    def _expand_params(self, partials, family: str | None, material: str | None, algorithm: str | None):
        """parameterSetIdentifier by the family's meaning (key/curve/digest); related-material size by material."""
        info = self.vocab.family_info(family)
        sem = info.param if info else "none"
        out = []
        for vals in partials:
            new = []
            for v in vals:
                if v.dim == "size":
                    n = _int(v.raw)
                    if n is not None:
                        dim = "key_size" if material in _KEY_MATERIAL else "param_set"
                        new.append(Val(dim, n if dim == "key_size" else str(n), v.raw, v.src, "field", v.issues))
                    continue
                if v.dim != "param":
                    new.append(v)
                    continue
                n = _int(v.raw)
                if sem in ("key", "curve") and n:
                    new.append(Val("key_size", n, v.raw, v.src, "field", v.issues))
                elif sem == "digest" and n and family in ("SHA-2", "SHA-3") and algorithm in (None, family):
                    name = f"SHA-{n}" if family == "SHA-2" else f"SHA3-{n}"
                    dim = "algorithm" if name in self.vocab.algorithms else "param_set"
                    new.append(Val(dim, name if dim == "algorithm" else str(v.raw), v.raw, v.src, "field", v.issues))
                elif sem == "digest" and n and _digest_matches(algorithm or family, n):
                    continue  # the digest length the name already says (SHA-256 / 256, MD5 / 128)
                elif not _no_info(v.raw):
                    new.append(Val("param_set", str(v.raw).strip(), v.raw, v.src, "field", v.issues))
            out.append(new)
        return out

    def _derive(self, rec: NormRecord) -> None:
        vals, src, via = rec.values, rec.src, rec.via

        def put(dim: str, value, origin: str, how: str = "derived") -> None:
            if value is not None and vals.get(dim) is None:
                vals[dim] = value
                src[dim] = origin
                via[dim] = how

        if vals.get("algorithm") and not vals.get("family"):
            put("family", self.vocab.family_of_algorithm(vals["algorithm"]), src["algorithm"], via["algorithm"])
        if vals.get("curve") and not vals.get("family"):
            put("family", "EC", "derived:curve")
        if vals.get("family") and not vals.get("algorithm"):
            put("algorithm", vals["family"], src["family"], via["family"])
        if vals.get("curve") and not vals.get("key_size"):
            put("key_size", self.vocab.curve_size(vals["curve"]), "derived:curve")
        if vals.get("family") and not vals.get("primitive"):
            info = self.vocab.family_info(vals["family"])
            algo = self.vocab.algorithms.get(vals.get("algorithm") or "", {})
            prim = algo.get("primitive") or (info.primitive if info else None)
            if vals["family"] == "AES" and vals.get("mode") in _AE_MODES:
                prim = "ae"
            elif vals["family"] == "AES" and vals.get("mode") in _WRAP_MODES:
                prim = "key-wrap"
            put("primitive", prim, "derived:family")
        if "group" in vals:  # a whole-name entry (JKS -> CERT) set the group directly
            rec.group = vals.pop("group")
            for d in (src, via, rec.raw):
                d.pop("group", None)
        if vals.get("family"):
            rec.group = self.vocab.group_of(vals["family"]) or rec.group
        elif rec.group is None and vals.get("asset_type") == "certificate":
            rec.group = "CERT"
        elif rec.group is None and vals.get("protocol") in ("TLS", "SSL", "DTLS"):
            rec.group = "TLS"


def merge(partials: list[list[Val]], vocab: Vocabulary | None = None):
    """First value per dimension wins; a later, more specific one of the same kind refines it (EC -> ECDSA); a later
    different one becomes a conflict unless it is supporting evidence. Returns
    (merged {dim: Val}, conflicts {dim: Val}, unmapped [(src, raw, ())], spec issues [str])."""
    vocab = vocab or load_vocabulary()
    merged: dict[str, Val] = {}
    conflicts: dict[str, Val] = {}
    unmapped: list[Unmapped] = []
    issues: list[str] = []
    for vals in partials:
        for v in vals:
            for i in v.issues:
                if i not in issues:
                    issues.append(i)
            if v.dim in _PSEUDO:
                continue
            if v.dim == "ambiguous":
                merged.setdefault("ambiguous", v)
                continue
            if v.value is None:
                if not v.known and not _no_info(v.raw) and v.raw not in ([], ()):
                    unmapped.append((v.src, str(_jsonable(v.raw)), ()))
                continue
            cur = merged.get(v.dim)
            if cur is None:
                merged[v.dim] = v
                continue
            rel = vocab.relation(v.dim, cur.value, v.value)
            if rel == "refines":
                merged[v.dim] = v
            elif rel is None and not v.fill_only and v.dim not in conflicts:
                conflicts[v.dim] = v
    return merged, conflicts, unmapped, issues


# ----- location extractors (named in specs: `locations:`) -----


def _evidence_occurrences(r: _Reader, comp: dict, props: dict) -> list[Loc]:
    out = []
    for occ in (comp.get("evidence") or {}).get("occurrences") or []:
        if not isinstance(occ, dict) or not occ.get("location"):
            continue
        issues: list[str] = []
        if r.index is not None:
            for key in ("location", "line", "offset", "symbol", "additionalContext"):
                if key in occ:
                    issues += r.index.check(("evidence", "occurrences", key), occ[key])
        out.append(Loc(raw=str(occ["location"]), line=_int(occ.get("line")), occ=occ, issues=tuple(issues)))
    return out


def _property_locations(r: _Reader, comp: dict, props: dict) -> list[Loc]:
    out = []
    for name in sorted(props, key=lambda n: (len(n), n)):
        if "location" in name.lower() and isinstance(props[name], str) and _PATH_LINE.match(props[name]):
            out.append(Loc(raw=props[name], occ={"property": name}))
    return out


def _detection_context(r: _Reader, comp: dict, props: dict) -> list[Loc]:
    out = []
    for dc in (comp.get("cryptoProperties") or {}).get("detectionContext") or []:
        if not isinstance(dc, dict) or not dc.get("filePath"):
            continue
        lines = sorted(n for n in (dc.get("lineNumbers") or []) if isinstance(n, int))
        out.append(
            Loc(
                raw=str(dc["filePath"]),
                line=lines[0] if lines else None,
                line_end=lines[-1] if len(lines) > 1 else None,
                occ={"additionalContext": dc.get("additionalContext")},
            )
        )
    return out


def _property_pair(props: dict, names: dict) -> list[Loc]:
    """File and line in two custom properties (spec: `{properties: {file: t2:file, line: t2:line}}`)."""
    file = props.get(names["file"])
    if not file:
        return []
    return [Loc(raw=str(file), line=_int(props.get(names.get("line"))), occ={"property": names["file"]})]


def _src_file_property(r: _Reader, comp: dict, props: dict) -> list[Loc]:
    value = props.get("SrcFile")
    return [Loc(raw=str(value))] if value else []


LOCATIONS = {
    "evidence_occurrences": _evidence_occurrences,
    "property_locations": _property_locations,
    "detection_context": _detection_context,
    "src_file_property": _src_file_property,
}

# ----- value extractors (named in specs: `{extract: <name>}`) -----

_JSON_KEYS = {
    "algorithm": "parse", "name": "parse", "family": "family", "algorithmFamily": "family", "mode": "mode",
    "padding": "padding", "keySize": "key_size", "key_size": "key_size", "curve": "curve", "hash": "digest",
    "digest": "digest", "primitive": "primitive",
}  # fmt: skip


def _json_context(r: _Reader, comp: dict, occ: dict | None, props: dict) -> list[Val]:
    """An occurrence `additionalContext` holding a JSON object : known keys become values."""
    raw = (occ or {}).get("additionalContext")
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    vals: list[Val] = []
    for key, value in data.items() if isinstance(data, dict) else []:
        dim = _JSON_KEYS.get(key)
        if dim and isinstance(value, str | int):
            vals += r.field_vals(dim, value, f"evidence.occurrences[].additionalContext.{key}")
    return vals


EXTRACTORS = {"json_context": _json_context}


# ----- public API -----


def normalize_cbom(
    cbom: dict,
    tool: str,
    *,
    language: str | None = None,
    repo_files: set[str] | frozenset[str] | None = None,
    source: dict | None = None,
    spec: AdapterSpec | None = None,
    vocab: Vocabulary | None = None,
) -> NormalizedCbom:
    """Normalise one CBOM of `tool` with its adapter spec (analysis/adapters/<tool>.yaml)."""
    spec = spec or load_adapter(tool)
    vocab = vocab or load_vocabulary()
    cbom = cbom if isinstance(cbom, dict) else {}
    reader = _Reader(cbom, tool, spec, vocab, language, repo_files)
    records: list[NormRecord] = []
    skipped: dict[str, int] = defaultdict(int)
    findings = 0
    for ci, comp in enumerate(reader.comps):
        ok, kind = reader.select(comp)
        if not ok:
            skipped[kind] += 1
            continue
        findings += 1
        records += reader.records(ci, comp)
    meta_tools = (cbom.get("metadata") or {}).get("tools")
    if isinstance(meta_tools, dict):
        meta_tools = meta_tools.get("components")
    stats = {
        "components": len(reader.comps),
        "findings": findings,
        "skipped": dict(sorted(skipped.items())),
        "records": len(records),
        "located": sum(1 for r in records if r.file and r.line),
        "with_family": sum(1 for r in records if r.values.get("family")),
        "with_unmapped": sum(1 for r in records if r.unmapped),
        "with_conflicts": sum(1 for r in records if r.conflicts),
        "ambiguous": sum(1 for r in records if r.ambiguous),
    }
    out = NormalizedCbom(
        tool=tool,
        adapter={"spec": spec.label, "fallback": spec.fallback},
        vocabulary={"algorithms": vocab.version, "registry": vocab.registry_version},
        source=dict(source or {}),
        declared={
            "bomFormat": reader.bom_format,
            "specVersion": reader.spec_version or None,
            "tools": [
                {"name": t.get("name"), "version": t.get("version")} for t in meta_tools or [] if isinstance(t, dict)
            ],
        },
        reader="cyclonedx" if reader.bom_format == "CycloneDX" else str(reader.bom_format or "unknown"),
        checked_against=f"CycloneDX {reader.spec_version}" if reader.index is not None else None,
        document_issues=reader.doc_issues,
        stats=stats,
        records=records,
    )
    out.mappings = reader.mappings
    return out
