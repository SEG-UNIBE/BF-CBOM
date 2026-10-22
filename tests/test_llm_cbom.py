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
        return self.answers.pop(0), llm.request_usage(
            model="fake-1-2026", input_tokens=100, output_tokens=10, cached_input_tokens=40
        )


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
    assert cbom["bomFormat"] == "CycloneDX" and cbom["specVersion"] == "1.7"
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
    assert (stats.input_tokens, stats.cached_input_tokens, stats.output_tokens, stats.chunks_sent) == (100, 40, 10, 1)
    assert stats.usage() == {
        "requests": 1,
        "requests_failed": 0,
        "chunks_total": 1,
        "chunks_sent": 1,
        "chunks_skipped": 0,
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "cache_write_tokens": 0,
        "output_tokens": 10,
        "reasoning_tokens": 0,
        "models": {"fake-1-2026": 1},
    }
    props = {p["name"]: p["value"] for p in cbom["metadata"]["properties"]}
    assert props["bf-cbom:llm-model"] == "fake-1" and props["bf-cbom:input-tokens"] == "100"
    assert props["bf-cbom:models-answered"] == "fake-1-2026" and props["bf-cbom:cached-input-tokens"] == "40"
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
        return '{"assets": []}', llm.request_usage(model="fake-1", input_tokens=1, output_tokens=1)


def test_generate_cbom_fails_when_every_request_fails(repo: Path):
    # an empty "ok" CBOM would look like a tool that found nothing
    with pytest.raises(llm.LLMRequestError, match="request failed .Chunk 1 of .* to fake-1: RuntimeError: anthropic"):
        llm.generate_cbom(FailingProvider(), repo, language="python", max_chunk_chars=120)


def test_a_failed_request_fails_the_job_fast(repo: Path):
    # a CBOM from a subset of the chunks would look like a tool that found less: the job fails at the first failure
    provider = FailingProvider(fail_first=1)
    trace = llm.Trace()
    with pytest.raises(llm.LLMRequestError):
        llm.generate_cbom(provider, repo, language="python", max_chunk_chars=120, concurrency=1, trace=trace)
    assert provider.n == 1  # nothing queued behind the failure was sent
    assert trace.fields["usage"]["requests_failed"] == 1 and trace.fields["usage"]["chunks_total"] > 1
    assert any("Chunk 1 of" in line and "failed" in line for line in trace.text().splitlines())


class LabelProvider:
    """Answers one asset named after the chunk label, so the order of the assembled CBOM can be checked."""

    name = "fake"
    model = "fake-1"

    def complete(self, system, user):
        import re
        import time

        i = int(re.search(r"Chunk (\d+) of", user).group(1))
        time.sleep(0.01 * ((7 * i) % 4))  # finish out of order
        return json.dumps({"assets": [{"name": f"ALG{i}", "assetType": "algorithm"}]}), llm.request_usage(
            model="fake-1", input_tokens=1, output_tokens=1
        )


def test_concurrent_requests_keep_chunk_order(repo: Path):
    cbom, stats = llm.generate_cbom(LabelProvider(), repo, language="python", max_chunk_chars=120, concurrency=4)
    n = stats.chunks_total
    assert n > 2 and stats.chunks_sent == n
    assert [c["name"] for c in cbom["components"]] == [f"ALG{i}" for i in range(1, n + 1)]


def test_deadline_is_a_timeout_not_a_partial_result(repo: Path):
    import time

    provider = FakeProvider(['{"assets": []}'] * 10)
    with pytest.raises(TimeoutError, match="llm_deadline"):
        llm.generate_cbom(provider, repo, language="python", max_chunk_chars=120, deadline=time.monotonic() - 1)


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


def test_generate_cbom_respects_max_chunks_and_records_the_cut(repo: Path):
    provider = FakeProvider(['{"assets": []}'] * 2)
    cbom, stats = llm.generate_cbom(provider, repo, language="python", max_chunk_chars=120, max_chunks=2)
    assert stats.chunks_sent == 2 and stats.chunks_total > 2
    assert stats.chunks_skipped == stats.chunks_total - 2 == stats.usage()["chunks_skipped"]
    props = {p["name"]: p["value"] for p in cbom["metadata"]["properties"]}
    assert props["bf-cbom:chunks-skipped"] == str(stats.chunks_skipped)


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
            {
                "model": "claude-sonnet-5-5-20261001",
                "content": [{"type": "text", "text": '{"assets": []}'}],
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 5,
                },
            },
        )

    p = llm.AnthropicProvider(name="claude", model="claude-sonnet-5-5", api_key="k", timeout=9, post=fake_post)
    text, usage = p.complete("SYS", "USER")
    assert text == '{"assets": []}' and usage == {
        "model": "claude-sonnet-5-5-20261001",
        "input_tokens": 32,  # Anthropic's input_tokens exclude the cached parts; we report the total sent
        "cached_input_tokens": 20,
        "cache_write_tokens": 5,
        "output_tokens": 3,
        "reasoning_tokens": 0,
    }
    url, headers, body, timeout = calls[0]
    assert url.endswith("/v1/messages") and headers["x-api-key"] == "k" and timeout == 9
    assert body["model"] == "claude-sonnet-5-5" and body["messages"][0]["content"] == "USER"
    assert "temperature" not in body  # not sent unless configured (Sonnet 5.5 rejects non-default sampling)
    assert body["system"].startswith("SYS")

    p0 = llm.AnthropicProvider(name="claude", model="claude-sonnet-4-6", api_key="k", post=fake_post, temperature=0.0)
    p0.complete("SYS", "USER")
    assert calls[-1][2]["temperature"] == 0.0

    attempts = []

    def failing_post(url, headers, json, timeout):
        attempts.append(1)
        return _Resp(429, {"error": "rate limited"})

    slept = []
    retrying = llm.AnthropicProvider(name="claude", model="m", api_key="k", post=failing_post, sleep=slept.append)
    with pytest.raises(RuntimeError, match="anthropic api 429"):
        retrying.complete("S", "U")
    assert len(attempts) == llm.LLM_MAX_RETRIES + 1 and slept == [1.0, 2.0, 4.0][: llm.LLM_MAX_RETRIES]

    def rejecting_post(url, headers, json, timeout):
        attempts.append(2)
        return _Resp(400, {"error": "bad request"})

    attempts.clear()
    with pytest.raises(RuntimeError, match="anthropic api 400"):
        llm.AnthropicProvider(name="claude", model="m", api_key="k", post=rejecting_post, sleep=slept.append).complete(
            "S", "U"
        )
    assert attempts == [2]  # a 400 is not transient: no retry


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

            class _PromptDetails:
                cached_tokens = 4

            class _CompletionDetails:
                reasoning_tokens = 1

            class _Usage:
                prompt_tokens, completion_tokens = 5, 2
                prompt_tokens_details = _PromptDetails()
                completion_tokens_details = _CompletionDetails()

            class _R:
                model = "gpt-5-2026-01-01"
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
    assert text == '{"assets": []}' and usage == {
        "model": "gpt-5-2026-01-01",
        "input_tokens": 5,
        "cached_input_tokens": 4,
        "cache_write_tokens": 0,
        "output_tokens": 2,
        "reasoning_tokens": 1,
    }


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
    ):
        sel = select_files(root / sub, lang)
        assert [p.name for p in sel.files] == [expected], lang


def test_model_values_become_valid_cyclonedx_17(repo: Path):
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
                {"name": "ECDSA", "assetType": "algorithm", "algorithmFamily": "ecdsa", "curve": "P-256"},
                {"name": "ECDH", "assetType": "algorithm", "algorithmFamily": "ECDH", "curve": "X25519"},
                {"name": "RSA-2048", "assetType": "algorithm", "algorithmFamily": "RSA", "keySize": 2048},
                {"name": "salt", "assetType": "related-crypto-material"},
                {"name": "crypto-js", "assetType": "library"},
            ]
        }
    )
    cbom, stats = llm.generate_cbom(FakeProvider([answer]), repo, language="Python")
    from analysis.schema import validate_document  # the official 1.7 schema with the registry (analysis/data/cyclonedx)

    assert validate_document(cbom) == {"schema": "1.7", "valid": True, "errors": 0, "error_kinds": {}}
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
    assert (kw["primitive"], kw["mode"]) == ("key-wrap", "other")  # key-wrap is a 1.7 primitive; KW is no mode
    assert "bf-cbom:primitive" not in props("A128KW") and props("A128KW")["bf-cbom:mode"] == "KW"
    # the registry-bound 1.7 fields: the registry's spelling, else left out and the model's value kept
    ecdsa = comps["ECDSA"]["cryptoProperties"]["algorithmProperties"]
    assert ecdsa == {"algorithmFamily": "ECDSA", "ellipticCurve": "nist/P-256"}
    ecdh = comps["ECDH"]["cryptoProperties"]["algorithmProperties"]
    assert ecdh == {"algorithmFamily": "ECDH"} and props("ECDH")["bf-cbom:curve"] == "X25519"
    rsa = comps["RSA-2048"]["cryptoProperties"]["algorithmProperties"]
    assert rsa == {"parameterSetIdentifier": "2048"} and props("RSA-2048")["bf-cbom:algorithm-family"] == "RSA"
    assert "algorithmFamily" in llm.SYSTEM_PROMPT and "RSASSA-PSS" in llm.SYSTEM_PROMPT  # the registry is offered
    assert comps["salt"]["cryptoProperties"]["relatedCryptoMaterialProperties"] == {"type": "salt"}
    assert comps["crypto-js"]["cryptoProperties"] == {"assetType": "related-crypto-material"}
    assert props("crypto-js")["bf-cbom:asset-type"] == "library"
    meta = {p["name"]: p["value"] for p in cbom["metadata"]["properties"]}
    assert meta["bf-cbom:assembly-version"] == llm.ASSEMBLY_VERSION
    assert (
        meta["bf-cbom:values-not-in-enum"] == str(stats.values_not_in_enum) == "7"
    )  # key x2, PSS, KW, library, curve X25519, family RSA
