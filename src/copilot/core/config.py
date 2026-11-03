"""Application settings loader combining YAML, environment variables, and defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import model_validator
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

    environment: str = "development"
    app_jwt_secret: str = "dev-secret-change-in-prod"
    crawl_contact: str = ""

    # Provider API keys
    groq_api_key: str = ""
    gemini_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

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

        class ConfigWithYaml(AppConfig):
            _yaml_path: ClassVar[Path | None] = None

        ConfigWithYaml._yaml_path = yaml_path

        return ConfigWithYaml(**overrides)
