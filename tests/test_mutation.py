"""The mutation harness without git (mutation/): catalogs, hosts and callers, imports, insertion, eligibility.

Every snippet x style is rendered into small hosts and must parse; where the toolchain is on PATH the Java and Go
results are compiled too (`javac`, `go build`), otherwise those tests are skipped: mutants in context cannot be
compiled (no project builds), so the templates themselves are checked here.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from analysis.normalize.vocab import load_vocabulary
from mutation.catalog import LANGUAGES, MARKER, STYLES, load_catalog, placeholders
from mutation.insert import apply, mutate, shift
from mutation.lang import Insert, SkipHost, candidate_path, source_file
from mutation.source import GoModule, Tree, _spec_allows, go_satisfies

JAVA_BARE = 'package app;\n\npublic class Host {\n    public static void main(String[] args) {\n        System.out.println("x");\n    }\n}\n'  # noqa: E501
JAVA_CLASH = (
    "package app;\n\nimport java.util.List;\n\npublic class Host {\n    static class Signature {}\n"
    "    void run(String e) {\n        Signature s = new Signature();\n    }\n"
    '    public static void main(String[] args) {\n        new Host().run("e");\n    }\n}\n'
)
PY_BARE = 'def main():\n    print("x")\n\n\nif __name__ == "__main__":\n    main()\n'
PY_DOC = (
    '"""Module."""\nimport os as operating_system\n\npadding = 3\n\n\nclass Box:\n    def run(self, rsa):\n'
    '        """Doc."""\n        return rsa + padding\n\n\ndef main():\n    return Box().run(1)\n'
)
GO_BARE = 'package main\n\nfunc main() {\n\tprintln("x")\n}\n'
GO_CLASH = (
    'package main\n\nimport (\n\t"fmt"\n\t"math/rand"\n)\n\nfunc run(rsa int) {\n\tfmt.Println(rsa, rand.Intn(3))\n}'
    "\n\nfunc main() {\n\trun(1)\n}\n"
)
HOSTS = {"java": [JAVA_BARE, JAVA_CLASH], "python": [PY_BARE, PY_DOC], "go": [GO_BARE, GO_CLASH]}
PATHS = {"java": "app/Host.java", "python": "app.py", "go": "main.go"}


def _context(language: str, f) -> dict:
    return {"siblings": set(), "package_names": f.top_level_names() if language == "go" else set()}


def _mutants(language: str):
    """Every (snippet, form, style, host) of `language` rendered into the small hosts."""
    catalog = load_catalog(language)
    for text in HOSTS[language]:
        for snippet in (*catalog.snippets, *catalog.decoys):
            for form in range(len(snippet.forms)):
                for style in ("direct",) if snippet.decoy else STYLES:
                    f = source_file(language, PATHS[language], text)
                    for host in f.hosts(f.called()):
                        m = mutate(f, host, catalog, snippet, form, style, context=_context(language, f))
                        yield snippet, style, host, m


# ----- catalogs -----


@pytest.mark.parametrize("language", LANGUAGES)
def test_catalog_snippets_are_complete_and_expect_vocabulary_identities(language):
    vocab = load_vocabulary()
    catalog = load_catalog(language)
    assert catalog.version and len(catalog.snippets) == 15
    shared = {"rsa-2048-keygen", "ec-p256-keygen", "ecdsa-p256-sign", "ed25519-sign", "aes-128-gcm", "aes-256-gcm",
              "aes-256-cbc", "rsa-oaep-sha256", "sha-1", "sha-256", "md5", "hmac-sha256", "pbkdf2-hmac-sha256",
              "tls13-context"}  # fmt: skip
    assert shared <= {s.id for s in catalog.snippets}
    for s in catalog.snippets:
        unit, exp = s.unit, s.expected
        if unit is None:
            assert exp.get("protocol") == "TLS" and exp.get("protocol_version") == "1.3"
        else:
            assert unit in (vocab.canonical("algorithm", unit), vocab.canonical("family", unit)), (s.id, unit)
        assert vocab.canonical("family", exp["family"]) == exp["family"] if exp.get("family") else True
        for dim in ("curve", "padding", "mode"):
            if exp.get(dim):
                assert vocab.canonical(dim, exp[dim]) == exp[dim], (s.id, dim)
        for also in s.also:
            assert also in (vocab.canonical("algorithm", also), vocab.canonical("family", also)), (s.id, also)
        for style in STYLES:
            assert s.supports(style), (s.id, style)
        for form in s.forms:
            for style, template in form.templates.items():
                markers = [ln for ln in template.splitlines() if MARKER.search(ln)]
                assert any(ln.rstrip().endswith("@call") for ln in markers), (s.id, style)
                if style == "indirect":
                    assert any(ln.rstrip().endswith("@related") for ln in markers), (s.id, style)
                assert "body" not in placeholders(template)


@pytest.mark.parametrize("language", LANGUAGES)
def test_catalog_decoys_are_known_negatives(language):
    catalog = load_catalog(language)
    assert [d.id for d in catalog.decoys] == ["comment", "log-message", "crc32", "base64", "identifier", "hex-constant",
                                              "url", "cert-path"]  # fmt: skip
    crypto_imports = {i for s in catalog.snippets for f in s.forms for i in f.imports}
    for d in catalog.decoys:
        assert d.decoy and d.expected == {} and d.what and d.supports("direct") and not d.supports("indirect")
        assert catalog.get(d.id) is d and not (set(d.forms[0].imports) & crypto_imports), d.id
        assert not any(MARKER.search(ln) for ln in d.forms[0].templates["direct"].splitlines())  # nothing to find


def test_pyca_snippets_require_cryptography_and_stdlib_ones_nothing():
    catalog = load_catalog("python")
    for s in catalog.snippets:
        imports = " ".join(s.forms[0].imports)
        assert ("cryptography" in imports) == ("cryptography" in s.forms[0].requires), s.id
    go = load_catalog("go").get("pbkdf2-hmac-sha256")
    assert [f.requires for f in go.forms] == [{"go": "1.24"}, {"golang.org/x/crypto": ""}]


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_snippet_and_style_parses_in_small_hosts(language):
    n = 0
    for snippet, style, host, m in _mutants(language):
        assert source_file(language, PATHS[language], m.text).ok, (snippet.id, style, host.name)
        lines = m.text.split("\n")
        assert lines[m.call_line - 1].strip(), (snippet.id, style)
        assert all(s["start"] <= s["end"] for s in m.spans)
        n += 1
    assert n >= 15 * 3 * 2


@pytest.mark.skipif(shutil.which("javac") is None, reason="no javac on PATH")
def test_java_mutants_compile(tmp_path):
    files, what = [], {}
    for i, (snippet, style, host, m) in enumerate(_mutants("java")):
        d = tmp_path / "src" / f"m{i}"  # one package per mutant: a single javac call compiles them all
        d.mkdir(parents=True)
        (d / "Host.java").write_text(m.text.replace("package app;", f"package m{i};", 1), encoding="utf-8")
        files.append(str(d / "Host.java"))
        what[f"m{i}"] = (snippet.id, style, host.name)
    proc = subprocess.run(["javac", "-d", str(tmp_path / "out"), *files], capture_output=True, text=True)
    failed = sorted({what[p] for p in what if f"/{p}/Host.java" in proc.stderr})
    assert proc.returncode == 0, (failed, proc.stderr[-800:])


@pytest.mark.skipif(shutil.which("go") is None, reason="no go toolchain on PATH")
def test_go_mutants_compile(tmp_path):
    for i, (snippet, style, host, m) in enumerate(_mutants("go")):
        if snippet.forms[m.form].requires.get("golang.org/x/crypto") is not None:
            continue  # needs a module download
        d = tmp_path / str(i)
        d.mkdir()
        (d / "main.go").write_text(m.text, encoding="utf-8")
        (d / "go.mod").write_text("module example.com/m\n\ngo 1.24\n", encoding="utf-8")
        proc = subprocess.run(["go", "build", "-o", "/dev/null", "."], cwd=d, capture_output=True, text=True)
        assert proc.returncode == 0, (snippet.id, style, host.name, proc.stderr[-500:])


def test_python_mutants_run():
    pytest.importorskip("cryptography")
    for snippet, style, host, m in _mutants("python"):
        ns: dict = {"__name__": "mutant"}
        exec(compile(m.text, "app.py", "exec"), ns)  # our own catalog's code, in small hosts
        try:
            ns["Box"]().run(1) if host.name == "run" else ns["main"]()
        except Exception as e:  # noqa: BLE001 (any failure of a snippet is the finding)
            raise AssertionError((snippet.id, style, host.name)) from e


# ----- files and hosts -----


def test_candidate_paths_are_first_party_source_only():
    assert candidate_path("java", "src/main/java/a/B.java")
    assert not candidate_path("java", "src/test/java/a/BTest.java") and not candidate_path(
        "java", "a/package-info.java"
    )
    assert not candidate_path("java", "target/generated-sources/a/B.java")
    assert candidate_path("python", "pkg/core.py") and not candidate_path("python", "pkg/test_core.py")
    assert not candidate_path("python", "tests/helpers.py") and not candidate_path("python", "setup.py")
    assert not candidate_path("python", "examples/demo.py") and not candidate_path("python", "pkg/core_test.py")
    assert candidate_path("go", "pkg/x.go") and not candidate_path("go", "pkg/x_test.go")
    assert not candidate_path("go", "vendor/a/x.go") and not candidate_path("go", "pkg/testdata/x.go")


def test_java_hosts_need_a_caller_or_main_and_a_body():
    text = (
        "package a;\n\npublic abstract class A {\n    abstract void none();\n    void called() {\n        int x = 1;\n"
        "    }\n    void lonely() {\n        int y = 2;\n    }\n    void caller() {\n        called();\n    }\n"
        "    void oneLine() { called(); }\n}\n"
    )
    f = source_file("java", "a/A.java", text)
    hosts = {h.name: h for h in f.hosts(f.called())}
    assert set(hosts) == {"called"}  # lonely: no caller; caller/oneLine: never called; none: abstract
    h = hosts["called"]
    assert (h.line, h.first_line, h.end_line, h.indent, h.unit, h.callers) == (5, 6, 7, "        ", "    ", 2)


def test_python_hosts_skip_dunders_properties_and_one_liners_and_respect_docstrings():
    text = (
        "class C:\n    def __init__(self):\n        self.x = helper()\n\n    @property\n    def p(self):\n"
        '        return helper()\n\n    def m(self):\n        """Doc."""\n        return 1\n\n\n'
        "def helper():\n    return C().m()\n\n\ndef short(): return helper()\n"
    )
    f = source_file("python", "c.py", text)
    hosts = {h.name: h for h in f.hosts(f.called())}
    assert set(hosts) == {"m", "helper"}
    assert hosts["m"].first_line == 11 and hosts["m"].top_line == 1 and hosts["helper"].top_line == 14


def test_go_hosts_skip_init_and_generated_files():
    text = "package p\n\nfunc init() {\n\tsetup()\n}\n\nfunc setup() {\n\tx := 1\n\t_ = x\n}\n"
    f = source_file("go", "p/p.go", text)
    assert [h.name for h in f.hosts(f.called())] == ["setup"]
    generated = "// Code generated by protoc-gen-go. DO NOT EDIT.\n\n" + text
    assert not source_file("go", "p/p.pb.go", generated).ok


def test_unparsable_files_are_not_used():
    assert not source_file("java", "a/A.java", "class A { void f( { }").ok
    assert not source_file("python", "a.py", "def f(:\n    pass\n").ok
    assert not source_file("go", "a.go", "package a\nfunc f( {\n}\n").ok


# ----- imports and names -----


def test_java_imports_reuse_on_demand_and_qualify_on_a_clash():
    catalog = load_catalog("java")
    f = source_file("java", "app/Host.java", JAVA_CLASH)
    run = next(h for h in f.hosts(f.called()) if h.name == "run")
    m = mutate(f, run, catalog, catalog.get("ecdsa-p256-sign"), 0, "direct", context={"siblings": {"Cipher"}})
    assert m.qualified == ["java.security.Signature"] and "java.security.Signature signer" in m.text
    assert "import java.security.KeyPairGenerator;" in m.imports_added
    assert "catch (GeneralSecurityException e2)" in m.text  # the parameter `e` is taken
    m = mutate(f, run, catalog, catalog.get("aes-256-gcm"), 0, "direct", context={"siblings": {"Cipher"}})
    assert "javax.crypto.Cipher.getInstance" in m.text  # a sibling Cipher.java in the package
    on_demand = "package a;\n\nimport java.security.*;\n\nclass A {\n    public static void main(String[] a) {\n        int x = 1;\n    }\n}\n"  # noqa: E501
    f = source_file("java", "a/A.java", on_demand)
    m = mutate(f, f.hosts(f.called())[0], catalog, catalog.get("sha-256"), 0, "direct", context={})
    assert m.imports_added == [] and m.qualified == []  # MessageDigest and GeneralSecurityException: java.security.*


def test_python_imports_reuse_existing_and_alias_clashes():
    catalog = load_catalog("python")
    f = source_file("python", "app.py", PY_DOC)
    run = next(h for h in f.hosts(f.called()) if h.name == "run")
    m = mutate(f, run, catalog, catalog.get("rsa-oaep-sha256"), 0, "direct", context={})
    assert (
        "from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding, rsa as asymmetric_rsa"
        in m.text
    )  # noqa: E501
    lines = m.text.split("\n")
    assert lines[1] == "import os as operating_system" and lines[2].startswith("from cryptography")  # after the block
    m = mutate(f, run, catalog, catalog.get("aes-128-gcm"), 0, "direct", context={})
    assert "operating_system.urandom(12)" in m.text and "import os\n" not in m.text  # `import os as ...` reused
    guarded = (
        "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n"
        "    from cryptography.hazmat.primitives.asymmetric import rsa\n\n\ndef main():\n    return 1\n"
    )
    f = source_file("python", "app.py", guarded)
    m = mutate(f, f.hosts(f.called())[0], catalog, catalog.get("rsa-2048-keygen"), 0, "direct", context={})
    assert m.imports_added == ["from cryptography.hazmat.primitives.asymmetric import rsa as asymmetric_rsa"]
    assert "asymmetric_rsa.generate_private_key" in m.text  # the type-checking import is not bound at run time


def test_go_imports_go_into_the_group_sorted_with_an_alias_on_a_clash():
    catalog = load_catalog("go")
    f = source_file("go", "main.go", GO_CLASH)
    run = next(h for h in f.hosts(f.called()) if h.name == "run")
    m = mutate(f, run, catalog, catalog.get("rsa-2048-keygen"), 0, "direct", context={"package_names": {"run"}})
    group = m.text.split("import (\n", 1)[1].split(")", 1)[0].splitlines()
    assert group == ['\tcrand "crypto/rand"', '\tcrsa "crypto/rsa"', '\t"fmt"', '\t"math/rand"']
    assert "crsa.GenerateKey(crand.Reader, 2048)" in m.text
    blank = 'package main\n\nimport (\n\t_ "crypto/rsa"\n)\n\nfunc main() {\n\tprintln("x")\n}\n'
    f = source_file("go", "main.go", blank)
    with pytest.raises(SkipHost):
        mutate(f, f.hosts(f.called())[0], catalog, catalog.get("rsa-2048-keygen"), 0, "direct", context={})


def test_styles_place_the_call_and_the_related_lines():
    catalog = load_catalog("python")
    f = source_file("python", "app.py", PY_BARE)
    host = f.hosts(f.called())[0]
    direct = mutate(f, host, catalog, catalog.get("sha-256"), 0, "direct", context={})
    indirect = mutate(f, host, catalog, catalog.get("sha-256"), 0, "indirect", context={})
    wrapper = mutate(f, host, catalog, catalog.get("sha-256"), 0, "wrapper", context={})
    assert 'hashlib.sha256(b"data")' in direct.text.split("\n")[direct.call_line - 1]
    assert indirect.text.split("\n")[indirect.related_lines[0] - 1].strip() == 'hash_name = "sha256"'
    lines = wrapper.text.split("\n")
    assert lines[wrapper.related_lines[0] - 1].strip() == "_sha256_digest()"  # the helper's call in the host
    assert lines[wrapper.call_line - 1].startswith("    sha256_digest = hashlib.sha256")  # inside the helper
    assert lines[wrapper.call_line - 2] == "def _sha256_digest():"


# ----- line bookkeeping -----


def test_apply_and_shift_map_original_lines_into_the_mutant():
    text = "a\nb\nc\n"
    out, placed = apply(text, [Insert(1, ("x", "y"), "snippet"), Insert(0, ("top",), "import")])
    assert out == "top\na\nx\ny\nb\nc\n" and [start for _, start in placed] == [1, 3]
    shifts = [[0, 1], [1, 2]]
    assert [shift(n, shifts) for n in (1, 2, 3)] == [2, 5, 6]
    crlf, _ = apply("a\r\nb\r\n", [Insert(1, ("x",), "snippet")])
    assert crlf == "a\r\nx\r\nb\r\n"
    tail, _ = apply("a\nb", [Insert(2, ("x",), "helper")])
    assert tail == "a\nb\nx"


# ----- what a project declares -----


class _FakeTree(Tree):
    def __init__(self, files: dict[str, str]):
        from mutation.source import Blob

        super().__init__(bare=Path("."), commit="0" * 40)
        self.blobs = {p: Blob(p, "100644", str(i), len(t)) for i, (p, t) in enumerate(files.items())}
        self._cache = {p: t.encode() for p, t in files.items()}


@pytest.mark.parametrize(
    "files, declared",
    [
        ({"requirements.txt": "requests\ncryptography>=41\n"}, True),
        ({"requirements.txt": "requests\n# cryptography\n"}, False),
        ({"pyproject.toml": '[project]\ndependencies = ["cryptography>=3.4"]\n'}, True),
        ({"pyproject.toml": '[tool.poetry.dependencies]\ncryptography = "^42.0"\n'}, True),
        ({"setup.cfg": "[options]\ninstall_requires =\n    cryptography==2.9\n"}, False),  # pinned below 3.1
        ({"setup.py": "setup(install_requires=['cryptography<3'])\n"}, False),
        ({"pyproject.toml": '[project]\ndependencies = ["cryptography-vectors"]\n'}, False),
        ({"Pipfile": '[packages]\ncryptography = "*"\n'}, True),
        ({"sub/pyproject.toml": "[project]\n", "requirements.txt": "cryptography\n"}, False),  # nearest project wins
    ],
)
def test_python_declares_cryptography_in_the_nearest_project(files, declared):
    from mutation.source import python_declares

    tree = _FakeTree({**files, "sub/pkg/mod.py": "x = 1\n"})
    assert python_declares(tree, "sub/pkg/mod.py", "cryptography", "3.1") is declared


def test_go_requirements_from_the_nearest_go_mod():
    from mutation.source import go_module

    tree = _FakeTree({
        "go.mod": "module a\n\ngo 1.21\n\nrequire (\n\tgolang.org/x/crypto v0.17.0 // indirect\n)\n",
        "tools/go.mod": "module a/tools\n\ngo 1.24.1\n",
        "x.go": "", "tools/y.go": "",
    })  # fmt: skip
    root, tools = go_module(tree, "x.go"), go_module(tree, "tools/y.go")
    assert root.go == (1, 21) and "golang.org/x/crypto" in root.requires and tools.go == (1, 24, 1)
    assert not go_satisfies(root, {"go": "1.24"}) and go_satisfies(root, {"golang.org/x/crypto": ""})
    assert go_satisfies(tools, {"go": "1.24"}) and not go_satisfies(tools, {"golang.org/x/crypto": ""})
    assert go_satisfies(GoModule("go.mod", None, frozenset()), {}) and _spec_allows(">=3.4,<46", "3.1")
