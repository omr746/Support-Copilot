#!/usr/bin/env bash
# scripts/check_env.sh
#
# Plan Step 0.1 test: checks that the tools needed for Support Copilot are
# installed and prints their versions.
#
# Exit codes:
#   0  every check passed
#   1  at least one tool is missing or unusable
#
# Usage:
#   ./scripts/check_env.sh
#   REQUIRED_PYTHON=3.13 ./scripts/check_env.sh     # override the Python version
#
# Windows: run it inside WSL2 (Ubuntu) or Git Bash.

set -u

REQUIRED_PYTHON="${REQUIRED_PYTHON:-3.12}"
failures=0

ok() {
  printf '  [ OK ]  %-16s %s\n' "$1" "$2"
}

fail() {
  printf '  [FAIL]  %-16s %s\n' "$1" "$2"
  failures=$((failures + 1))
}

# Print only the first line of a multi-line string (bash builtins only).
first_line() {
  local text="$1"
  printf '%s' "${text%%$'\n'*}"
}

# check_tool <label> <command> <install hint> [version args...]
# Default version argument is --version.
check_tool() {
  local label="$1" cmd="$2" hint="$3"
  shift 3
  local -a version_args=("$@")
  if [ "${#version_args[@]}" -eq 0 ]; then
    version_args=(--version)
  fi

  if ! command -v "$cmd" >/dev/null 2>&1; then
    fail "$label" "not found. $hint"
    return 1
  fi

  local output
  if ! output="$("$cmd" "${version_args[@]}" 2>&1)"; then
    fail "$label" "found, but '$cmd ${version_args[*]}' failed"
    return 1
  fi

  ok "$label" "$(first_line "$output")"
}

# Python is OK if the system interpreter is the required minor version,
# or if uv can provide that version (uv-managed Python is what the project uses).
check_python() {
  local py="" candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      py="$candidate"
      break
    fi
  done

  if [ -n "$py" ]; then
    local output version minor
    output="$("$py" --version 2>&1)"
    version="${output#Python }"
    minor="${version%.*}"
    if [ "$minor" = "$REQUIRED_PYTHON" ]; then
      ok "python" "$(first_line "$output") (system)"
      return 0
    fi
  fi

  if command -v uv >/dev/null 2>&1; then
    local found
    if found="$(uv python find "$REQUIRED_PYTHON" 2>/dev/null)"; then
      ok "python" "$REQUIRED_PYTHON available through uv: $(first_line "$found")"
      return 0
    fi
  fi

  if [ -n "$py" ]; then
    fail "python" "found $(first_line "$output") but $REQUIRED_PYTHON is required. Run: uv python install $REQUIRED_PYTHON"
  else
    fail "python" "not found. Install Python $REQUIRED_PYTHON, or install uv and run: uv python install $REQUIRED_PYTHON"
  fi
  return 1
}

check_docker() {
  check_tool "docker" docker "Install Docker Desktop (on Windows enable WSL2 integration)." --version || return 1

  if docker info >/dev/null 2>&1; then
    ok "docker daemon" "running"
  else
    fail "docker daemon" "docker is installed but the daemon is not reachable. Start Docker Desktop or the docker service"
  fi

  if docker compose version >/dev/null 2>&1; then
    ok "docker compose" "$(first_line "$(docker compose version 2>&1)")"
  else
    fail "docker compose" "the compose plugin is missing. Update Docker Desktop or install docker-compose-plugin"
  fi
}

echo "Support Copilot environment check (required Python: $REQUIRED_PYTHON)"
echo

check_tool "git" git "Install Git from https://git-scm.com"
check_tool "uv" uv "Install uv from https://docs.astral.sh/uv/"
check_python
check_docker
check_tool "ffmpeg" ffmpeg "Install ffmpeg (for example: sudo apt install ffmpeg, or brew install ffmpeg)." -version

echo
if [ "$failures" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi

echo "$failures check(s) failed. Fix the items marked [FAIL] and run this script again."
exit 1