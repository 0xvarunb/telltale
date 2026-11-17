from pathlib import Path

import pytest

from telltale.settings import SettingsError, load_settings


@pytest.fixture(autouse=True)
def no_key_in_environment(monkeypatch):
    # The shell running the tests may carry a real key; keep it out.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_missing_key_raises(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("")

    with pytest.raises(SettingsError) as error:
        load_settings(env_file)

    assert str(error.value) == "ANTHROPIC_API_KEY is not set. Add it to .env"


def test_reads_key_and_defaults(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=test-key\n")

    settings = load_settings(env_file)

    assert settings.anthropic_api_key == "test-key"
    assert settings.model == "claude-haiku-5-5"
    assert settings.data_dir == Path("data")


def test_environment_variable_wins_over_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-environment")

    assert load_settings(env_file).anthropic_api_key == "from-environment"


def test_ignores_comments_blank_lines_and_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# a comment\n\n  ANTHROPIC_API_KEY = "quoted-key"  \n')

    assert load_settings(env_file).anthropic_api_key == "quoted-key"


def test_empty_value_counts_as_missing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=\n")

    with pytest.raises(SettingsError):
        load_settings(env_file)


def test_missing_env_file_raises_settings_error(tmp_path):
    with pytest.raises(SettingsError):
        load_settings(tmp_path / "does-not-exist.env")


def test_key_optional_when_not_required(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("")

    settings = load_settings(env_file, require_key=False)

    assert settings.anthropic_api_key is None
    assert settings.data_dir == Path("data")
    assert "None" in repr(settings)


def test_repr_masks_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=test-secret-value\n")

    assert "test-secret-value" not in repr(load_settings(env_file))
