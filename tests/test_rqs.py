"""The research questions are defined once (analysis/rqs.py): four, in the paper's order, and every table a
question names has a description in the summary."""

from analysis import summary
from analysis.rqs import RAQUN_TABLES, RQS


def test_the_four_questions_name_described_tables():
    assert [rq.id for rq in RQS] == ["RQ1", "RQ2", "RQ3", "RQ4"]  # the id is the place in RQS
    assert [rq.slug for rq in RQS] == ["content", "normalization", "agreement", "detection"]
    for rq in RQS:
        assert rq.question and rq.heading() == f"## {rq.id} {rq.title}"
        for table in rq.tables:
            assert table in summary.FILES, (rq.id, table)
    assert all(table in summary.FILES and "raqun" in table for table in RAQUN_TABLES)  # RaQuN's files are marked
    assert not [t for rq in RQS for t in rq.tables if "raqun" in t]  # and no question rests on one
