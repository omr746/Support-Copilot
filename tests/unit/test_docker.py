"""Unit tests for Dockerfile, docker-compose, and container security compliance (Plan Step 1.8)."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_dockerfile_exists_and_uses_multi_stage() -> None:
    """Dockerfile exists and implements a multi-stage build (builder and runtime)."""
    dockerfile_path = REPO_ROOT / "Dockerfile"
    assert dockerfile_path.is_file(), "Dockerfile must exist at repository root"

    content = dockerfile_path.read_text(encoding="utf-8")
    assert "AS builder" in content or "as builder" in content, (
        "Dockerfile must define builder stage"
    )
    assert "AS runtime" in content or "as runtime" in content, (
        "Dockerfile must define runtime stage"
    )
    assert "python:3.12-slim" in content, "Dockerfile must use python:3.12-slim base image"


def test_dockerfile_installs_ffmpeg_and_healthcheck_tool() -> None:
    """Dockerfile installs ffmpeg for voice processing and curl for container healthchecks."""
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ffmpeg" in content, "Dockerfile must install ffmpeg"
    assert "curl" in content, "Dockerfile must install curl for healthchecks"


def test_dockerfile_enforces_non_root_execution() -> None:
    """Dockerfile must create and switch to a non-root user (appuser) with UID 10001."""
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "useradd" in content or "adduser" in content, "Dockerfile must create a system user"
    assert "USER appuser" in content or "USER 10001" in content, (
        "Dockerfile must switch to non-root user"
    )
    assert "--chown=appuser" in content or "--chown=10001" in content, (
        "Copied files must be owned by non-root user"
    )


def test_dockerfile_defines_healthcheck_probe() -> None:
    """Dockerfile must specify a container HEALTHCHECK targeting /healthz."""
    content = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in content, "Dockerfile must specify HEALTHCHECK instruction"
    assert "/healthz" in content, "HEALTHCHECK must query /healthz endpoint"


def test_dockerignore_excludes_secrets_and_caches() -> None:
    """.dockerignore exists and excludes secrets, git history, virtual environments, and caches."""
    dockerignore_path = REPO_ROOT / ".dockerignore"
    assert dockerignore_path.is_file(), ".dockerignore must exist"

    content = dockerignore_path.read_text(encoding="utf-8")
    ignored_patterns = [
        line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")
    ]

    assert ".git" in ignored_patterns
    assert ".venv" in ignored_patterns
    assert ".env" in ignored_patterns
    assert "__pycache__" in ignored_patterns
    assert "tests" in ignored_patterns


def test_docker_compose_core_profile_and_services() -> None:
    """docker-compose.yml must define core profile with app, pgvector postgres, and redis."""
    compose_path = REPO_ROOT / "docker-compose.yml"
    assert compose_path.is_file(), "docker-compose.yml must exist"

    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "services" in data, "docker-compose.yml must declare services"

    services = data["services"]
    assert "app" in services, "Must declare 'app' service"
    assert "postgres" in services, "Must declare 'postgres' service"
    assert "redis" in services, "Must declare 'redis' service"

    # Verify core profile on all services
    for svc_name, svc in services.items():
        profiles = svc.get("profiles", [])
        assert "core" in profiles, f"Service '{svc_name}' must belong to the 'core' profile"

    # Verify pgvector image
    postgres_image = services["postgres"].get("image", "")
    assert "pgvector" in postgres_image, f"Postgres must use pgvector image, got: {postgres_image}"

    # Verify redis image
    redis_image = services["redis"].get("image", "")
    assert "redis" in redis_image, f"Redis must use redis image, got: {redis_image}"

    # Verify app dependencies and healthchecks
    assert "depends_on" in services["app"], "App must declare dependencies"
    depends = services["app"]["depends_on"]
    assert "postgres" in depends and depends["postgres"].get("condition") == "service_healthy"
    assert "redis" in depends and depends["redis"].get("condition") == "service_healthy"


def test_docker_compose_ci_file_configuration() -> None:
    """docker-compose.ci.yml must configure CI orchestration with healthchecks."""
    ci_compose_path = REPO_ROOT / "docker-compose.ci.yml"
    assert ci_compose_path.is_file(), "docker-compose.ci.yml must exist"

    data = yaml.safe_load(ci_compose_path.read_text(encoding="utf-8"))
    assert "services" in data

    services = data["services"]
    assert "app" in services
    assert "postgres" in services
    assert "redis" in services

    assert "healthcheck" in services["postgres"]
    assert "healthcheck" in services["redis"]


def test_check_docker_script_exists_and_enforces_size_limit() -> None:
    """scripts/check_docker.sh must enforce maximum image size and check all required endpoints."""
    script_path = REPO_ROOT / "scripts" / "check_docker.sh"
    assert script_path.is_file(), "scripts/check_docker.sh must exist"

    content = script_path.read_text(encoding="utf-8")
    assert "MAX_SIZE_MB" in content, "Script must configure image size limit"
    assert "docker compose" in content, "Script must orchestrate services via docker compose"
    assert "/healthz" in content, "Script must query /healthz"
    assert "/readyz" in content, "Script must query /readyz"
    assert "ffmpeg" in content, "Script must verify ffmpeg"
    assert "id -u" in content, "Script must verify non-root UID"


def test_docker_compose_avoids_hardcoded_secrets() -> None:
    """docker-compose.yml must not hardcode passwords and must interpolate POSTGRES_PASSWORD."""
    content = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${POSTGRES_PASSWORD" in content, (
        "POSTGRES_PASSWORD must be interpolated from environment"
    )
    assert "POSTGRES_PASSWORD: copilot\n" not in content, (
        "Password must not be hardcoded in compose file"
    )
