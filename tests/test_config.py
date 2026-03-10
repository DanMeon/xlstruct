"""Tests for configuration."""

from xlstruct.config import ExtractorConfig, get_provider_kwargs


class TestExtractorConfig:
    def test_defaults(self):
        config = ExtractorConfig()
        assert config.provider == "anthropic/claude-sonnet-4-6"
        assert config.max_retries == 3
        assert config.token_budget == 100_000
        assert config.temperature == 0.0

    def test_custom(self):
        config = ExtractorConfig(
            provider="anthropic/claude-sonnet-4-5-20250514",
            max_retries=5,
            temperature=0.1,
        )
        assert config.provider == "anthropic/claude-sonnet-4-5-20250514"
        assert config.max_retries == 5


class TestGetProviderKwargs:
    def test_anthropic_includes_max_tokens(self):
        config = ExtractorConfig(provider="anthropic/claude-sonnet-4-5-20250514")
        kwargs = get_provider_kwargs(config)
        assert "max_tokens" in kwargs
        assert kwargs["max_tokens"] == 8192

    def test_openai_no_extra_kwargs(self):
        config = ExtractorConfig(provider="openai/gpt-4o")
        kwargs = get_provider_kwargs(config)
        assert "max_tokens" not in kwargs

    def test_provider_options_override(self):
        config = ExtractorConfig(
            provider="anthropic/claude-sonnet-4-5-20250514",
            provider_options={"max_tokens": 4096},
        )
        kwargs = get_provider_kwargs(config)
        assert kwargs["max_tokens"] == 4096
