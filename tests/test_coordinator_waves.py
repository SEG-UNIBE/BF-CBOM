"""Wave scheduling of UI inspections (coordinator/redis_io.py): pure helpers, no Redis."""

import json

from coordinator import redis_io

MEM = {"cbomkit": 8.0, "cbomkit-jar": 8.0, "cryptobomforge": 6.0, "cdxgen": 4.0, "cryptoscan": 2.0}


def test_inspection_waves_uses_registry_memory_and_budget(monkeypatch):
    monkeypatch.setattr(redis_io, "load_tools", lambda: {k: {"memory_gb": v} for k, v in MEM.items()})
    assert redis_io.inspection_waves(list(MEM), budget_gb=0) == [list(MEM)]
    waves = redis_io.inspection_waves(list(MEM), budget_gb=10)
    assert waves == [["cbomkit", "cryptoscan"], ["cbomkit-jar"], ["cryptobomforge", "cdxgen"]]


def test_wave_fields_round_trip():
    waves = [["a", "b"], ["c"]]
    meta = redis_io.wave_fields(waves, 1)
    assert json.loads(meta["waves_json"]) == waves
    assert redis_io.get_insp_waves(meta) == (waves, 1)
    assert redis_io.get_insp_waves({}) == ([], 0)
    assert redis_io.get_insp_waves({"waves_json": "not json", "wave": "x"}) == ([], 0)


def test_wave_complete_only_counts_the_wave_workers():
    rows = [("cbomkit", "completed"), ("cryptoscan", "failed"), ("cbomkit-jar", "held"), ("cdxgen", "fired")]
    assert redis_io.wave_complete(rows, ["cbomkit", "cryptoscan"])
    assert not redis_io.wave_complete(rows, ["cbomkit", "cdxgen"])
    assert not redis_io.wave_complete(rows, ["cbomkit-jar"])
    assert redis_io.wave_complete(rows, ["not-in-inspection"])  # a wave without jobs never blocks
    assert redis_io.wave_complete([("cbomkit", "cancelled")], ["cbomkit"])
