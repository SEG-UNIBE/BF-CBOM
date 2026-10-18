"""LLM costs, computed offline from the stored token usage and a dated price table (CLAUDE.md Phase 2).

The workers store only token counts (`JobResult.usage`, common/llm_cbom.py); prices change, differ per model, per
cache hit and, for DeepSeek, per time of day, so dollars are derived here from `analysis/data/llm_prices.yaml`:
one entry per (provider, model ids, valid_from) with the price per 1M input, cached-input, cache-write and output
tokens, the source URL and the retrieval date. `cost_usd` picks the entry of the model the provider answered with
(`usage["models"]`), valid at the run's start, and the peak or off-peak rate where the table has both. The table's
version lands in `runs.csv` next to the cost, so a paper states which price table it used.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import yaml

PRICES_FILE = Path(__file__).resolve().parent / "data" / "llm_prices.yaml"
_PER = 1_000_000


@lru_cache(maxsize=2)
def load_prices(path: Path = PRICES_FILE) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = []
    for e in data.get("prices") or []:
        entries.append({**e, "models": [str(m) for m in e.get("models") or []], "valid_from": str(e.get("valid_from"))})
    return {"version": str(data.get("version") or "?"), "prices": entries}


def _ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def price_for(model: str | None, on: str | datetime | None = None, table: dict | None = None) -> dict | None:
    """The newest entry for `model` whose `valid_from` is not after `on` (default: the newest entry)."""
    if not model:
        return None
    table = table or load_prices()
    when = _ts(on) if on else None
    candidates = [e for e in table["prices"] if model in e["models"]]
    if not candidates:  # dated snapshots of a model id (gpt-5-2025-08-07, claude-sonnet-5-5-20261001)
        candidates = [e for e in table["prices"] if any(model.startswith(p) for p in e.get("prefixes") or [])]
    if when is not None:
        dated = [e for e in candidates if (_ts(e["valid_from"]) or when) <= when]
        candidates = dated or candidates
    return max(candidates, key=lambda e: e["valid_from"]) if candidates else None


def period(started_at: str | None, entry: dict) -> str:
    """`peak` or `off_peak` for an entry with `off_peak` rates (DeepSeek's UTC windows, weekends off-peak; Chinese
    public holidays are not modelled), else `peak` (the standard rate)."""
    if not entry.get("off_peak"):
        return "peak"
    t = _ts(started_at)
    if t is None:
        return "peak"
    if t.weekday() >= 5:
        return "off_peak"
    windows = entry.get("peak_hours_utc") or []
    return "peak" if any(lo <= t.hour < hi for lo, hi in windows) else "off_peak"


def cost_usd(usage: dict | None, *, started_at: str | None = None, table: dict | None = None) -> dict:
    """{cost_usd, priced_model, price_period} for a run's usage; cost None when no model or no price is known. With
    several answering models the first (sorted) prices every token; the others are listed in `note`."""
    out = {"cost_usd": None, "priced_model": None, "price_period": None, "note": None}
    if not isinstance(usage, dict):
        return out
    models = sorted(usage.get("models") or {})
    entry = price_for(models[0], started_at, table) if models else None
    if entry is None:
        out["note"] = f"no price for {models}" if models else "no model recorded"
        return out
    rates = entry["off_peak"] if period(started_at, entry) == "off_peak" else entry
    inp = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_input_tokens") or 0)
    write = int(usage.get("cache_write_tokens") or 0)
    outp = int(usage.get("output_tokens") or 0)
    fresh = max(inp - cached - write, 0)
    cost = (
        fresh * float(rates.get("input", entry.get("input", 0)))
        + cached * float(rates.get("cached_input", entry.get("cached_input", entry.get("input", 0))))
        + write * float(rates.get("cache_write", entry.get("cache_write", entry.get("input", 0))))
        + outp * float(rates.get("output", entry.get("output", 0)))
    ) / _PER
    out.update(
        cost_usd=round(cost, 6),
        priced_model=models[0],
        price_period=period(started_at, entry),
        note=f"also answered: {models[1:]}" if len(models) > 1 else None,
    )
    return out
