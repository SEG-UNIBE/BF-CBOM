"""Tests for common/llm_cbom.py with a fake provider (no network)."""

import json
from pathlib import Path

import pytest

from common import llm_cbom as llm
from common.models import RepoInfo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "crypto_util.py").write_text(
        "import hashlib\nfrom cryptography.hazmat.primitives.ciphers.aead import AESGCM\n\n"
        "def digest(b):\n    return hashlib.sha256(b).hexdigest()\n\n"
        "def enc(key, n, d):\n    return AESGCM(key).encrypt(n, d, None)\n"
    )
    (tmp_path / "pkg" / "plain.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("import hashlib\n")
    (tmp_path / "README.md").write_text("hashlib everywhere\n")
    return tmp_path


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls: list[str] = []

    def complete(self, system, user):
        self.calls.append(user)
        return self.answers.pop(0), {"prompt_tokens": 100, "completion_tokens": 10}


def test_select_files_filters_and_orders(repo: Path):
    sel = llm.select_files(repo, "Python")
    assert [str(p.relative_to(repo)) for p in sel.files] == ["pkg/crypto_util.py"]
    assert (sel.candidates, sel.matched) == (2, 1)  # node_modules and README are not candidates
    sel_any = llm.select_files(repo, None)  # language detected from extensions
    assert sel_any.files == sel.files


def test_render_chunks_numbers_lines_and_splits(repo: Path):
    files = llm.select_files(repo, "python").files
    chunks = llm.render_chunks(repo, files, max_chars=10_000)
    assert len(chunks) == 1
    assert "### FILE pkg/crypto_util.py" in chunks[0]
    assert "pkg/crypto_util.py:5:     return hashlib.sha256(b).hexdigest()" in chunks[0]
    small = llm.render_chunks(repo, files, max_chars=120)
    assert len(small) > 1 and small[1].startswith("### FILE pkg/crypto_util.py (lines ")


def test_parse_assets_tolerates_fences_and_lists():
    assert llm.parse_assets('```json\n{"assets": [{"name": "AES"}]}\n```') == [{"name": "AES"}]
    assert llm.parse_assets('[{"name": "SHA-256"}]') == [{"name": "SHA-256"}]
    assert llm.parse_assets('{"something": 1}') == []
    with pytest.raises(json.JSONDecodeError):
        llm.parse_assets("not json")


def test_generate_cbom_assembles_cyclonedx_and_verifies_locations(repo: Path):
    answer = json.dumps(
        {
            "assets": [
                {
                    "name": "SHA-256",
                    "assetType": "algorithm",
                    "primitive": "hash",
                    "file": "pkg/crypto_util.py",
                    "line": 5,
                    "evidence": "hashlib.sha256(b)",
                    "confidence": "high",
                },
                {
                    "name": "AES",
                    "assetType": "algorithm",
                    "primitive": "ae",
                    "mode": "GCM",
                    "keySize": "256",
                    "file": "pkg/crypto_util.py",
                    "line": 8,
                    "confidence": "high",
                },
                {
                    "name": "AES",
                    "assetType": "algorithm",
                    "primitive": "ae",
                    "mode": "GCM",
                    "keySize": 256,
                    "file": "pkg/crypto_util.py",
                    "line": 8,
                },  # duplicate -> collapsed
                {"name": "RSA", "assetType": "algorithm", "file": "pkg/nope.py", "line": 3},  # bad location
                {"name": "", "file": "x", "line": 1},  # dropped
                {"name": "TLS", "assetType": "protocol"},  # no location
            ]
        }
    )
    provider = FakeProvider([answer])
    ri = RepoInfo(full_name="o/r", git_url="http://gs/o/r.git", branch="bfcbom/x", size_kb=1, commit="a" * 40)
    cbom, stats = llm.generate_cbom(provider, repo, repo_info=ri, language="Python")
    assert cbom["bomFormat"] == "CycloneDX" and cbom["specVersion"] == "1.6"
    assert len(provider.calls) == 1 and "Project: o/r" in provider.calls[0]
    names = [c["name"] for c in cbom["components"]]
    assert names == ["SHA-256", "AES", "RSA", "TLS"]
    aes = cbom["components"][1]
    assert aes["cryptoProperties"]["algorithmProperties"] == {
        "primitive": "ae",
        "parameterSetIdentifier": "256",
        "mode": "gcm",  # the model's "GCM" as the CycloneDX enum value
    }
    assert aes["evidence"]["occurrences"] == [{"location": "pkg/crypto_util.py", "line": 8}]
    rsa = cbom["components"][2]
    assert {"name": "bf-cbom:location-unverified", "value": "true"} in rsa["properties"]
    tls = cbom["components"][3]
    assert {"name": "bf-cbom:location-missing", "value": "true"} in tls["properties"] and "evidence" not in tls
    assert (stats.locations_verified, stats.locations_unverified) == (2, 2)
    assert (stats.prompt_tokens, stats.completion_tokens, stats.chunks_sent) == (100, 10, 1)
    props = {p["name"]: p["value"] for p in cbom["metadata"]["properties"]}
    assert props["bf-cbom:llm-model"] == "fake-1" and props["bf-cbom:prompt-tokens"] == "100"
    assert cbom["metadata"]["component"]["version"] == "a" * 12


def test_generate_cbom_survives_bad_chunk_answers(repo: Path):
    files = llm.select_files(repo, "python").files
    chunks = llm.render_chunks(repo, files, max_chars=120)
    n_splittable = sum(1 for c in chunks if len(llm.split_chunk(c)) == 2)
    # every answer is garbage: each splittable chunk is retried as two halves, then given up
    provider = FakeProvider(["garbage"] * (len(chunks) + 2 * n_splittable))
    cbom, stats = llm.generate_cbom(provider, repo, language="python", max_chunk_chars=120)
    assert cbom["components"] == []
    assert stats.chunks_split == n_splittable
    assert stats.chunks_sent == len(chunks) + 2 * n_splittable
    assert stats.assets_unparsable_chunks == len(chunks) - n_splittable + 2 * n_splittable


class FailingProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, fail_first: int | None = None):
        self.fail_first, self.n = fail_first, 0

    def complete(self, system, user):
        self.n += 1
        if self.fail_first is None or self.n <= self.fail_first:
            raise RuntimeError("anthropic api 400: temperature is not supported")
        return '{"assets": []}', {"prompt_tokens": 1, "completion_tokens": 1}


def test_generate_cbom_fails_when_every_request_fails(repo: Path):
    # an empty "ok" CBOM would look like a tool that found nothing
    with pytest.raises(
        RuntimeError, match="all .* requests to fake-1 failed; last error: RuntimeError: anthropic api 400"
    ):
        llm.generate_cbom(FailingProvider(), repo, language="python", max_chunk_chars=120)


def test_generate_cbom_keeps_partial_results_and_counts_failures(repo: Path):
    cbom, stats = llm.generate_cbom(FailingProvider(fail_first=1), repo, language="python", max_chunk_chars=120)
    assert stats.requests_failed >= 1 and stats.chunks_sent >= 1
    props = {p["name"]: p["value"] for p in cbom["metadata"]["properties"]}
    assert props["bf-cbom:requests-failed"] == str(stats.requests_failed)


def test_truncated_answer_is_retried_in_halves(repo: Path):
    files = llm.select_files(repo, "python").files
    chunk = llm.render_chunks(repo, files, max_chars=10_000)[0]
    assert len(llm.split_chunk(chunk)) == 2
    provider = FakeProvider(
        [
            '{"assets": [{"name": "AES", "file": "pkg/crypto_util.py", "line": 8}, {"name": "SHA-2',  # cut off
            '{"assets": [{"name": "SHA-256", "file": "pkg/crypto_util.py", "line": 5}]}',
            '{"assets": [{"name": "AES", "file": "pkg/crypto_util.py", "line": 8}]}',
        ]
    )
    cbom, stats = llm.generate_cbom(provider, repo, language="python")
    assert [c["name"] for c in cbom["components"]] == ["SHA-256", "AES"]
    assert (stats.chunks_split, stats.chunks_sent, stats.assets_unparsable_chunks) == (1, 3, 0)
    assert "part 1 of 2" in provider.calls[1] and "part 2 of 2" in provider.calls[2]


def test_generate_cbom_respects_max_chunks(repo: Path):
    provider = FakeProvider(['{"assets": []}'] * 2)
    _, stats = llm.generate_cbom(provider, repo, language="python", max_chunk_chars=120, max_chunks=2)
    assert stats.chunks_sent == 2 and stats.chunks_total > 2


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._payload


def test_anthropic_provider_request_and_usage():
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return _Resp(
            200,
            {"content": [{"type": "text", "text": '{"assets": []}'}], "usage": {"input_tokens": 7, "output_tokens": 3}},
        )

    p = llm.AnthropicProvider(name="claude", model="claude-sonnet-5-5", api_key="k", timeout=9, post=fake_post)
    text, usage = p.complete("SYS", "USER")
    assert text == '{"assets": []}' and usage == {"prompt_tokens": 7, "completion_tokens": 3}
    url, headers, body, timeout = calls[0]
    assert url.endswith("/v1/messages") and headers["x-api-key"] == "k" and timeout == 9
    assert body["model"] == "claude-sonnet-5-5" and body["messages"][0]["content"] == "USER"
    assert "temperature" not in body  # not sent unless configured (Sonnet 5.5 rejects non-default sampling)
    assert body["system"].startswith("SYS")

    p0 = llm.AnthropicProvider(name="claude", model="claude-sonnet-4-6", api_key="k", post=fake_post, temperature=0.0)
    p0.complete("SYS", "USER")
    assert calls[-1][2]["temperature"] == 0.0

    def failing_post(url, headers, json, timeout):
        return _Resp(429, {"error": "rate limited"})

    with pytest.raises(RuntimeError, match="anthropic api 429"):
        llm.AnthropicProvider(name="claude", model="m", api_key="k", post=failing_post).complete("S", "U")


def test_openai_provider_temperature_and_max_tokens_param():
    class _Completions:
        def __init__(self):
            self.kwargs = []

        def create(self, **kwargs):
            self.kwargs.append(kwargs)

            class _Msg:
                content = '{"assets": []}'

            class _Choice:
                message = _Msg()

            class _Usage:
                prompt_tokens, completion_tokens = 5, 2

            class _R:
                choices = [_Choice()]
                usage = _Usage()

            return _R()

    comp = _Completions()
    client = type("C", (), {"chat": type("Chat", (), {"completions": comp})()})()

    llm.OpenAICompatProvider(
        name="chatgpt",
        model="gpt-5",
        api_key="k",
        client=client,
        temperature=None,
        max_tokens_param="max_completion_tokens",
    ).complete("S", "U")
    kw = comp.kwargs[-1]
    assert (
        "temperature" not in kw and "max_tokens" not in kw and kw["max_completion_tokens"] == llm.LLM_MAX_OUTPUT_TOKENS
    )

    text, usage = llm.OpenAICompatProvider(
        name="deepseek",
        model="deepseek-chat",
        api_key="k",
        client=client,
        temperature=0.0,
        max_tokens_param="max_tokens",
    ).complete("S", "U")
    kw = comp.kwargs[-1]
    assert kw["temperature"] == 0.0 and kw["max_tokens"] == llm.LLM_MAX_OUTPUT_TOKENS
    assert text == '{"assets": []}' and usage == {"prompt_tokens": 5, "completion_tokens": 2}


def test_temperature_from_env(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    assert llm._temperature_from_env() is None
    monkeypatch.setenv("LLM_TEMPERATURE", " ")
    assert llm._temperature_from_env() is None
    monkeypatch.setenv("LLM_TEMPERATURE", "0")
    assert llm._temperature_from_env() == 0.0


def test_has_api_key_rejects_placeholders():
    from common.llm_worker import has_api_key

    assert has_api_key("sk-real-key")
    assert (
        not has_api_key(None)
        and not has_api_key("")
        and not has_api_key(" your_secret ")
        and not has_api_key("'changeme'")
    )


def test_select_files_covers_all_micro_languages():
    from common.llm_cbom import select_files

    root = Path(__file__).resolve().parents[1] / "corpus" / "micro"
    for lang, sub, expected in (
        ("Java", "java", "CryptoDemo.java"),
        ("Python", "python", "crypto_demo.py"),
        ("Go", "go", "main.go"),
        ("JavaScript", "javascript", "crypto_demo.js"),
        ("C", "c", "crypto_demo.c"),
    ):
        sel = select_files(root / sub, lang)
        assert [p.name for p in sel.files] == [expected], lang


def _cyclonedx_16_validator():
    """Official CycloneDX 1.6 JSON schema (vendored in analysis/data/cyclonedx/) with its local references."""
    jsonschema = pytest.importorskip("jsonschema")
    from referencing import Registry, Resource

    schema_dir = Path(__file__).resolve().parents[1] / "analysis" / "data" / "cyclonedx"
    docs = {n: json.loads((schema_dir / n).read_text(encoding="utf-8")) for n in
            ("bom-1.6.schema.json", "spdx.schema.json", "jsf-0.82.schema.json")}  # fmt: skip
    registry = Registry().with_resources((d["$id"], Resource.from_contents(d)) for d in docs.values())
    return jsonschema.Draft7Validator(docs["bom-1.6.schema.json"], registry=registry)


def test_model_values_become_valid_cyclonedx_16(repo: Path):
    answer = json.dumps(
        {
            "assets": [
                {
                    "name": "RSA private key",
                    "assetType": "key",
                    "keySize": 2048,
                    "file": "pkg/crypto_util.py",
                    "line": 1,
                },
                {"name": "RSA", "assetType": "key", "file": "pkg/crypto_util.py", "line": 2},
                {"name": "RSA-PSS", "assetType": "algorithm", "primitive": "signature", "padding": "PSS"},
                {
                    "name": "RSA-OAEP",
                    "assetType": "algorithm",
                    "primitive": "pke",
                    "padding": "OAEPWithSHA-256AndMGF1Padding",
                },  # fmt: skip
                {
                    "name": "AES",
                    "assetType": "algorithm",
                    "primitive": "blockcipher",
                    "mode": "CBC",
                    "padding": "NoPadding",
                },
                {"name": "A128KW", "assetType": "algorithm", "primitive": "key-wrap", "mode": "KW"},
                {"name": "salt", "assetType": "related-crypto-material"},
                {"name": "crypto-js", "assetType": "library"},
            ]
        }
    )
    cbom, stats = llm.generate_cbom(FakeProvider([answer]), repo, language="Python")
    errors = [f"{list(e.absolute_path)}: {e.message}" for e in _cyclonedx_16_validator().iter_errors(cbom)]
    assert errors == []
    comps = {c["name"]: c for c in cbom["components"]}

    def props(name):
        return {p["name"]: p["value"] for p in comps[name]["properties"]}

    assert comps["RSA private key"]["cryptoProperties"] == {
        "assetType": "related-crypto-material",
        "relatedCryptoMaterialProperties": {"type": "private-key", "size": 2048},
    }
    assert props("RSA private key")["bf-cbom:asset-type"] == "key"
    assert comps["RSA"]["cryptoProperties"]["relatedCryptoMaterialProperties"] == {"type": "key"}
    assert comps["RSA-PSS"]["cryptoProperties"]["algorithmProperties"]["padding"] == "other"
    assert props("RSA-PSS")["bf-cbom:padding"] == "PSS"
    oaep = comps["RSA-OAEP"]
    assert oaep["cryptoProperties"]["algorithmProperties"]["padding"] == "oaep"
    assert props("RSA-OAEP")["bf-cbom:digest"] == "SHA-256" and "bf-cbom:padding" not in props("RSA-OAEP")
    assert comps["AES"]["cryptoProperties"]["algorithmProperties"] == {
        "primitive": "block-cipher", "mode": "cbc", "padding": "raw"}  # fmt: skip
    kw = comps["A128KW"]["cryptoProperties"]["algorithmProperties"]
    assert (kw["primitive"], kw["mode"]) == ("other", "other")
    assert (props("A128KW")["bf-cbom:primitive"], props("A128KW")["bf-cbom:mode"]) == ("key-wrap", "KW")
    assert comps["salt"]["cryptoProperties"]["relatedCryptoMaterialProperties"] == {"type": "salt"}
    assert comps["crypto-js"]["cryptoProperties"] == {"assetType": "related-crypto-material"}
    assert props("crypto-js")["bf-cbom:asset-type"] == "library"
    meta = {p["name"]: p["value"] for p in cbom["metadata"]["properties"]}
    assert meta["bf-cbom:assembly-version"] == llm.ASSEMBLY_VERSION
    assert (
        meta["bf-cbom:values-not-in-enum"] == str(stats.values_not_in_enum) == "6"
    )  # key x2, PSS, key-wrap, KW, library
