"""The mutants of an experiment: selected deterministically, committed as branches in the bare mirror, recorded in
mutants.json next to the record `_experiment.yaml`.

`ensure_mutants` is called by the runner (common/experiment.py:resolve_snapshots) for every repo of a spec with a
`mutation:` block. A repo already in mutants.json (a resumed run) keeps its mutants: their branches are checked and,
where missing (e.g. a fresh VM), rebuilt from the recorded inserts with the same SHA; `--overwrite-old` removes the
file with the experiment folder, and the regenerated mutants are the same ones (same seed, commit and catalog
version). A new repo gets `per_repo` insertion mutants: (snippet, style) pairs drawn round-robin over the styles from
a seeded shuffle, each into a host from a file not used yet where possible, among the snippets whose library the
project already declares (dependency files are never edited, decided 2026-10-20), then `decoys` decoy mutants (the
catalog's known negatives, `direct` only, round-robin over the kinds; `equal`: as many as calls, decided 2026-10-22).
Every mutant is one commit on the pinned commit: one file changed, whole lines inserted.
"""

from __future__ import annotations

import hashlib
import os
import random
import subprocess
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath

from common.experiment import ExperimentError, MutationSpec, RepoSpec, mirror_path, variant_branch

from .catalog import LANGUAGES, Catalog, load_catalog
from .insert import InsertError, Mutation, apply, mutate
from .lang import MAX_FILE_BYTES, GoFile, Host, Insert, SkipHost, SourceFile, candidate_path, source_file
from .source import Tree, satisfied

MUTANT_AUTHOR = ("BF-CBOM mutants", "mutants@bf-cbom.invalid")
MUTANT_DATE = "2026-10-20T00:00:00Z"
HOST_TRIES = 25  # hosts tried per (snippet, style) before the pair is given up for the repo


def _git(bare: Path, *args: str, data: bytes | None = None, env: dict | None = None) -> str:
    proc = subprocess.run(["git", "-C", str(bare), *args], input=data, capture_output=True, env=env)
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip()[-300:]
        raise ExperimentError(f"git {args[0]} in {bare.name} failed: {msg}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


def write_branch(bare: Path, base: str, path: str, mode: str, text: str, mutant_id: str) -> str:
    """Commit `text` as `path` on top of `base` without a checkout (a temporary index) and point the mutant's branch
    at it. Author, committer and date are fixed, so the SHA depends only on the base and the content."""
    blob = _git(bare, "hash-object", "-w", "--stdin", data=text.encode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="mutant-") as tmp:
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(Path(tmp) / "index"),
            "GIT_AUTHOR_NAME": MUTANT_AUTHOR[0],
            "GIT_AUTHOR_EMAIL": MUTANT_AUTHOR[1],
            "GIT_AUTHOR_DATE": MUTANT_DATE,
            "GIT_COMMITTER_NAME": MUTANT_AUTHOR[0],
            "GIT_COMMITTER_EMAIL": MUTANT_AUTHOR[1],
            "GIT_COMMITTER_DATE": MUTANT_DATE,
        }
        _git(bare, "read-tree", base, env=env)
        _git(bare, "update-index", "--cacheinfo", f"{mode},{blob},{path}", env=env)
        tree = _git(bare, "write-tree", env=env)
        sha = _git(bare, "commit-tree", tree, "-p", base, "-m", f"BF-CBOM mutant {mutant_id}", env=env)
    _git(bare, "update-ref", f"refs/heads/{variant_branch(mutant_id, base)}", sha)
    return sha


def mutant_id(commit: str, version: str, snippet: str, style: str, path: str, line: int) -> str:
    h = hashlib.sha256(f"{commit}|{version}|{snippet}|{style}|{path}|{line}".encode()).hexdigest()[:6]
    return f"ins-{snippet}-{style}-{h}"


class RepoMutator:
    """A repository at a commit, parsed once: its hosts and what each project may take."""

    def __init__(self, bare: Path, commit: str, language: str):
        self.bare, self.commit, self.language = bare, commit, language
        self.catalog: Catalog = load_catalog(language)
        self.tree = Tree.load(bare, commit)
        paths = [
            p for p, b in sorted(self.tree.blobs.items()) if candidate_path(language, p) and b.size <= MAX_FILE_BYTES
        ]
        self.files: dict[str, SourceFile] = {}
        for p, data in sorted(self.tree.read(paths).items()):
            try:
                f = source_file(language, p, data.decode("utf-8"))
            except UnicodeDecodeError:
                continue
            if f.ok:
                self.files[p] = f
        callers: Counter = Counter()
        for f in self.files.values():
            callers.update(f.called())
        self.hosts: list[Host] = [h for p in sorted(self.files) for h in self.files[p].hosts(callers)]
        self._contexts: dict[str, dict] = {}
        self._satisfied: dict[tuple, bool] = {}

    def context(self, path: str) -> dict:
        """What the file's neighbours declare: Java's sibling type names, Go's package-level names."""
        directory = str(PurePosixPath(path).parent)
        directory = "" if directory == "." else directory
        if directory not in self._contexts:
            ctx: dict = {}
            neighbours = self.tree.dir_files(directory)
            if self.language == "java":
                ctx["siblings"] = {PurePosixPath(p).stem for p in neighbours if p.endswith(".java") and p != path}
            elif self.language == "go":
                package = getattr(self.files.get(path), "package", "")
                names: set[str] = set()
                for p, data in self.tree.read([p for p in neighbours if p.endswith(".go")]).items():
                    try:
                        g = GoFile(p, data.decode("utf-8"))
                    except UnicodeDecodeError:
                        continue
                    if g.package == package:
                        names |= g.top_level_names()
                ctx["package_names"] = names
            self._contexts[directory] = ctx
        return self._contexts[directory]

    def form_for(self, snippet, path: str) -> int | None:
        """The first form of `snippet` whose requirements the project of `path` meets."""
        for i, form in enumerate(snippet.forms):
            key = (str(PurePosixPath(path).parent), tuple(sorted(form.requires.items())))
            if key not in self._satisfied:
                self._satisfied[key] = satisfied(self.tree, self.language, path, form.requires)
            if self._satisfied[key]:
                return i
        return None

    def try_pair(self, snippet_id: str, style: str, used_files: set[str], rng: random.Random) -> Mutation | None:
        snippet = self.catalog.get(snippet_id)
        candidates = [h for h in self.hosts if self.form_for(snippet, h.path) is not None]
        rng.shuffle(candidates)
        candidates.sort(key=lambda h: h.path in used_files)  # stable: unused files first, shuffled within
        for host in candidates[:HOST_TRIES]:
            form = self.form_for(snippet, host.path)
            try:
                return mutate(
                    self.files[host.path], host, self.catalog, snippet, form, style, context=self.context(host.path)
                )
            except (InsertError, SkipHost):
                continue
        return None


def _entry(m: Mutation, mid: str, catalog: Catalog, mode: str, sha: str, patch: str) -> dict:
    snippet = catalog.get(m.snippet)
    return {
        "id": mid,
        "snippet": m.snippet,
        "style": m.style,
        "language": catalog.language,
        "catalog_version": catalog.version,
        "form": m.form,
        "file": m.path,
        "mode": mode,
        "host": {"name": m.host.name, "line": m.host.line, "callers": m.host.callers, "entry": m.host.entry},
        "call_line": m.call_line,
        "related_lines": m.related_lines,
        "spans": m.spans,
        "shifts": m.shifts,
        "inserts": [{"after": i.after, "order": i.order, "kind": i.kind, "lines": list(i.lines)} for i in m.inserts],
        "imports_added": m.imports_added,
        "qualified": m.qualified,
        "expected": dict(snippet.expected),
        "also": list(snippet.also),
        "decoy": snippet.decoy,
        "commit": sha,
        "patch": patch,
    }


def generate(bare: Path, repo: RepoSpec, commit: str, params: MutationSpec, *, log=print) -> dict:
    """mutants.json's entry for a repository: its mutants (committed) and why fewer than asked were made."""
    language = (repo.language or "").lower()
    entry: dict = {"commit": commit, "language": language, "mutation": params.to_dict(), "mutants": [], "notes": []}
    if language not in LANGUAGES:
        entry["notes"].append(f"no snippet catalog for language {repo.language!r}")
        return entry
    mutator = RepoMutator(bare, commit, language)
    catalog = mutator.catalog
    entry["catalog_version"] = catalog.version
    entry["hosts"] = len(mutator.hosts)
    if not mutator.hosts:
        entry["notes"].append("no host: no first-party method with a caller or a main")
        return entry
    seed = hashlib.sha256(f"{params.seed}|{repo.full_name}|{commit}|{catalog.version}".encode()).hexdigest()
    rng = random.Random(int(seed[:16], 16))
    wanted = [s.id for s in catalog.snippets if params.snippets is None or s.id in params.snippets]
    pairs = [(sid, st) for sid in wanted for st in params.styles if catalog.get(sid).supports(st)]
    rng.shuffle(pairs)
    used_snippets: Counter = Counter()
    used_files: set[str] = set()
    for i in range(params.per_repo):
        style = params.styles[i % len(params.styles)]
        made = None
        for pair in sorted(pairs, key=lambda p: (p[1] != style, used_snippets[p[0]], pairs.index(p))):
            pairs.remove(pair)  # every (snippet, style) at most once per repository; one that does not fit is dropped
            made = mutator.try_pair(pair[0], pair[1], used_files, rng)
            if made:
                break
        if made is None:
            entry["notes"].append(f"{len(entry['mutants'])} of {params.per_repo} mutants: no further pair fits a host")
            break
        _commit(bare, commit, mutator, catalog, made, entry, log)
        used_snippets[made.snippet] += 1
        used_files.add(made.path)
    calls = len(entry["mutants"])
    wanted_decoys = calls if params.decoys == "equal" else int(params.decoys)
    kinds = [d.id for d in catalog.decoys]
    rng.shuffle(kinds)
    for i in range(wanted_decoys if kinds else 0):
        made = mutator.try_pair(kinds[i % len(kinds)], "direct", used_files, rng)
        if made is None:
            entry["notes"].append(f"{len(entry['mutants']) - calls} of {wanted_decoys} decoys: no host fits")
            break
        _commit(bare, commit, mutator, catalog, made, entry, log)
        used_files.add(made.path)
    return entry


def _commit(bare: Path, commit: str, mutator: RepoMutator, catalog: Catalog, made: Mutation, entry: dict, log) -> None:
    mid = mutant_id(commit, catalog.version, made.snippet, made.style, made.path, made.host.line)
    mode = mutator.tree.blobs[made.path].mode
    sha = write_branch(bare, commit, made.path, mode, made.text, mid)
    patch = _git(bare, "diff", commit, sha) + "\n"
    entry["mutants"].append(_entry(made, mid, catalog, mode, sha, patch))
    what = "decoy" if catalog.get(made.snippet).decoy else "mutant"
    log(f"    {what} {mid}: {made.path}:{made.call_line} in {made.host.name}()")


def rebuild(bare: Path, base: str, mutant: dict) -> str:
    """Make sure the mutant's branch exists with its recorded SHA (rebuilt from the recorded inserts if needed)."""
    branch = f"refs/heads/{variant_branch(mutant['id'], base)}"
    proc = subprocess.run(["git", "-C", str(bare), "rev-parse", "--verify", "-q", branch], capture_output=True)
    if proc.returncode == 0 and proc.stdout.decode().strip() == mutant["commit"]:
        return mutant["commit"]
    original = Tree.load(bare, base).text(mutant["file"])
    if original is None:
        raise ExperimentError(f"mutant {mutant['id']}: {mutant['file']} not found at {base[:12]}")
    inserts = [Insert(i["after"], tuple(i["lines"]), i["kind"], i["order"]) for i in mutant["inserts"]]
    text, _ = apply(original, inserts)
    sha = write_branch(bare, base, mutant["file"], mutant["mode"], text, mutant["id"])
    if sha != mutant["commit"]:
        raise ExperimentError(f"mutant {mutant['id']} rebuilt as {sha[:12]}, recorded {mutant['commit'][:12]}")
    return sha


def ensure_mutants(
    repo: RepoSpec, commit: str, params: MutationSpec, manifest: dict, *, mirror_dir: Path, log=print
) -> list[str]:
    """The mutant variants of `repo` at `commit`: those recorded in `manifest` (mutants.json; branches rebuilt where
    missing) or, for a repository not recorded yet, newly generated and added to it."""
    bare = mirror_path(repo.full_name, mirror_dir)
    repos = manifest.setdefault("repos", {})
    entry = repos.get(repo.full_name)
    if entry and entry.get("commit") == commit:
        if entry.get("mutation") != params.to_dict():
            log(f"  {repo.full_name}: keeping the mutants in mutants.json (made with {entry.get('mutation')})")
        for m in entry["mutants"]:
            rebuild(bare, commit, m)
        return [m["id"] for m in entry["mutants"]]
    log(f"  {repo.full_name}: generating mutants")
    entry = generate(bare, repo, commit, params, log=log)
    for note in entry["notes"]:
        log(f"    {note}")
    repos[repo.full_name] = entry
    return [m["id"] for m in entry["mutants"]]
