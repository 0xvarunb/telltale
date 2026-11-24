"""One price table, keyed by model; a model with no entry is refused."""

import pytest

from telltale.pricing import PRICES, UnknownModelPrice, cost_usd, require_price
from telltale.settings import DEFAULT_JUDGE_MODEL, DEFAULT_MODEL


def test_the_default_models_have_prices():
    require_price(DEFAULT_MODEL)
    require_price(DEFAULT_JUDGE_MODEL)
    assert set(PRICES) == {DEFAULT_MODEL, DEFAULT_JUDGE_MODEL}


def test_cost_is_priced_by_model():
    assert cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 1_000_000) == pytest.approx(12.0)


def test_a_model_with_no_price_is_refused_by_name():
    with pytest.raises(UnknownModelPrice, match="claude-unpriced-test"):
        require_price("claude-unpriced-test")
    with pytest.raises(UnknownModelPrice, match="claude-unpriced-test"):
        cost_usd("claude-unpriced-test", 10, 10)
