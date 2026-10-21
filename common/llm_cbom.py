"""Code-reading LLM CBOM generation shared by all LLM provider workers (BF-CBOM 2.0, Phase 2).

Pipeline: select source files that reference crypto APIs -> send them in line-numbered chunks ->
require file and line per reported asset -> assemble a CycloneDX 1.7 CBOM. Provider workers
(`workers/deepseek`, later `workers/chatgpt`, `workers/claude`) only supply an `LLMProvider`, so all
of them use identical prompts and file selection and differ in the model alone.

Determinism knobs: deterministic file order, PROMPT_VERSION recorded in the CBOM, and the sampling
temperature from LLM_TEMPERATURE (unset: the parameter is not sent, as current Claude and OpenAI reasoning
models reject non-default values and DeepSeek ignores it while thinking; the value is recorded in the CBOM as
bf-cbom:temperature).
Request knobs (env): LLM_TEMPERATURE, OPENAI_MAX_TOKENS_PARAM (max_tokens, or max_completion_tokens for
OpenAI reasoning models).
Budget knobs (env): LLM_MAX_FILES, LLM_MAX_CHUNK_CHARS, LLM_MAX_CHUNKS (a safety valve, not a cost cap: a run that hits
it is marked `bf-cbom:chunks-skipped` and the chunk coverage is in runs.csv), LLM_MAX_FILE_BYTES; LLM_CONCURRENCY
requests in flight per job.

Request failures: the providers retry transient errors (rate limits, 5xx, timeouts) themselves; a request that still
fails aborts the job (`error`), since a CBOM built from a subset of the chunks would look like a tool that found
less. An unparsable answer is retried once as two halves. Chunks past the deadline raise a TimeoutError (`timeout`).

The model answers in free text ("GCM", "PSS", assetType "key"); `assemble_cbom` maps every value onto the
CycloneDX 1.7 enums so the CBOM is valid. A value without an enum equivalent becomes `other` and the model's
original is kept in a `bf-cbom:<field>` property, so nothing is lost (ASSEMBLY_VERSION, recorded in the CBOM). The two
1.7 fields bound to the CycloneDX registry (`algorithmFamily`, `ellipticCurve`) have no `other`: a value outside the
registry is left out of the field and kept in `bf-cbom:algorithm-family` / `bf-cbom:curve`. The prompt lists the
registry's algorithm families, so the model can name them as a 1.7 generator should.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import RepoInfo, Trace

logger = logging.getLogger(__name__)

PROMPT_VERSION = "2026-10-20.1"  # prompt + file-selection version (recorded in the CBOM metadata); .1: 1.7 families
ASSEMBLY_VERSION = "2026-10-20.1"  # how answers become CycloneDX (in the CBOM metadata); .1: CycloneDX 1.7

# The CycloneDX 1.7 registry of algorithm families and elliptic curves. Vendored once, for the analysis, in
# analysis/data/cyclonedx/; the LLM worker images copy this one file to the same path.
CDX_DEFS = Path(
    os.getenv(
        "CYCLONEDX_DEFS",
        str(Path(__file__).resolve().parents[1] / "analysis" / "data" / "cyclonedx" / "cryptography-defs.schema.json"),
    )
)


def _registry(path: Path = CDX_DEFS) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(algorithm families, elliptic-curve identifiers `<category>/<name>`) of the CycloneDX 1.7 registry."""
    defs = json.loads(path.read_text(encoding="utf-8"))["definitions"]
    return tuple(defs["algorithmFamiliesEnum"]["enum"]), tuple(defs["ellipticCurvesEnum"]["enum"])


CDX_FAMILIES, CDX_CURVES = _registry()

LLM_MAX_FILES = int(os.getenv("LLM_MAX_FILES", "1000"))
LLM_MAX_CHUNK_CHARS = int(os.getenv("LLM_MAX_CHUNK_CHARS", "40000"))  # ~10k tokens of code per request
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8192"))  # long asset lists must not be cut off
LLM_MAX_CHUNKS = int(os.getenv("LLM_MAX_CHUNKS", "1000"))  # 100 cut bisq2 (312 chunks), bisq, parse-server, smallstep
LLM_MAX_FILE_BYTES = int(os.getenv("LLM_MAX_FILE_BYTES", "400000"))
LLM_CONCURRENCY = max(1, int(os.getenv("LLM_CONCURRENCY", "4")))  # requests in flight per job
LLM_MAX_RETRIES = max(0, int(os.getenv("LLM_MAX_RETRIES", "3")))  # transient API errors, retried by the provider


def _temperature_from_env() -> float | None:
    """LLM_TEMPERATURE: a number to send as `temperature`; unset or empty: the parameter is omitted."""
    value = os.getenv("LLM_TEMPERATURE", "").strip()
    return float(value) if value else None


LLM_TEMPERATURE = _temperature_from_env()
OPENAI_MAX_TOKENS_PARAM = os.getenv("OPENAI_MAX_TOKENS_PARAM", "max_tokens").strip() or "max_tokens"

SOURCE_EXTENSIONS: dict[str, set[str]] = {
    "python": {".py"},
    "java": {".java", ".kt", ".scala"},
    "go": {".go"},
    "javascript": {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"},
    "c": {".c", ".h", ".cc", ".cpp", ".hpp", ".cxx"},
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
    "go": re.compile(
        r"\b(crypto/(aes|cipher|des|dsa|ecdsa|ecdh|ed25519|elliptic|hmac|md5|rand|rc4|rsa|sha1|sha256|sha3|sha512|"
        r"subtle|tls|x509)|golang\.org/x/crypto|filippo\.io|cloudflare/circl|crypto\.SHA\d+|rsa\.|ecdsa\.|ed25519\.|"
        r"aes\.|cipher\.|hmac\.|sha256\.|sha512\.|md5\.|pbkdf2\.|scrypt\.|argon2\.|hkdf\.|chacha20|nacl|tls\.Config|"
        r"x509\.|GenerateKey|NewGCM|NewCBC|NewCTR|Sum256|Sum512)\b"
    ),
    "javascript": re.compile(
        r"\b(crypto|node:crypto|webcrypto|subtle|crypto-js|jsonwebtoken|jose|bcrypt(js)?|argon2|tweetnacl|libsodium|"
        r"sodium-native|forge|elliptic|secp256k1|noble|createHash|createHmac|createCipheriv|createDecipheriv|createSign|"
        r"createVerify|generateKeyPair(Sync)?|publicEncrypt|privateDecrypt|pbkdf2(Sync)?|scrypt(Sync)?|hkdf(Sync)?|"
        r"randomBytes|sign|verify|encrypt|decrypt|digest|importKey|exportKey|deriveKey|deriveBits|SHA-?\d+|MD5|AES|RSA|"
        r"ECDSA|Ed25519|X25519|HMAC|TLS|https)\b"
    ),
    "c": re.compile(
        r"\b(openssl/|EVP_[A-Za-z0-9_]+|RAND_bytes|HMAC(_[A-Za-z]+)?|SHA\d*_(Init|Update|Final)|MD5_(Init|Update|Final)|"
        r"AES_[a-z_]+|RSA_[a-z_]+|EC_KEY_[a-z_]+|ECDSA_[a-z_]+|PKCS5_PBKDF2_HMAC|X509_[a-z_]+|SSL_CTX_[a-z_]+|SSL_[a-z_]+|"
        r"gcrypt|gnutls|mbedtls_[a-z0-9_]+|wolfSSL|sodium_[a-z_]+|crypto_[a-z0-9_]+|br_[a-z0-9_]+|nettle_[a-z0-9_]+|"
        r"CryptEncrypt|BCrypt[A-Za-z]+|SecKey[A-Za-z]+|CC[A-Za-z]*Crypt[A-Za-z]*|libtomcrypt)\b"
    ),
}
# "other" languages: the union of both pattern sets
_ANY_PATTERN = re.compile("|".join(p.pattern for p in CRYPTO_API_PATTERNS.values()))

SYSTEM_PROMPT = (
    """You are a cryptography auditor producing a Cryptography Bill of Materials (CBOM).
You receive source files of one software project, each line prefixed with its file path and line number.
List every cryptographic asset that this code USES or CONFIGURES: algorithms (ciphers, hashes, MACs, signatures,
key agreement, key derivation, random number generation), keys and key material, certificates, and protocols
(TLS, SSH, JWT/JWS/JWE). Report only what the shown code actually does; do not infer from library names alone,
do not list general-purpose libraries, and do not invent locations.

Answer with ONE JSON object and nothing else:
{"assets": [
  {"name": "<algorithm or asset name as used, e.g. AES, SHA-256, RSA, HMAC-SHA256, TLS, X.509>",
   "assetType": "algorithm|key|certificate|protocol|related-crypto-material",
   "primitive": "<for algorithms: block-cipher|stream-cipher|hash|mac|signature|pke|kem|key-agree|kdf|drbg|ae|key-wrap|
                 other, else null>",
   "algorithmFamily": "<for algorithms: one of the CycloneDX algorithm families listed below, or null>",
   "keySize": <bits as integer or null>, "curve": "<e.g. P-256, secp256k1, Ed25519 or null>",
   "mode": "<e.g. GCM, CBC, CTR or null>", "padding": "<e.g. PKCS7, OAEP, PKCS1v15 or null>",
   "digest": "<hash used inside a MAC/signature/KDF, e.g. SHA-256, or null>",
   "file": "<path exactly as given>", "line": <integer line number of the call site>,
   "evidence": "<the code fragment on that line that shows the use>",
   "confidence": "high|medium|low"}
]}
One entry per call site. If a value is not visible in the code, use null. If there is nothing to report,
answer {"assets": []}.
CycloneDX algorithm families: """
    + ", ".join(CDX_FAMILIES)
    + "."
)


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
    return {
        "python": "python",
        "java": "java",
        "kotlin": "java",
        "scala": "java",
        "go": "go",
        "golang": "go",
        "javascript": "javascript",
        "typescript": "javascript",
        "c": "c",
        "c++": "c",
        "cpp": "c",
    }.get(lang)


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
        """Return (text, usage), usage as built by `request_usage`."""
        ...


def request_usage(
    *,
    model: str | None,
    input_tokens=0,
    output_tokens=0,
    cached_input_tokens=0,
    cache_write_tokens=0,
    reasoning_tokens=0,
) -> dict:
    """Usage of one request in provider-independent terms. `input_tokens` is everything the request sent (cached
    tokens included), `cached_input_tokens` the part served from the provider's prompt cache (priced lower),
    `cache_write_tokens` the part written to it (Anthropic prices writes higher), `reasoning_tokens` the share of
    the output spent on reasoning where reported. `model` is the id the provider answered with."""
    return {
        "model": model,
        "input_tokens": int(input_tokens or 0),
        "cached_input_tokens": int(cached_input_tokens or 0),
        "cache_write_tokens": int(cache_write_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "reasoning_tokens": int(reasoning_tokens or 0),
    }


class OpenAICompatProvider:
    """Any OpenAI-compatible chat API (DeepSeek, OpenAI, ...), JSON mode. `temperature` is sent only when set;
    `max_tokens_param` names the output-limit parameter (OpenAI reasoning models need max_completion_tokens)."""

    def __init__(
        self,
        name: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 180.0,
        max_output_tokens: int = LLM_MAX_OUTPUT_TOKENS,
        temperature: float | None = LLM_TEMPERATURE,
        max_tokens_param: str = OPENAI_MAX_TOKENS_PARAM,
        client=None,
    ):
        self.name, self.model, self.max_output_tokens = name, model, max_output_tokens
        self.temperature, self.max_tokens_param = temperature, max_tokens_param
        if client is not None:
            self._client = client
            return
        from openai import OpenAI  # imported lazily so the module stays importable without the SDK

        # the SDK retries 408/409/429/5xx and connection errors with backoff; anything that still escapes fails the job
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=LLM_MAX_RETRIES)

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        kwargs: dict = {
            "model": self.model,
            self.max_tokens_param: self.max_output_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self._client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        return resp.choices[0].message.content or "", request_usage(
            model=getattr(resp, "model", None) or self.model,
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            # DeepSeek reports cache hits at the top level, OpenAI under prompt_tokens_details
            cached_input_tokens=getattr(usage, "prompt_cache_hit_tokens", None)
            or getattr(prompt_details, "cached_tokens", 0),
            reasoning_tokens=getattr(completion_details, "reasoning_tokens", 0),
        )


class AnthropicProvider:
    """Anthropic Messages API over HTTPS (requests); `temperature` is sent only when set (Claude Sonnet 5.5 and
    Opus 5.5 reject non-default sampling parameters). JSON is requested in the prompt and the
    answer is parsed by `parse_assets`, which tolerates code fences."""

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    RETRY_STATUSES = (408, 409, 429, 500, 502, 503, 504, 529)  # 529 = overloaded

    def __init__(
        self,
        name: str,
        model: str,
        api_key: str,
        timeout: float = 180.0,
        max_output_tokens: int = LLM_MAX_OUTPUT_TOKENS,
        post=None,
        temperature: float | None = LLM_TEMPERATURE,
        sleep=time.sleep,
    ):
        import requests

        self.name, self.model, self.timeout, self.max_output_tokens = name, model, timeout, max_output_tokens
        self.temperature, self._sleep = temperature, sleep
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
            "system": system + "\nAnswer with the JSON object only, no prose, no code fences.",
            "messages": [{"role": "user", "content": user}],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        resp = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            resp = self._post(self.API_URL, headers=self._headers, json=payload, timeout=self.timeout)
            if resp.status_code == 200 or resp.status_code not in self.RETRY_STATUSES or attempt == LLM_MAX_RETRIES:
                break
            self._sleep(min(2.0**attempt, 30.0))  # 1, 2, 4 s: rate limits and overload are transient
        if resp.status_code != 200:
            raise RuntimeError(f"anthropic api {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        return text, request_usage(
            model=data.get("model") or self.model,
            # Anthropic's input_tokens exclude the cached parts; the total is what the request sent
            input_tokens=int(usage.get("input_tokens") or 0) + cache_read + cache_write,
            output_tokens=usage.get("output_tokens"),
            cached_input_tokens=cache_read,
            cache_write_tokens=cache_write,
        )


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


_ASSET_TYPES = {"algorithm", "key", "certificate", "protocol", "related-crypto-material"}  # what the prompt allows

# CycloneDX 1.7 enum values the model's free text is mapped to (`other`/`unknown` are valid too)
CDX_PRIMITIVES = ("drbg", "mac", "block-cipher", "stream-cipher", "signature", "hash", "pke", "xof", "kdf",
                  "key-agree", "kem", "ae", "combiner", "key-wrap")  # fmt: skip
CDX_MODES = ("cbc", "ecb", "ccm", "gcm", "cfb", "ofb", "ctr")
CDX_PADDINGS = ("pkcs5", "pkcs7", "pkcs1v15", "oaep", "raw")
_SYNONYMS = {
    "primitive": {
        "blockcipher": "block-cipher",
        "streamcipher": "stream-cipher",
        "digest": "hash",
        "aead": "ae",
        "keyagreement": "key-agree",
        "keyexchange": "key-agree",
        "kex": "key-agree",
        "rng": "drbg",
        "prng": "drbg",
        "csprng": "drbg",
        "keyderivation": "kdf",
        "keywrap": "key-wrap",
        "keywrapping": "key-wrap",
    },  # fmt: skip
    "mode": {"cfb1": "cfb", "cfb8": "cfb", "cfb64": "cfb", "cfb128": "cfb"},
    "padding": {
        "pkcs5padding": "pkcs5",
        "pkcs7padding": "pkcs7",
        "pkcs1": "pkcs1v15",
        "pkcs1padding": "pkcs1v15",
        "pkcs1v15padding": "pkcs1v15",
        "pkcs115": "pkcs1v15",
        "nopadding": "raw",
        "none": "raw",
        "oaeppadding": "oaep",
    },  # fmt: skip
}
# related-crypto-material type from the asset name ("RSA private key" -> private-key), first match wins
_MATERIAL_WORDS = (("private", "private-key"), ("public", "public-key"), ("secret", "secret-key"),
                   ("symmetric", "secret-key"), ("salt", "salt"), ("nonce", "nonce"), ("password", "password"),
                   ("passphrase", "password"), ("seed", "seed"), ("token", "token"))  # fmt: skip
_OAEP_DIGEST = re.compile(r"OAEPWith(.+?)AndMGF1", re.IGNORECASE)


def _fold(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def to_cdx_enum(field_name: str, value: str | None, allowed: tuple[str, ...]) -> tuple[str | None, str | None]:
    """(CycloneDX 1.7 value, the model's original when it had to become `other`) for a free-text value."""
    if value is None:
        return None, None
    key = _fold(value)
    if key in ("other", "unknown"):
        return key, None
    for a in allowed:
        if _fold(a) == key:
            return a, None
    if key in _SYNONYMS.get(field_name, {}):
        return _SYNONYMS[field_name][key], None
    if field_name == "padding" and key.startswith("oaepwith"):
        return "oaep", None
    return "other", value


def to_registry(value: str | None, allowed: tuple[str, ...]) -> tuple[str | None, str | None]:
    """(registry value, the model's original when it has none) for `algorithmFamily` or `ellipticCurve`: the same
    spelling up to case and separators; for a curve also its name without the `<category>/` prefix (P-256 ->
    nist/P-256, secp256r1 -> secg/secp256r1; the names are unique across categories). No synonyms, no guessing."""
    if value is None:
        return None, None
    key = _fold(value)
    for a in allowed:
        if _fold(a) == key or ("/" in a and _fold(a.split("/", 1)[1]) == key):
            return a, None
    return None, value


def material_type(name: str) -> str | None:
    text = name.lower()
    if re.search(r"(^|[^a-z])ivs?([^a-z]|$)|initiali[sz]ation vector", text):
        return "initialization-vector"
    return next((t for word, t in _MATERIAL_WORDS if word in text), None)


def _normalize_asset(a: dict) -> dict | None:
    name = _str_or_none(a.get("name"))
    if not name:
        return None
    raw_type = _str_or_none(a.get("assetType")) or "algorithm"
    asset_type = raw_type.lower() if raw_type.lower() in _ASSET_TYPES else "related-crypto-material"
    return {
        "name": name,
        "assetType": asset_type,
        "assetTypeRaw": raw_type,
        "primitive": _str_or_none(a.get("primitive")),
        "algorithmFamily": _str_or_none(a.get("algorithmFamily")),
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
    chunks_sent: int = 0  # successful requests
    requests_failed: int = 0  # requests that raised (network / API errors) after the provider's retries
    chunks_total: int = 0  # chunks the selected files render to; chunks_skipped of them were over LLM_MAX_CHUNKS
    chunks_skipped: int = 0
    input_tokens: int = 0  # sums over the successful requests, see request_usage
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    models: dict[str, int] = field(default_factory=dict)  # model id the provider answered with -> requests
    assets_raw: int = 0
    assets_unparsable_chunks: int = 0
    chunks_split: int = 0  # chunks re-sent as two halves after an unparsable answer
    locations_verified: int = 0
    locations_unverified: int = 0
    values_not_in_enum: int = 0  # model values kept in bf-cbom:<field> because CycloneDX 1.7 has no such value
    notes: list[str] = field(default_factory=list)

    def add_usage(self, usage: dict) -> None:
        for key in ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens"):
            setattr(self, key, getattr(self, key) + int(usage.get(key) or 0))
        if usage.get("model"):
            self.models[str(usage["model"])] = self.models.get(str(usage["model"]), 0) + 1

    def usage(self) -> dict:
        """What JobResult.usage holds: every request counts (failed ones too, the provider may have billed them),
        token sums per kind, and the model ids answered with."""
        return {
            "requests": self.chunks_sent + self.requests_failed,
            "requests_failed": self.requests_failed,
            "chunks_total": self.chunks_total,
            "chunks_sent": self.chunks_sent,
            "chunks_skipped": self.chunks_skipped,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "models": dict(self.models),
        }


def assemble_cbom(
    assets: list[dict],
    *,
    tool_name: str,
    model: str,
    repo_info: RepoInfo | None,
    repo_path: Path | None,
    stats: GenerationStats,
) -> dict:
    """CycloneDX 1.7 CBOM from normalised assets; duplicates (same asset at the same place) collapse."""
    components: list[dict] = []
    seen: set[tuple] = set()
    for raw in assets:
        a = _normalize_asset(raw)
        if a is None:
            continue
        key = (
            a["name"].lower(),
            a["assetType"],
            a["algorithmFamily"],
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
        primitive, primitive_raw = to_cdx_enum("primitive", a["primitive"], CDX_PRIMITIVES)
        mode, mode_raw = to_cdx_enum("mode", a["mode"], CDX_MODES)
        padding, padding_raw = to_cdx_enum("padding", a["padding"], CDX_PADDINGS)
        family, family_raw = to_registry(a["algorithmFamily"], CDX_FAMILIES)
        curve, curve_raw = to_registry(a["curve"], CDX_CURVES)
        digest = a["digest"]
        if not digest and a["padding"] and (m := _OAEP_DIGEST.search(a["padding"])):
            digest = m.group(1)  # "OAEPWithSHA-256AndMGF1Padding" -> SHA-256
        asset_type = a["assetType"]
        material = None
        if asset_type == "key":  # not a CycloneDX asset type: keys are related crypto material
            asset_type, material = "related-crypto-material", material_type(a["name"]) or "key"
        elif asset_type == "related-crypto-material":
            material = material_type(a["name"])
        algo = {
            k: v
            for k, v in {
                "algorithmFamily": family,
                "primitive": primitive,
                "parameterSetIdentifier": str(a["keySize"]) if a["keySize"] else None,
                "ellipticCurve": curve,
                "mode": mode,
                "padding": padding,
            }.items()
            if v
        }
        crypto: dict = {"assetType": asset_type}
        if asset_type == "algorithm" and algo:
            crypto["algorithmProperties"] = algo
        elif asset_type == "related-crypto-material" and (material or a["keySize"]):
            crypto["relatedCryptoMaterialProperties"] = {
                k: v for k, v in {"type": material, "size": a["keySize"]}.items() if v
            }
        props = [{"name": "bf-cbom:confidence", "value": a["confidence"]}]
        if digest:
            props.append({"name": "bf-cbom:digest", "value": digest})
        if a["evidence"]:
            props.append({"name": "bf-cbom:evidence", "value": a["evidence"]})
        originals = {"asset-type": a["assetTypeRaw"] if asset_type != a["assetTypeRaw"].lower() else None}
        if asset_type == "algorithm":  # the other fields are only written for algorithms
            originals.update(
                {
                    "primitive": primitive_raw,
                    "algorithm-family": family_raw,
                    "curve": curve_raw,
                    "mode": mode_raw,
                    "padding": padding_raw,
                }
            )
        for name, value in originals.items():
            if value:
                props.append({"name": f"bf-cbom:{name}", "value": value})
                stats.values_not_in_enum += 1
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
            {"name": "bf-cbom:temperature", "value": "default" if LLM_TEMPERATURE is None else str(LLM_TEMPERATURE)},
            {"name": "bf-cbom:assembly-version", "value": ASSEMBLY_VERSION},
            {"name": "bf-cbom:files-candidates", "value": str(stats.files_candidates)},
            {"name": "bf-cbom:files-matched", "value": str(stats.files_matched)},
            {"name": "bf-cbom:files-sent", "value": str(stats.files_sent)},
            {"name": "bf-cbom:chunks-total", "value": str(stats.chunks_total)},
            {"name": "bf-cbom:chunks-skipped", "value": str(stats.chunks_skipped)},
            {"name": "bf-cbom:requests", "value": str(stats.chunks_sent + stats.requests_failed)},
            {"name": "bf-cbom:requests-failed", "value": str(stats.requests_failed)},
            {"name": "bf-cbom:chunks-split", "value": str(stats.chunks_split)},
            {"name": "bf-cbom:input-tokens", "value": str(stats.input_tokens)},
            {"name": "bf-cbom:cached-input-tokens", "value": str(stats.cached_input_tokens)},
            {"name": "bf-cbom:cache-write-tokens", "value": str(stats.cache_write_tokens)},
            {"name": "bf-cbom:output-tokens", "value": str(stats.output_tokens)},
            {"name": "bf-cbom:reasoning-tokens", "value": str(stats.reasoning_tokens)},
            {"name": "bf-cbom:models-answered", "value": ";".join(sorted(stats.models)) or model},
            {"name": "bf-cbom:values-not-in-enum", "value": str(stats.values_not_in_enum)},
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
        "specVersion": "1.7",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": metadata,
        "components": components,
    }


class LLMRequestError(RuntimeError):
    """A request still failed after the provider's retries; the job is an `error`, never a partial CBOM."""


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
    concurrency: int = LLM_CONCURRENCY,
) -> tuple[dict, GenerationStats]:
    """Select, chunk, query and assemble. `concurrency` requests are in flight at once; assets keep chunk order.

    `deadline` is a time.monotonic() value: a chunk not started by then raises TimeoutError (the run is a `timeout`,
    never a partial `ok`). A request that fails after the provider's retries raises LLMRequestError (the run is an
    `error`); an unparsable answer is retried once as two halves. Usage is kept in `trace.fields` after every
    request, so failed and killed jobs still report what was billed."""
    repo_path = Path(repo_path)
    trace = trace or Trace()
    stats = GenerationStats()
    sel = select_files(repo_path, language, max_files=max_files)
    stats.files_candidates, stats.files_matched, stats.files_sent = sel.candidates, sel.matched, len(sel.files)
    chunks = render_chunks(repo_path, sel.files, max_chars=max_chunk_chars)
    stats.chunks_total = len(chunks)
    if len(chunks) > max_chunks:
        stats.chunks_skipped = len(chunks) - max_chunks
        trace.add(f"llm: {len(chunks)} chunks exceed LLM_MAX_CHUNKS={max_chunks}; sending the first {max_chunks}")
        chunks = chunks[:max_chunks]
    project = repo_info.full_name if repo_info else repo_path.name
    lock = threading.Lock()

    def ask(label: str, chunk: str) -> list[dict] | None:
        """One request; None when the answer could not be parsed; LLMRequestError when the request failed."""
        user = f"Project: {project}\n{label}\n\n{chunk}"
        try:
            text, usage = provider.complete(SYSTEM_PROMPT, user)
        except Exception as e:
            with lock:
                stats.requests_failed += 1
                trace.fields["usage"] = stats.usage()
                trace.add(f"llm: {label} failed: {type(e).__name__}: {str(e)[:200]}")
            raise LLMRequestError(
                f"llm: request failed ({label.rstrip('.')}) to {provider.model}: {type(e).__name__}: {str(e)[:300]}"
            ) from e
        with lock:
            stats.chunks_sent += 1
            stats.add_usage(usage)
            trace.fields["usage"] = stats.usage()  # after every request, so a failed or killed job keeps its usage
        try:
            return parse_assets(text)
        except (json.JSONDecodeError, ValueError) as e:
            with lock:
                trace.add(f"llm: {label} answer not parsable: {e}")
            return None

    def work(i: int, chunk: str) -> list[dict]:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(f"llm_deadline: chunk {i} of {len(chunks)} not started before the job deadline")
        found = ask(f"Chunk {i} of {len(chunks)}.", chunk)
        if found is None:
            # Usually a truncated or malformed JSON answer for a dense chunk: retry once with two halves.
            halves = split_chunk(chunk)
            found = []
            if len(halves) == 2:
                with lock:
                    stats.chunks_split += 1
                for j, half in enumerate(halves, start=1):
                    part = ask(f"Chunk {i} of {len(chunks)}, part {j} of 2.", half)
                    if part is None:
                        with lock:
                            stats.assets_unparsable_chunks += 1
                    else:
                        found.extend(part)
            else:
                with lock:
                    stats.assets_unparsable_chunks += 1
        with lock:
            stats.assets_raw += len(found)
        if on_progress:
            on_progress()
        return found

    assets: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(chunks)))) as pool:
        futures = [pool.submit(work, i, chunk) for i, chunk in enumerate(chunks, start=1)]
        try:
            for fut in futures:  # submission order = chunk order
                assets.extend(fut.result())
        except BaseException:
            for fut in futures:  # fail fast: nothing queued behind the failure is sent
                fut.cancel()
            raise
    cbom = assemble_cbom(
        assets, tool_name=provider.name, model=provider.model, repo_info=repo_info, repo_path=repo_path, stats=stats
    )
    trace.add(
        f"llm: files {stats.files_sent}/{stats.files_matched} matched of {stats.files_candidates}; "
        f"requests {stats.chunks_sent} ok, {stats.requests_failed} failed, for {stats.chunks_total} chunks "
        f"({stats.chunks_split} split); "
        f"tokens {stats.input_tokens} in ({stats.cached_input_tokens} cached) + {stats.output_tokens} out; "
        f"assets {len(cbom['components'])} (raw {stats.assets_raw}); "
        f"locations verified {stats.locations_verified}, unverified {stats.locations_unverified}"
    )
    return cbom, stats
