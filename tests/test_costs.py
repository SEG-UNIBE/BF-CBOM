"""LLM costs from token usage and the price table (analysis/costs.py, analysis/data/llm_prices.yaml)."""

from analysis import costs


def test_price_table_entries_are_complete_and_sourced():
    table = costs.load_prices()
    assert table["version"] and table["prices"]
    for e in table["prices"]:
        assert e["models"] and e["source"].startswith("https://") and e["retrieved"] and e["valid_from"]
        assert e["input"] >= 0 and e["output"] >= 0 and e["cached_input"] <= e["input"]


def test_price_lookup_exact_prefix_and_date():
    assert costs.price_for("gpt-5")["input"] == 1.25 and costs.price_for("gpt-5-2026-01-01")["input"] == 1.25
    assert costs.price_for("claude-sonnet-5-5-20261001")["cache_write"] == 2.50
    assert costs.price_for("deepseek-chat")["provider"] == "deepseek" and costs.price_for("unknown-9") is None
    table = {
        "version": "t",
        "prices": [
            {"models": ["m"], "valid_from": "2026-01-01", "input": 1, "cached_input": 0, "output": 2},
            {"models": ["m"], "valid_from": "2026-06-01", "input": 10, "cached_input": 0, "output": 20},
        ],
    }
    assert costs.price_for("m", "2026-03-01T00:00:00Z", table)["input"] == 1
    assert (
        costs.price_for("m", "2026-07-01T00:00:00Z", table)["input"] == 10
        and costs.price_for("m", None, table)["input"] == 10
    )


def test_cost_counts_cached_and_written_tokens_at_their_rates():
    usage = {
        "input_tokens": 1_000_000,
        "cached_input_tokens": 400_000,
        "cache_write_tokens": 100_000,
        "output_tokens": 50_000,
        "models": {"claude-sonnet-5-5-20261001": 3},
    }
    out = costs.cost_usd(usage, started_at="2026-10-17T22:50:00Z")
    # 500k fresh * 2.00 + 400k cached * 0.10 + 100k written * 2.50 + 50k out * 10.00, per 1M
    assert out["cost_usd"] == round((500_000 * 2.0 + 400_000 * 0.10 + 100_000 * 2.5 + 50_000 * 10.0) / 1e6, 6)
    assert out["priced_model"] == "claude-sonnet-5-5-20261001" and out["price_period"] == "peak"


def test_deepseek_off_peak_by_weekday_and_utc_hour():
    entry = costs.price_for("deepseek-flash")
    assert costs.period("2026-10-16T02:30:00Z", entry) == "peak"  # Friday 02:30 UTC
    assert costs.period("2026-10-16T05:00:00Z", entry) == "off_peak"  # Friday 05:00 UTC, between the windows
    assert costs.period("2026-10-17T08:00:00Z", entry) == "off_peak"  # Saturday
    usage = {"input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 0, "models": {"deepseek-chat": 1}}
    assert costs.cost_usd(usage, started_at="2026-10-17T08:00:00Z")["cost_usd"] == 0.15
    assert costs.cost_usd(usage, started_at="2026-10-16T02:30:00Z")["cost_usd"] == 0.30
    assert costs.cost_usd({"models": {}}, started_at=None)["note"] == "no model recorded"
    assert costs.cost_usd(None)["cost_usd"] is None
