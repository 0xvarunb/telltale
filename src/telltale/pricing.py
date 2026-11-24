"""What each model costs, in one table, so every cost is priced by the model actually used.

A model with no entry is refused before any call, since its cost could not be counted against
a cost cap. Add a model here only with prices read on the pricing page.
"""

# US dollars per million tokens, (input, output), base prices.
# claude-haiku-5-5: as set in docs/plans/stage-1-handover/constraints.md.
# claude-sonnet-5-5: from https://platform.claude.com/docs/en/about-claude/pricing
# (docs.anthropic.com redirects there), read 19 Nov 2026.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
}


# A request sent in a Message Batch costs half its live price (stage 2 plan, Task 6).
BATCH_DISCOUNT = 0.5


class UnknownModelPrice(ValueError):
    """Raised for a model with no entry in the price table."""


def require_price(model: str) -> tuple[float, float]:
    """The (input, output) price per million tokens of `model`; `UnknownModelPrice` if none."""
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelPrice(
            f"There is no price for the model {model}, so its cost cannot be counted. "
            f"Add its price to telltale/pricing.py first (priced now: {', '.join(PRICES)})."
        ) from None


def cost_usd(
    model: str, input_tokens: float, output_tokens: float, *, batch: bool = False
) -> float:
    """The cost of these tokens on `model`, at the batch price when `batch` is true."""
    price_in, price_out = require_price(model)
    cost = input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out
    return cost * BATCH_DISCOUNT if batch else cost
