"""The setting that turns each model's thinking off, in one table, for the answering model and
the judge alike.

A model with no entry is refused before any call. Add a model here only with the setting read
in Anthropic's thinking docs.
"""

# The lowest thinking setting each model accepts; any other value is a 400 error there.
# From the per-model table at https://platform.claude.com/docs/en/build-with-claude/thinking
# (read 26 Nov 2026; first read 19 Nov 2026):
# - claude-sonnet-5-5 takes "between_tools" at effort high or below and refuses "disabled";
#   with no tools in the request, the reply is text only.
# - claude-haiku-5-5 takes "disabled" at effort high or below.
# Both run at their default effort here. claude-opus-5-5 is left out: the same table gives it a
# 400 error for "disabled", "between_tools" and a thinking budget, and the page says its
# thinking can't be turned off.
THINKING_OFF: dict[str, dict] = {
    "claude-sonnet-5-5": {"type": "between_tools"},
    "claude-haiku-5-5": {"type": "disabled"},
}


class UnsupportedModel(ValueError):
    """Raised for a model with no known setting that turns its thinking off."""


def thinking_off(model: str) -> dict:
    """The `thinking` value that turns `model`'s thinking off; `UnsupportedModel` if none.

    Returns a copy, so a caller cannot change the table.
    """
    try:
        return dict(THINKING_OFF[model])
    except KeyError:
        raise UnsupportedModel(
            f"The model {model} has no known setting that turns its thinking off. Add it to "
            f"THINKING_OFF in telltale/thinking.py first (listed now: {', '.join(THINKING_OFF)})."
        ) from None
