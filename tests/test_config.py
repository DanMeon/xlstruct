"""Tests for configuration."""

import pytest
from pydantic import ValidationError

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


class TestExtractorConfigValidation:
    def test_token_budget_zero_raises(self):
        with pytest.raises(ValidationError):
            ExtractorConfig(token_budget=0)

    def test_token_budget_negative_raises(self):
        with pytest.raises(ValidationError):
            ExtractorConfig(token_budget=-1)

    def test_max_retries_negative_raises(self):
        with pytest.raises(ValidationError):
            ExtractorConfig(max_retries=-1)

    def test_temperature_too_high_raises(self):
        with pytest.raises(ValidationError):
            ExtractorConfig(temperature=3.0)

    def test_codegen_timeout_zero_raises(self):
        with pytest.raises(ValidationError):
            ExtractorConfig(codegen_timeout=0)

    def test_max_retries_zero_valid(self):
        config = ExtractorConfig(max_retries=0)
        assert config.max_retries == 0

    def test_temperature_max_valid(self):
        config = ExtractorConfig(temperature=2.0)
        assert config.temperature == 2.0


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
