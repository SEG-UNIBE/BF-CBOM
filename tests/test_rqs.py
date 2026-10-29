"""The research questions are defined once (analysis/rqs.py): CLAUDE.md names the same ids and titles, and every
table a question names has a description in the summary."""

from pathlib import Path

from analysis import summary
from analysis.rqs import RQS

ROOT = Path(__file__).resolve().parents[1]


def test_the_six_questions_match_claude_md_and_name_described_tables():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = text[text.index("## Research questions") : text.index("## Status")]
    assert [rq.id for rq in RQS] == ["RQ1", "RQ2", "RQ3", "RQ4", "RQ5", "RQ6"]
    for rq in RQS:
        assert f"**{rq.id}, {rq.title.lower()}:**" in section, rq.id
        assert rq.question and rq.heading() == f"## {rq.id} {rq.title}"
        for table in rq.tables:
            assert table in summary.FILES, (rq.id, table)
