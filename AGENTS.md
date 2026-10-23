# Repository instructions

- Preserve fail-closed account, tool, workspace, channel and user checks.
- Never commit credentials, transcripts, state databases or local config.
- Use uv for dependencies and commands: `uv sync --locked` and
  `uv run --locked pytest`. Keep `uv.lock` current and run Bash syntax checks.
- Do not send Slack messages, run model turns, enable hosted CI, commit, push or
  publish without authorization for that action.
- The optional GitHub reader is restricted to the configured github.com
  owner/repository. Preserve slug validation, fixed GET transport, scoped search
  and response checks; tool inputs must never select another repository.
- Include worker command/repository changes in the session profile so old
  sessions cannot retain a stale tool scope.

## Installing for a user

- For setup requests, follow `docs/agent-setup.md`.
- Inspect existing configuration, state and processes before installing.
- Have the owner authenticate and supply tokens locally, outside chat.
- Distinguish local tests, token-free preflight and a successful live exchange.
