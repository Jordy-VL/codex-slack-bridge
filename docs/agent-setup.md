# Agent-assisted setup

For coding agents with a local terminal and file access. The installing agent
can be any agent; the bridge runtime requires Codex signed in with ChatGPT.

## Copyable task

> Set up https://github.com/Jordy-VL/codex-slack-bridge on this computer.
> Read AGENTS.md, README.md and docs/agent-setup.md first. Inspect existing
> installations and processes before creating another runner. Use Bash and uv with the committed lockfile. Keep configuration, credentials and state
> outside the checkout. Ask only for missing non-secret settings: Slack
> workspace/channel/user IDs and an optional github.com owner/repo. Have me
> authenticate locally and enter Slack tokens in the hidden launcher prompt;
> never ask me to paste credentials into chat. Run local tests and token-free
> preflight. Report what is ready and what needs my action. Start the listener,
> run a model turn or send Slack messages only when I request those steps.
> Do not install automatic startup or weaken account/tool checks.

## 1. Inspect and install

Read applicable workspace instructions and repository AGENTS.md. Inspect any
existing checkout, private config, state and running listener. Preserve changes;
do not reset a checkout or overwrite configuration to make installation easier.

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/), Bash, Python 3.11+, and an installed Codex executable already signed in
with ChatGPT. The supplied launcher runs on Linux, macOS or WSL; native Windows
users should use WSL Bash. An agent without terminal access can guide the owner
through these steps instead.

```bash
git clone https://github.com/Jordy-VL/codex-slack-bridge.git
cd codex-slack-bridge
uv sync --locked
uv run --locked pytest -q
bash -n scripts/start-slack-bridge.sh
```

Use an existing checkout if present. No hosted CI, cloud deployment, paid API
or startup service is required. If Codex is missing or signed out, have the
owner follow the [official authentication guide](https://learn.chatgpt.com/docs/auth).
Do not print or copy authentication files or dump the full Codex configuration.

## 2. Prepare private configuration

For a new configuration only:

```bash
mkdir -p "$HOME/.config/codex-slack-bridge"
chmod 700 "$HOME/.config/codex-slack-bridge"
# If bridge.toml exists, preserve it and inspect the existing setup first.
cp -n examples/bridge.example.toml "$HOME/.config/codex-slack-bridge/bridge.toml"
chmod 600 "$HOME/.config/codex-slack-bridge/bridge.toml"
mkdir -p "$HOME/.local/state/codex-slack-bridge" "$HOME/.local/share/codex-slack-session"
chmod 700 "$HOME/.local/state/codex-slack-bridge" "$HOME/.local/share/codex-slack-session"
```

Replace placeholders in the private TOML. Use literal absolute paths: TOML
does not expand `$HOME`.

| Setting | Meaning |
| --- | --- |
| `team_id` | Slack workspace ID |
| `channels` | Explicit public-channel IDs |
| `users` | Explicit allowed human Slack user IDs |
| `owner_label` | Human-readable identity on replies |
| `codex_executable` | Signed-in executable launched by the bridge host |
| `codex_config` | Existing Codex config readable by the bridge host |
| `codex_cwd` | Empty session directory expressed for the Codex host |
| `state_dir` | Private durable state directory on the bridge host |
| `included_usage_verified` | Keep false until the owner verifies included usage and overage settings |
| `github_mcp_command` | Optional worker command with `--repository owner/repo` |

On native Linux/macOS, use native absolute paths throughout. When WSL launches
Windows Codex, executable/config paths use `/mnt/c/...` from WSL, while
`codex_cwd` uses a Windows path to an empty Windows directory. A Windows path in
TOML can use a single-quoted literal, such as `'C:\Users\YourName\codex-session'`.
Create that Windows directory instead of using the Linux session root.

## 3. Optionally connect GitHub

The owner authenticates `gh` locally on the worker host. Confirm repository
access with a metadata read; do not print authentication tokens. Add this to
the private TOML for native Linux/macOS Codex:

```toml
github_mcp_command = [
  "/absolute/path/to/uv", "run", "--locked", "--no-dev",
  "--project", "/absolute/path/to/codex-slack-bridge",
  "--", "python", "-m", "codex_slack_bridge.jobs.github_mcp",
  "--repository", "your-org/your-repo"
]
```

The command is launched by **Codex**, so Windows Codex needs a Windows-runnable
command even if the bridge and worker are in WSL:

```toml
github_mcp_command = [
  "wsl.exe", "-d", "Ubuntu", "--cd", "/absolute/path/to/codex-slack-bridge", "--",
  "/absolute/path/to/uv", "run", "--locked", "--no-dev",
  "--project", "/absolute/path/to/codex-slack-bridge",
  "--", "python", "-m", "codex_slack_bridge.jobs.github_mcp",
  "--repository", "your-org/your-repo"
]
```

Public/private repositories on github.com are supported according to the
existing login's access. GitHub Enterprise hosts are not supported. One runner
has one configured repository; separate Slack threads isolate conversations,
not repository configuration. Omit the setting for conversation-only mode.
Never insert an access token into this command.

## 4. Have the owner authorize Slack

Follow [Slack app creation](operations.md#create-the-internal-slack-app) using
[the manifest](../examples/slack-app-manifest.json). The owner chooses the
workspace, reviews permissions and installs the app. The bot scopes are
`app_mentions:read`, `channels:history` and `chat:write`. Enable Socket Mode and
create an app-level token with only `connections:write`.

Invite the bot to each configured public channel. Do not add DM/private-channel
permissions. Each personal runner needs a separate app/token pair: concurrent
listeners sharing tokens receive load-balanced deliveries.

The owner supplies tokens locally through the launcher's hidden prompt or an
existing secret manager. Never request credentials in chat, put them in task
prompts or command history, or commit them. No public server or tunnel is needed.

## 5. Check before starting

```bash
uv run --locked codex-slack-bridge --config "$HOME/.config/codex-slack-bridge/bridge.toml" --check
```

This starts App Server and validates authentication and effective tool policy
without Slack tokens, a model turn or a Slack post. GitHub mode may start the
configured worker. It does not establish successful GitHub reads, Slack delivery
or spending eligibility. Report failures without exposing private configuration
or provider responses.

The owner verifies included usage and paid-overage settings before setting
`included_usage_verified = true`. Runtime checks still refuse paid credits,
unknown states and exhausted windows. Do not bypass these checks if a Codex
update changes its response schemas or feature flags. Consult the current
[App Server protocol](https://learn.chatgpt.com/docs/app-server).

## 6. Start and smoke-test when requested

Use a foreground Bash terminal the owner can access for hidden token input:

```bash
./scripts/start-slack-bridge.sh "$HOME/.config/codex-slack-bridge/bridge.toml"
```

If the agent terminal has no interactive TTY, hand this command to the owner or
use their existing secret-manager environment. Do not create plaintext token
files as a workaround or background the listener implicitly. Ctrl+C stops it.

Have the owner mention the bot in an allowed channel, verify a threaded reply,
continue with a follow-up, and verify isolation in a second thread. For GitHub,
ask for a known file and check its source link. Only this live test proves the
end-to-end connection; tests/preflight alone do not.

## 7. Handoff and recovery

Report the checkout, private config/state paths (never secret contents), host
arrangement, allowed channels/users, selected repository, test/preflight results
and remaining owner actions. Distinguish installed, preflight-passed and
live-tested. Include the launch command and how to stop/restart.

Keep SQLite across restarts. Drain running/posting work before stopping or
changing scope. A changed worker/repository creates a fresh Codex session for
the same Slack thread with its recorded history quoted; use a new Slack thread
for a clean conversation. For uncertain events, stop the bridge, inspect
`--status`, review Slack, and only then use `--skip ID`. Never silently replay
ambiguous work or delete the database to clear a lock. See [operations](operations.md).
