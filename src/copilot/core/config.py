"""Application settings loader combining YAML, environment variables, and defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML configuration safely."""
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    content = yaml.safe_load(path.read_text(encoding="utf-8"))

    if content is None:
        return {}

    if not isinstance(content, dict):
        raise ValueError(f"Expected a YAML mapping in configuration file: {path}")

    return content


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Custom Pydantic settings source for YAML configuration."""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        yaml_data: dict[str, Any],
    ) -> None:
        super().__init__(settings_cls)
        self.yaml_data = yaml_data

    def get_field_value(
        self,
        field: FieldInfo,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        """Retrieve a field value from YAML."""
        return self.yaml_data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        """Return YAML values matching declared settings fields."""
        return {
            field_name: self.yaml_data[field_name]
            for field_name in self.settings_cls.model_fields
            if field_name in self.yaml_data
        }


class LLMProviderConfig(BaseModel):
    """Configuration for an LLM provider endpoint."""

    model_config = ConfigDict(extra="ignore")

    base_url: str
    default_model: str
    timeout: float = 30.0


class LLMConfig(BaseModel):
    """Configuration for LLM providers and role-to-model mapping."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    default_provider: str = "groq"
    fallback_provider: str | None = Field(
        default="gemini",
        alias="fallbackprovider",
        description="Secondary fallback provider name from providers mapping.",
    )
    fallback_model: str | None = Field(
        default="gemini-3.1-flash-lite",
        alias="fallbackmodel",
        description="Model override for the fallback provider.",
    )
    roles: dict[str, str] = Field(
        default_factory=lambda: {
            "router": "openai/gpt-oss-20b",
            "answer": "openai/gpt-oss-120b",
            "judge": "openai/gpt-oss-120b",
        }
    )

    @property
    def fallbackmodel(self) -> str | None:
        """Alias property for fallback_model."""
        return self.fallback_model

    @property
    def fallbackprovider(self) -> str | None:
        """Alias property for fallback_provider."""
        return self.fallback_provider

    providers: dict[str, LLMProviderConfig] = Field(
        default_factory=lambda: {
            "groq": LLMProviderConfig(
                base_url="https://api.groq.com/openai/v1",
                default_model="openai/gpt-oss-20b",
                timeout=30.0,
            ),
            "gemini": LLMProviderConfig(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                default_model="gemini-3.1-flash-lite",
                timeout=30.0,
            ),
        }
    )


class AppConfig(BaseSettings):
    """Core application configuration.

    Source priority, highest to lowest:
      1. Explicit initialization arguments
      2. OS environment variables
      3. .env file
      4. YAML configuration
      5. Pydantic field defaults
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("environment", "ENVIRONMENT", "APP_ENV", "app_env"),
    )
    app_jwt_secret: str = "dev-secret-change-in-prod"
    crawl_contact: str = ""

    # Provider API keys
    groq_api_key: str = ""
    gemini_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # LLM settings and role configuration
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Class-level holder for custom YAML source path (ignored by Pydantic model fields)
    _yaml_path: ClassVar[Path | None] = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Define the settings source priority."""
        yaml_path = getattr(settings_cls, "_yaml_path", None)
        yaml_data = _load_yaml(yaml_path) if yaml_path is not None else {}

        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSettingsSource(settings_cls, yaml_data),
            file_secret_settings,
        )

    @model_validator(mode="after")
    def validate_production_secrets(self) -> AppConfig:
        """Prevent using the development JWT secret in production."""
        if self.environment == "production":
            if not self.app_jwt_secret or self.app_jwt_secret == "dev-secret-change-in-prod":
                raise ValueError("A secure JWT secret is required in production.")

        return self

    @classmethod
    def load(
        cls,
        yaml_path: Path | None = None,
        **overrides: Any,
    ) -> AppConfig:
        """Load configuration from YAML and other configured sources."""
        target_yaml = yaml_path
        if target_yaml is None and Path("configs/base.yaml").is_file():
            target_yaml = Path("configs/base.yaml")

        class ConfigWithYaml(AppConfig):
            _yaml_path: ClassVar[Path | None] = None

        ConfigWithYaml._yaml_path = target_yaml

        return ConfigWithYaml(**overrides)
