#!/usr/bin/env bash
# Run in Bash on Linux, macOS, or WSL; credentials stay in process memory.
set +x
set -euo pipefail
if (( $# != 1 )); then
  printf 'Usage: %s /absolute/path/to/bridge.toml\n' "$0" >&2
  exit 2
fi
bridge_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v uv >/dev/null 2>&1; then
  printf 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
  exit 1
fi
if [[ ! -f "$1" ]]; then
  printf 'Configuration file does not exist.\n' >&2
  exit 1
fi
# Resolve the locked runtime before prompting for secrets.
uv sync --locked --no-dev --project "$bridge_root"
trap 'unset SLACK_BOT_TOKEN SLACK_APP_TOKEN' EXIT
if [[ -z "${SLACK_BOT_TOKEN:-}" ]]; then
  IFS= read -r -s -p 'Slack bot token (hidden): ' SLACK_BOT_TOKEN </dev/tty
  printf '\n' >/dev/tty
fi
if [[ -z "${SLACK_APP_TOKEN:-}" ]]; then
  IFS= read -r -s -p 'Slack app token (hidden): ' SLACK_APP_TOKEN </dev/tty
  printf '\n' >/dev/tty
fi
export SLACK_BOT_TOKEN SLACK_APP_TOKEN
uv run --locked --no-sync --no-dev --project "$bridge_root" -- python -m codex_slack_bridge --config "$1"
