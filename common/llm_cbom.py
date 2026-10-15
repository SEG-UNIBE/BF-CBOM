"""Code-reading LLM CBOM generation shared by all LLM provider workers (BF-CBOM 2.0, Phase 2).

Pipeline: select source files that reference crypto APIs -> send them in line-numbered chunks ->
require file and line per reported asset -> assemble a CycloneDX 1.6 CBOM. Provider workers
(`workers/deepseek`, later `workers/chatgpt`, `workers/claude`) only supply an `LLMProvider`, so all
of them use identical prompts and file selection and differ in the model alone.

Determinism knobs: temperature 0, deterministic file order, PROMPT_VERSION recorded in the CBOM.
Cost knobs (env): LLM_MAX_FILES, LLM_MAX_CHUNK_CHARS, LLM_MAX_CHUNKS, LLM_MAX_FILE_BYTES.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import RepoInfo, Trace

logger = logging.getLogger(__name__)

PROMPT_VERSION = "2026-10-15.1"

LLM_MAX_FILES = int(os.getenv("LLM_MAX_FILES", "40"))
LLM_MAX_CHUNK_CHARS = int(os.getenv("LLM_MAX_CHUNK_CHARS", "40000"))  # ~10k tokens of code per request
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192"))  # long asset lists must not be cut off
LLM_MAX_CHUNKS = int(os.getenv("LLM_MAX_CHUNKS", "12"))
LLM_MAX_FILE_BYTES = int(os.getenv("LLM_MAX_FILE_BYTES", "400000"))

SOURCE_EXTENSIONS: dict[str, set[str]] = {
    "python": {".py"},
    "java": {".java", ".kt", ".scala"},
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "build", "dist", "target", "out", ".venv", "venv", "env", "__pycache__",
    ".idea", ".vscode", "vendor", "third_party", "thirdparty", "site-packages", ".tox", ".mypy_cache",
}  # fmt: skip

# Generous, import- and API-based pre-filter: a file is sent to the model if any pattern matches.
# This bounds the recall of every LLM worker and is therefore part of the "tool" (see the paper).
CRYPTO_API_PATTERNS: dict[str, re.Pattern] = {
    "python": re.compile(
        r"\b(cryptography|hashlib|hmac|ssl|secrets|Crypto|Cryptodome|nacl|OpenSSL|ecdsa|rsa|bcrypt|passlib|jwt|jose|"
        r"paramiko|M2Crypto|pyasn1|certifi|argon2|scrypt|blake3|pycryptodome|tink|Fernet|AESGCM|ChaCha20|"
        r"PBKDF2|HKDF|SHA\d|MD5|RSA|DSA|ECDSA|Ed25519|X25519|TLS|AES|ChaCha|Poly1305|HMAC|KeyPair|PublicKey|PrivateKey|"
        r"load_pem|Certificate|x509|sign|verify|encrypt|decrypt)\b"
    ),
    "java": re.compile(
        r"\b(javax\.crypto|java\.security|javax\.net\.ssl|org\.bouncycastle|com\.google\.crypto\.tink|"
        r"MessageDigest|KeyPairGenerator|KeyGenerator|KeyFactory|KeyStore|KeyAgreement|Signature|SecureRandom|Cipher|Mac|"
        r"SSLContext|SSLSocket|TrustManager|X509|Certificate|CertificateFactory|SecretKeySpec|IvParameterSpec|GCMParameterSpec|"
        r"PBEKeySpec|SecretKeyFactory|RSAPublicKey|ECGenParameterSpec|Argon2|BCrypt|SCrypt|PBKDF2|HmacSHA|SHA-?\d|MD5|AES|RSA|"
        r"ECDSA|Ed25519|X25519|ChaCha20|TLS)\b"
    ),
}
# "other" languages: the union of both pattern sets
_ANY_PATTERN = re.compile("|".join(p.pattern for p in CRYPTO_API_PATTERNS.values()))

SYSTEM_PROMPT = """You are a cryptography auditor producing a Cryptography Bill of Materials (CBOM).
You receive source files of one software project, each line prefixed with its file path and line number.
List every cryptographic asset that this code USES or CONFIGURES: algorithms (ciphers, hashes, MACs, signatures,
key agreement, key derivation, random number generation), keys and key material, certificates, and protocols
(TLS, SSH, JWT/JWS/JWE). Report only what the shown code actually does; do not infer from library names alone,
do not list general-purpose libraries, and do not invent locations.

Answer with ONE JSON object and nothing else:
{"assets": [
  {"name": "<algorithm or asset name as used, e.g. AES, SHA-256, RSA, HMAC-SHA256, TLS, X.509>",
   "assetType": "algorithm|key|certificate|protocol|related-crypto-material",
   "primitive": "<for algorithms: block-cipher|stream-cipher|hash|mac|signature|pke|kem|key-agree|kdf|drbg|ae|other,
                 else null>",
   "keySize": <bits as integer or null>, "curve": "<e.g. P-256, secp256k1, Ed25519 or null>",
   "mode": "<e.g. GCM, CBC, CTR or null>", "padding": "<e.g. PKCS7, OAEP, PKCS1v15 or null>",
   "digest": "<hash used inside a MAC/signature/KDF, e.g. SHA-256, or null>",
   "file": "<path exactly as given>", "line": <integer line number of the call site>,
   "evidence": "<the code fragment on that line that shows the use>",
   "confidence": "high|medium|low"}
]}
One entry per call site. If a value is not visible in the code, use null. If there is nothing to report,
answer {"assets": []}."""


@dataclass
class FileSelection:
    files: list[Path]
    candidates: int = 0  # source files of the language
    matched: int = 0  # files that matched the crypto pattern
    skipped_large: int = 0


def _language_key(language: str | None) -> str | None:
    if not language:
        return None
    lang = language.strip().lower()
    return {"python": "python", "java": "java", "kotlin": "java", "scala": "java"}.get(lang)


def _detect_language(repo_path: Path) -> str | None:
    counts = dict.fromkeys(SOURCE_EXTENSIONS, 0)
    for p in _iter_source_files(repo_path, None):
        for lang, exts in SOURCE_EXTENSIONS.items():
            if p.suffix.lower() in exts:
                counts[lang] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def _iter_source_files(repo_path: Path, language: str | None):
    exts = SOURCE_EXTENSIONS.get(language) if language else set().union(*SOURCE_EXTENSIONS.values())
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            p = Path(root) / name
            if p.suffix.lower() in exts and not p.is_symlink():
                yield p


def select_files(
    repo_path: str | Path,
    language: str | None,
    *,
    max_files: int = LLM_MAX_FILES,
    max_file_bytes: int = LLM_MAX_FILE_BYTES,
) -> FileSelection:
    """Deterministically pick the source files that reference crypto APIs (most matches first)."""
    repo_path = Path(repo_path)
    lang = _language_key(language) or _detect_language(repo_path)
    pattern = CRYPTO_API_PATTERNS.get(lang, _ANY_PATTERN) if lang else _ANY_PATTERN
    sel = FileSelection(files=[])
    scored: list[tuple[int, str, Path]] = []
    for p in _iter_source_files(repo_path, lang):
        sel.candidates += 1
        try:
            if p.stat().st_size > max_file_bytes:
                sel.skipped_large += 1
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hits = len(pattern.findall(text))
        if hits:
            sel.matched += 1
            scored.append((-hits, str(p.relative_to(repo_path)), p))
    scored.sort()
    sel.files = [p for _, _, p in scored[:max_files]]
    return sel


def render_chunks(repo_path: str | Path, files: list[Path], *, max_chars: int = LLM_MAX_CHUNK_CHARS) -> list[str]:
    """Pack line-numbered files into chunks of at most `max_chars`; long files are split into line windows."""
    repo_path = Path(repo_path)
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    def flush():
        nonlocal current, size
        if current:
            chunks.append("\n".join(current))
            current, size = [], 0

    for p in files:
        rel = str(p.relative_to(repo_path))
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        numbered = [f"{rel}:{i}: {ln}" for i, ln in enumerate(lines, start=1)]
        pos = 0
        while pos < len(numbered):
            header = f"### FILE {rel}" + (f" (lines {pos + 1}-)" if pos else "")
            block, block_size = [header], len(header) + 1
            while pos < len(numbered) and block_size + len(numbered[pos]) + 1 <= max_chars:
                block.append(numbered[pos])
                block_size += len(numbered[pos]) + 1
                pos += 1
            if len(block) == 1:  # a single line longer than the budget: truncate it
                block.append(numbered[pos][: max_chars - block_size - 1])
                pos += 1
                block_size = max_chars
            if size + block_size > max_chars:
                flush()
            current.extend(block)
            size += block_size
        if not numbered:
            continue
    flush()
    return chunks


def split_chunk(chunk: str) -> list[str]:
    """Split a rendered chunk into two halves at a line boundary (keeps `path:line:` prefixes intact)."""
    lines = chunk.splitlines()
    if len(lines) < 4:
        return [chunk]
    mid = len(lines) // 2
    return ["\n".join(lines[:mid]), "\n".join(lines[mid:])]


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        """Return (text, usage) where usage has prompt_tokens / completion_tokens when known."""
        ...


class OpenAICompatProvider:
    """Any OpenAI-compatible chat API (DeepSeek, OpenAI, ...). Temperature 0, JSON mode."""

    def __init__(
        self,
        name: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 180.0,
        max_output_tokens: int = LLM_MAX_OUTPUT_TOKENS,
    ):
        from openai import OpenAI  # imported lazily so the module stays importable without the SDK

        self.name, self.model, self.max_output_tokens = name, model, max_output_tokens
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1)

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=self.max_output_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        usage = getattr(resp, "usage", None)
        return resp.choices[0].message.content or "", {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }


class AnthropicProvider:
    """Anthropic Messages API over HTTPS (requests), temperature 0. JSON is requested in the prompt and the
    answer is parsed by `parse_assets`, which tolerates code fences."""

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        name: str,
        model: str,
        api_key: str,
        timeout: float = 180.0,
        max_output_tokens: int = LLM_MAX_OUTPUT_TOKENS,
        post=None,
    ):
        import requests

        self.name, self.model, self.timeout, self.max_output_tokens = name, model, timeout, max_output_tokens
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }
        self._post = post or requests.post

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        payload = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "temperature": 0,
            "system": system + "\nAnswer with the JSON object only, no prose, no code fences.",
            "messages": [{"role": "user", "content": user}],
        }
        resp = self._post(self.API_URL, headers=self._headers, json=payload, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"anthropic api {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        return text, {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
        }


def parse_assets(text: str) -> list[dict]:
    """Extract the asset list from a model answer (tolerates code fences and a bare list)."""
    body = text.strip()
    if "```" in body:
        body = body.split("```", 1)[1]
        body = body.split("\n", 1)[1] if body.startswith("json") else body
        body = body.split("```", 1)[0]
    data = json.loads(body)
    if isinstance(data, list):
        return [a for a in data if isinstance(a, dict)]
    if isinstance(data, dict):
        assets = data.get("assets")
        if isinstance(assets, list):
            return [a for a in assets if isinstance(a, dict)]
    return []


def _int_or_none(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _str_or_none(v) -> str | None:
    return str(v).strip() if v not in (None, "", "null") else None


_ASSET_TYPES = {"algorithm", "key", "certificate", "protocol", "related-crypto-material"}


def _normalize_asset(a: dict) -> dict | None:
    name = _str_or_none(a.get("name"))
    if not name:
        return None
    asset_type = (_str_or_none(a.get("assetType")) or "algorithm").lower()
    if asset_type not in _ASSET_TYPES:
        asset_type = "related-crypto-material"
    return {
        "name": name,
        "assetType": asset_type,
        "primitive": _str_or_none(a.get("primitive")),
        "keySize": _int_or_none(a.get("keySize")),
        "curve": _str_or_none(a.get("curve")),
        "mode": _str_or_none(a.get("mode")),
        "padding": _str_or_none(a.get("padding")),
        "digest": _str_or_none(a.get("digest")),
        "file": _str_or_none(a.get("file")),
        "line": _int_or_none(a.get("line")),
        "evidence": (_str_or_none(a.get("evidence")) or "")[:300],
        "confidence": (_str_or_none(a.get("confidence")) or "medium").lower(),
    }


def verify_location(repo_path: Path | None, file: str | None, line: int | None) -> bool:
    """True when `file` exists under the checkout and `line` is inside it."""
    if not repo_path or not file or not line or line < 1:
        return False
    p = (repo_path / file).resolve()
    try:
        p.relative_to(repo_path.resolve())
    except ValueError:
        return False
    if not p.is_file():
        return False
    try:
        with p.open("rb") as fh:
            return sum(1 for _ in fh) >= line
    except OSError:
        return False


@dataclass
class GenerationStats:
    files_candidates: int = 0
    files_matched: int = 0
    files_sent: int = 0
    chunks_sent: int = 0
    chunks_total: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    assets_raw: int = 0
    assets_unparsable_chunks: int = 0
    chunks_split: int = 0  # chunks re-sent as two halves after an unparsable answer
    locations_verified: int = 0
    locations_unverified: int = 0
    notes: list[str] = field(default_factory=list)


def assemble_cbom(
    assets: list[dict],
    *,
    tool_name: str,
    model: str,
    repo_info: RepoInfo | None,
    repo_path: Path | None,
    stats: GenerationStats,
) -> dict:
    """CycloneDX 1.6 CBOM from normalised assets; duplicates (same asset at the same place) collapse."""
    components: list[dict] = []
    seen: set[tuple] = set()
    for raw in assets:
        a = _normalize_asset(raw)
        if a is None:
            continue
        key = (
            a["name"].lower(),
            a["assetType"],
            a["keySize"],
            a["curve"],
            a["mode"],
            a["padding"],
            a["file"],
            a["line"],
        )
        if key in seen:
            continue
        seen.add(key)
        algo = {
            k: v
            for k, v in {
                "primitive": a["primitive"],
                "parameterSetIdentifier": str(a["keySize"]) if a["keySize"] else None,
                "curve": a["curve"],
                "mode": a["mode"],
                "padding": a["padding"],
            }.items()
            if v
        }
        crypto: dict = {"assetType": a["assetType"]}
        if a["assetType"] == "algorithm" and algo:
            crypto["algorithmProperties"] = algo
        props = [{"name": "bf-cbom:confidence", "value": a["confidence"]}]
        if a["digest"]:
            props.append({"name": "bf-cbom:digest", "value": a["digest"]})
        if a["evidence"]:
            props.append({"name": "bf-cbom:evidence", "value": a["evidence"]})
        comp: dict = {
            "type": "cryptographic-asset",
            "bom-ref": f"{tool_name}:{uuid.uuid4()}",
            "name": a["name"],
            "cryptoProperties": crypto,
            "properties": props,
        }
        if a["file"] and a["line"]:
            verified = verify_location(repo_path, a["file"], a["line"])
            comp["evidence"] = {"occurrences": [{"location": a["file"], "line": a["line"]}]}
            if verified:
                stats.locations_verified += 1
            else:
                stats.locations_unverified += 1
                props.append({"name": "bf-cbom:location-unverified", "value": "true"})
        else:
            stats.locations_unverified += 1
            props.append({"name": "bf-cbom:location-missing", "value": "true"})
        components.append(comp)

    metadata: dict = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tools": {"components": [{"type": "application", "name": tool_name, "version": model}]},
        "properties": [
            {"name": "bf-cbom:llm-model", "value": model},
            {"name": "bf-cbom:prompt-version", "value": PROMPT_VERSION},
            {"name": "bf-cbom:files-candidates", "value": str(stats.files_candidates)},
            {"name": "bf-cbom:files-matched", "value": str(stats.files_matched)},
            {"name": "bf-cbom:files-sent", "value": str(stats.files_sent)},
            {"name": "bf-cbom:chunks-total", "value": str(stats.chunks_total)},
            {"name": "bf-cbom:requests", "value": str(stats.chunks_sent)},
            {"name": "bf-cbom:chunks-split", "value": str(stats.chunks_split)},
            {"name": "bf-cbom:prompt-tokens", "value": str(stats.prompt_tokens)},
            {"name": "bf-cbom:completion-tokens", "value": str(stats.completion_tokens)},
        ],
    }
    if repo_info is not None:
        metadata["component"] = {
            "type": "application",
            "name": repo_info.full_name,
            **({"version": repo_info.commit[:12]} if repo_info.commit else {}),
            "properties": [
                {"name": "bf-cbom:git-url", "value": repo_info.git_url},
                {"name": "bf-cbom:branch", "value": repo_info.branch},
                *([{"name": "bf-cbom:commit", "value": repo_info.commit}] if repo_info.commit else []),
            ],
        }
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": metadata,
        "components": components,
    }


def generate_cbom(
    provider: LLMProvider,
    repo_path: str | Path,
    *,
    repo_info: RepoInfo | None = None,
    language: str | None = None,
    trace: Trace | None = None,
    max_files: int = LLM_MAX_FILES,
    max_chunk_chars: int = LLM_MAX_CHUNK_CHARS,
    max_chunks: int = LLM_MAX_CHUNKS,
    deadline: float | None = None,
    on_progress: Callable[[], None] | None = None,
) -> tuple[dict, GenerationStats]:
    """Select, chunk, query and assemble. `deadline` is a time.monotonic() value; chunks past it are skipped."""
    repo_path = Path(repo_path)
    trace = trace or Trace()
    stats = GenerationStats()
    sel = select_files(repo_path, language, max_files=max_files)
    stats.files_candidates, stats.files_matched, stats.files_sent = sel.candidates, sel.matched, len(sel.files)
    chunks = render_chunks(repo_path, sel.files, max_chars=max_chunk_chars)
    stats.chunks_total = len(chunks)
    if len(chunks) > max_chunks:
        trace.add(f"llm: {len(chunks)} chunks exceed LLM_MAX_CHUNKS={max_chunks}; sending the first {max_chunks}")
        chunks = chunks[:max_chunks]
    assets: list[dict] = []
    project = repo_info.full_name if repo_info else repo_path.name

    def ask(label: str, chunk: str) -> list[dict] | None:
        """One request; None when the answer could not be parsed (API errors are recorded and count as None)."""
        user = f"Project: {project}\n{label}\n\n{chunk}"
        try:
            text, usage = provider.complete(SYSTEM_PROMPT, user)
        except Exception as e:  # network / API errors: keep what we have, record the failure
            trace.add(f"llm: {label} failed: {type(e).__name__}: {str(e)[:200]}")
            return None
        stats.chunks_sent += 1
        stats.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        stats.completion_tokens += int(usage.get("completion_tokens") or 0)
        try:
            return parse_assets(text)
        except (json.JSONDecodeError, ValueError) as e:
            trace.add(f"llm: {label} answer not parsable: {e}")
            return None

    for i, chunk in enumerate(chunks, start=1):
        if deadline is not None and time.monotonic() > deadline:
            trace.add(f"llm: deadline reached after {i - 1}/{len(chunks)} chunks")
            break
        found = ask(f"Chunk {i} of {len(chunks)}.", chunk)
        if found is None:
            # Usually a truncated or malformed JSON answer for a dense chunk: retry once with two halves.
            halves = split_chunk(chunk)
            if len(halves) == 2:
                stats.chunks_split += 1
                found = []
                for j, half in enumerate(halves, start=1):
                    part = ask(f"Chunk {i} of {len(chunks)}, part {j} of 2.", half)
                    if part is None:
                        stats.assets_unparsable_chunks += 1
                    else:
                        found.extend(part)
            else:
                stats.assets_unparsable_chunks += 1
                continue
        stats.assets_raw += len(found)
        assets.extend(found)
        if on_progress:
            on_progress()
    cbom = assemble_cbom(
        assets, tool_name=provider.name, model=provider.model, repo_info=repo_info, repo_path=repo_path, stats=stats
    )
    trace.add(
        f"llm: files {stats.files_sent}/{stats.files_matched} matched of {stats.files_candidates}; "
        f"requests {stats.chunks_sent} for {stats.chunks_total} chunks ({stats.chunks_split} split); "
        f"tokens {stats.prompt_tokens}+{stats.completion_tokens}; "
        f"assets {len(cbom['components'])} (raw {stats.assets_raw}); "
        f"locations verified {stats.locations_verified}, unverified {stats.locations_unverified}"
    )
    return cbom, stats
