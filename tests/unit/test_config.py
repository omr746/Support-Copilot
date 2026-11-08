"""Unit tests for configuration loader, YAML handling, and priority hierarchy (Plan Step 1.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from copilot.core.config import AppConfig


def test_yaml_defaults_loaded(tmp_path: Path) -> None:
    config_file = tmp_path / "base.yaml"
    config_file.write_text("environment: staging\napp_jwt_secret: yaml-secret\n", encoding="utf-8")

    cfg = AppConfig.load(config_file)
    assert cfg.environment == "staging"
    assert cfg.app_jwt_secret == "yaml-secret"


def test_env_override_takes_precedence_over_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. YAML sets staging
    config_file = tmp_path / "base.yaml"
    config_file.write_text("environment: staging\n", encoding="utf-8")

    # 2. Environment sets production
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("APP_JWT_SECRET", "prod-secret-from-env")

    cfg = AppConfig.load(config_file)
    # Environment variable must win over YAML
    assert cfg.environment == "production"
    assert cfg.app_jwt_secret == "prod-secret-from-env"


def test_explicit_overrides_take_precedence_over_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "base.yaml"
    config_file.write_text("environment: staging\n", encoding="utf-8")

    monkeypatch.setenv("ENVIRONMENT", "production")

    # Explicit kwargs passed to load() must win over environment and YAML
    cfg = AppConfig.load(config_file, environment="testing-override")
    assert cfg.environment == "testing-override"


def test_empty_yaml_returns_defaults(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty.yaml"
    empty_file.write_text("", encoding="utf-8")

    cfg = AppConfig.load(empty_file)
    assert cfg.environment == "development"


def test_non_dict_yaml_raises_value_error(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "invalid.yaml"
    bad_yaml.write_text("- item1\n- item2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Expected a YAML mapping"):
        AppConfig.load(bad_yaml)


def test_missing_yaml_file_raises_filenotfound(tmp_path: Path) -> None:
    missing_file = tmp_path / "non_existent.yaml"
    with pytest.raises(FileNotFoundError, match="not found"):
        AppConfig.load(missing_file)


def test_production_rejects_default_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="secure JWT secret is required"):
        AppConfig(
            environment="production",
            app_jwt_secret="dev-secret-change-in-prod",
        )


def test_production_accepts_secure_jwt_secret() -> None:
    cfg = AppConfig(
        environment="production",
        app_jwt_secret="strong-prod-secret-key-12345",
    )
    assert cfg.environment == "production"
    assert cfg.app_jwt_secret == "strong-prod-secret-key-12345"


def test_invalid_type_fails_validation() -> None:
    with pytest.raises(ValidationError):
        AppConfig(langfuse_host=12345)  # type: ignore[arg-type]


def test_real_base_yaml_loads_llm_roles() -> None:
    base_yaml = Path("configs/base.yaml")
    cfg = AppConfig.load(base_yaml)
    assert cfg.llm.default_provider == "groq"
    assert cfg.llm.roles["router"] == "openai/gpt-oss-20b"
    assert cfg.llm.roles["answer"] == "openai/gpt-oss-120b"
    assert cfg.llm.roles["judge"] == "openai/gpt-oss-120b"
    assert cfg.llm.providers["groq"].base_url == "https://api.groq.com/openai/v1"
    assert (
        cfg.llm.providers["gemini"].base_url
        == "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    assert cfg.llm.fallback_provider == "gemini"
    assert cfg.llm.fallback_model == "gemini-3.1-flash-lite"
    assert cfg.llm.fallbackmodel == "gemini-3.1-flash-lite"
    assert cfg.llm.fallbackprovider == "gemini"
