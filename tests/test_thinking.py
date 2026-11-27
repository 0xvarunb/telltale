import pytest

from telltale.thinking import THINKING_OFF, UnsupportedModel, thinking_off


def test_thinking_off_per_model():
    # The lowest thinking setting each model accepts; the other value is a 400 error there.
    assert thinking_off("claude-haiku-5-5") == {"type": "disabled"}
    assert thinking_off("claude-sonnet-5-5") == {"type": "between_tools"}


def test_thinking_off_refuses_an_unlisted_model():
    with pytest.raises(UnsupportedModel, match="THINKING_OFF"):
        thinking_off("claude-unlisted-test")


def test_opus_5_5_is_refused_since_its_thinking_cannot_be_turned_off():
    # https://platform.claude.com/docs/en/build-with-claude/thinking (read 26 Nov 2026): the
    # per-model table gives Claude Opus 5.5 a 400 error for "disabled", "between_tools" and
    # "enabled" with budget_tokens, and the page says its thinking can't be turned off. So it
    # is left out of THINKING_OFF, and out of the answer-model comparison.
    assert "claude-opus-5-5" not in THINKING_OFF
    with pytest.raises(UnsupportedModel, match="claude-opus-5-5"):
        thinking_off("claude-opus-5-5")


def test_thinking_off_returns_a_copy_the_caller_cannot_use_to_change_the_table():
    setting = thinking_off("claude-haiku-5-5")
    setting["type"] = "adaptive"
    assert thinking_off("claude-haiku-5-5") == {"type": "disabled"}
