"""Mutation harness (Phase 4): insert known crypto calls into real projects as branches on the git server.

`catalog` holds the snippets per language (mutation/catalog/<language>.yaml), `source` reads a pinned commit from the
bare mirror, `lang` parses Java, Python and Go with tree-sitter (hosts, callers, imports), `insert` renders one snippet
into one host, `mutants` selects, commits and records the mutants of an experiment (mutants.json).
"""
