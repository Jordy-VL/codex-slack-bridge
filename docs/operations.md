# Local Slack → Codex discussion pilot

Tracking: [#185](https://github.com/Jordy-VL/form2agent/issues/185).

An internal Slack bot can route messages to a locally running Codex App Server
using the owner's existing ChatGPT sign-in. It does not require the official
Business/Enterprise Slack responder deployment. It consumes the owner's included
Codex usage; it does not create additional included capacity. The local computer
and bridge must remain running. The owner starts/stops it explicitly.

## Scope and routing

- Each `(workspace, channel, root message timestamp)` has one dedicated persisted
  Codex thread. Existing desktop chats are never selected or read. These sessions
  are not automatically attached to the desktop sidebar.
- An allowed user mentions your bot in an allowed public channel to start.
  Later plain replies from allowed users in that thread are queued automatically.
  Separate Slack threads create separate Codex sessions. Replies arriving during a
  turn wait for the next turn rather than interrupting it.
- Only messages received after activation are available. A mention midway through
  an existing thread does not backfill its earlier context. Paste relevant issue
  text/decisions into the thread. Slack links, attachments, reactions, edits and
  deletions are not fetched or reconciled. Optional read-only GitHub access below
  can inspect published repository state; this pilot cannot open PRs.
- Allowlists apply before enqueueing and again before execution. Ignore bot
  messages, message subtypes and duplicate mention/message deliveries. An app can
  receive other messages in channels it joins, but the bridge stores only accepted
  messages. Do not invite it to personal/private channels.
- Discussion and plans only. Shell, general MCP, apps, plugins, hooks,
  browser/computer use, subagents and image tools are disabled; unexpected server
  tool/approval requests are rejected. No public listener or tunnel is required.
  Older App Server read-only sandboxing does **not** confine filesystem reads to
  one project, which is why this pilot offers only the optional repository-scoped GitHub
  reader instead of shell/file-system access. Tool policy is checked from the
  effective server config.
- The session's messages and generated replies are visible to that Slack channel.
  Do not send private session contents or secrets into it. Accepted messages and
  replies also persist in private local SQLite and Codex session storage.

## Create the internal Slack app

1. Review [the app manifest](../examples/slack-app-manifest.json).
   In [Slack app management](https://api.slack.com/apps), create an app **from a
   manifest**, select your workspace, paste the JSON and review its permissions.
2. Authorize creation/installation with your chosen bot name. Its bot scopes are
   `app_mentions:read`, `channels:history` and `chat:write`: read mentions and
   public-channel messages where invited, and post messages. No DM/private-channel,
   user-token, file or workspace-admin scopes are requested. Adding it consumes
   one of Free Slack's app slots; no Slack paid upgrade is part of this procedure.
3. Under **Basic Information → App-Level Tokens**, generate an app token with only
   `connections:write`. Socket Mode must be enabled. This `xapp-…` token opens the
   outbound connection; the installed bot's `xoxb-…` token reads/posts as the bot.
4. Keep both tokens out of Git, shell history, issues and chat. Enter them using
   hidden input in the local launching environment or an OS secret store. Never
   paste them into a Codex conversation. The bridge reads `SLACK_APP_TOKEN` and
   `SLACK_BOT_TOKEN`; neither is forwarded to the Codex child process.
5. Add your bot under each public channel’s **Agents & apps** and include
   its channel ID in the private configuration. Do not
   run multiple personal runners on the same bot/app token pair: Socket Mode load
   balances deliveries rather than broadcasting them. Give each colleague a
   separate app identity/token pair if piloting personal runners in parallel.

Creating persistent app credentials/authorizing these permissions through the
browser requires the owner's action-time confirmation. Prepare/review the app
first; this is separate from the already connected personal Slack plugin.

## Local configuration and launch

Install this standalone package as described in [the README](../README.md).

Keep the following TOML outside Git, with owner-only filesystem permissions. Use
absolute paths and the Codex installation that is already signed in. On WSL a
Windows executable uses `/mnt/c/.../codex.exe`; `codex_cwd` must be an absolute
**Windows** path pointing at an empty local directory. `codex_config` is the
existing Windows config file, accessed through `/mnt/c/...`. No authentication
file is copied. On native Linux use Linux paths throughout.

```toml
team_id = "T_REPLACE_ME"
channels = ["C_REPLACE_ME"]
users = ["U_REPLACE_ME"]
owner_label = "My local Codex"
codex_executable = "/absolute/path/to/codex"
codex_config = "/absolute/path/to/.codex/config.toml"
codex_cwd = "/absolute/path/to/empty-session-root"
state_dir = "/absolute/path/to/private-bridge-state"
included_usage_verified = false
```

```sh
codex-slack-bridge --config /private/bridge.toml --check
codex-slack-bridge --config /private/bridge.toml
```

`--check` verifies ChatGPT authentication, provider and tool policy without a model
turn or Slack token. It does not establish a successful reply. Before enabling,
verify included usage and paid-overage eligibility and set
`included_usage_verified = true`. Before **every** turn the bridge additionally
checks the account: it refuses non-ChatGPT authentication, unknown credit state,
extra credits or exhausted usage windows. It forces the OpenAI provider and
standard service tier and does not inherit API-key/base-URL environment overrides.
If paid credits are purchased later, it stops instead of spending them. Never
change the guard to bypass uncertainty without the user's spending authorization.

Launch from Bash with `./scripts/start-slack-bridge.sh /private/bridge.toml`.
The script prompts for missing tokens without echoing them; preexisting token
environment variables are also supported. Use Bash on Linux, macOS or WSL.
It has no PowerShell or DPAPI dependency. Ctrl+C stops the foreground runner.
There is no automatic startup. Never enable shell tracing or SDK debug logging.

## Validation and recovery

### Optional read-only GitHub access

The local `form2agent_github_readonly` MCP worker exposes one tool,
`form2agent_github_read`, for **the configured github.com owner/repository only**. It uses the bridge
owner's existing authenticated GitHub CLI on the worker host. No new GitHub token, Slack
scope or global Codex configuration is needed. Install/authenticate `gh` locally
if it is not already available; do not paste credentials into Slack. The worker
uses the installed CLI or `~/.local/bin/gh` and calls only repository-scoped GitHub REST
**GET** endpoints on `github.com`.

Add this optional command array to the private bridge TOML, replacing absolute
paths for the selected checkout/runtime:

```toml
github_mcp_command = [
  "wsl.exe", "-d", "Ubuntu", "--cd", "/absolute/path/to/codex-slack-bridge", "--",
  "/absolute/path/to/uv", "run", "--locked", "--no-dev",
  "--project", "/absolute/path/to/codex-slack-bridge",
  "--", "python", "-m", "codex_slack_bridge.jobs.github_mcp",
  "--repository", "your-org/your-repo"
]
```

This example is for a **Windows** Codex executable. Native Linux Codex can use
`["/absolute/path/to/uv", "run", "--locked", "--no-dev", "--project", "/absolute/path/to/codex-slack-bridge", "--", "python", "-m", "codex_slack_bridge.jobs.github_mcp", "--repository", "your-org/your-repo"]`.
The MCP module is an internal worker; user commands still belong to the main CLI.
Only this exact configured worker and its single read tool may be enabled.
Omit `github_mcp_command` for the original conversation-only mode. The other
connectors, MCP servers and shell tools remain disabled in both modes.
The installed Codex version requires its internal tool-discovery host for MCP
discovery; this host is enabled only in GitHub-reader mode. General code mode
and command execution remain disabled. `--check` validates that distinction.

The worker requires `--repository owner/repo`; URLs, traversal and missing slugs
are rejected. The model cannot select or override this scope. Only github.com
is supported. Without an explicit ref, the reader discovers and caches the
repository default branch for the worker lifetime.

The tool supports recent commits/issues/PRs/workflow runs, a paginated recursive
file tree (optional path prefix), commit-pinned UTF-8
files (line ranges), directories, plain-text code/issue search, issue and PR
details, conversation comments, inline PR comments/reviews, changed-file patches
and commit checks. Outputs carry source URLs, retrieval times, pagination and
truncation flags. Large/binary files, submodules and external symlinks are
unsupported. Search is scoped and bounded; GitHub indexing may lag recent edits or return no
matches for private code. Use the recursive tree to locate files, then read only
relevant files at its returned commit; immutable commit reads avoid redundant
branch-resolution requests.
This reads **live GitHub**, so uncommitted or unpushed work is not visible.

The underlying existing GitHub login can have broader privileges; the adapter
does not delegate its credentials or a general CLI to the model. Its fixed tool
surface rejects other repositories, arbitrary URLs/endpoints, search-scope
overrides, path traversal and all writes. GitHub token environment overrides are
removed from the Codex child. Treat returned repository text as evidence rather
than instructions to change permissions or execute commands.

Restart after changing the command/config. Keep the existing SQLite database.
This installed Codex version retains an old session's initial tool/instruction
view even after resume/reload. When the bridge's worker command or repository scope changes, it starts
a fresh underlying Codex session for that **same Slack thread**, records the old
and new session IDs in private `session_changes`, and supplies that thread's
completed recorded messages and bot replies as quoted historical context.
It never copies another thread or an existing desktop chat. Historical context
is capped at 100,000 serialized characters with an explicit omitted-message
count for long threads. Old sessions are retained; no Slack messages or queue
history are deleted. Bootstrap context remains saved until a turn succeeds, so
an interrupted first upgraded turn does not silently lose the conversation.
Future turns resume the new session normally. To restart,
first wait until no event is `running`/`posting`, then stop the old bridge cleanly
and wait for its lock to be removed before starting another runner. Do not run
two listeners on the same app token.

Test from an existing Slack thread: ask the bot to read
a known text file in your configured repository, report a verifiable detail and cite the
returned GitHub link. Then ask for an issue or PR's current state/checks. A tool
error must be reported honestly; the bot should not ask you to paste secrets or
local bridge configuration as the normal path to repository reading.

### Thread and queue checks

After connection, send `@Local Codex propose a plan for …` as an allowed user.
Verify the answer appears **inside its thread**. Send a plain follow-up and verify
it uses the earlier context; start a separate root mention and verify isolation.
Test a colleague only after adding their Slack user ID to the allowlist and
restarting. No existing conversations or repo collaborator lists are automatic
authorization to execute commands through someone's personal account.

Accepted input is committed to SQLite before Slack acknowledgement. A single
worker processes turns serially. Queued accepted input and completed-but-unposted
answers survive a restart; answers are saved before posting. This is **not** an
offline Slack inbox: messages sent while the connection is down are not guaranteed
to arrive later. Repost relevant context after reconnection. No archive or nightly
history reconciliation is implemented here.

A unique accepted event gets a threaded queue acknowledgement after its durable
save and Socket Mode ACK, including while another request is running. Duplicate
Slack deliveries produce no duplicate acknowledgements. If Slack posting is down,
input remains saved. Research turns have a separate bounded **15-minute** deadline
rather than the RPC request's three minutes; reasoning effort is medium. A failed
request posts a sanitized failure notice when Slack is reachable; no provider
body or credential is sent. The worker attempts to interrupt an expired turn and
confirms that exact turn stopped before continuing other threads. If interruption
cannot be confirmed, or another transport/delivery error occurs, it stops for review.
Only the lifecycle owner closes the Socket Mode SDK; cleanup still releases the
runner/SQLite/lock if SDK close fails.

A crash or timeout during generation/posting leaves the event **uncertain** and
blocks later work in that same thread. Other accepted input remains saved; a
confirmed interruption does not block other threads. Stop the process, then inspect:

```sh
codex-slack-bridge --config /private/bridge.toml --status
codex-slack-bridge --config /private/bridge.toml --skip 12
```

Review Slack and the dedicated Codex thread before skipping an uncertain event.
Skipping does not undo a completed turn or post; it unblocks later queued input.
Repost the request explicitly if needed. There is no silent model/post replay.
The state directory's `runner.lock` prevents concurrent runners. After an unclean
process exit, verify no bridge/App Server child remains before removing a stale
lock. Never remove the SQLite database to clear a lock: that loses routing and
duplicate-delivery history. Keep state files private and do not commit them.

The future Git archive, code/PR execution permissions, offline reconciliation and
team-wide routing remain separate acceptance criteria in #185. Local fake
transports validate routing; only the live check proves Slack connectivity and
actual Codex replies for a particular installation.

## Sources

- [Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp)
- [GitHub CLI API transport](https://cli.github.com/manual/gh_api)
- [GitHub repository contents](https://docs.github.com/en/rest/repos/contents)
- [Slack Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)
- [Python SDK Socket Mode](https://docs.slack.dev/tools/python-slack-sdk/socket-mode/)
- [Pinned SDK metadata](https://pypi.org/project/slack-sdk/3.45.0/)


### Public-channel expansion

Add each new public channel ID to `channels`, invite the bot, and restart after
its queue drains. Add participating human IDs explicitly to `users`. Private
channels and DMs are outside this package's current scope.

### Slack reply rendering

The bridge sends standard Markdown through Slack's native `markdown_text` API
argument. Slack renders bold, labeled links, inline/fenced code, lists and tables;
it also derives the notification/accessibility representation. No regex Markdown
converter or model rerun is needed. This argument is never combined with `text`
or `blocks`. The 12,000-character limit includes the owner header. Longer replies
retain the existing 35,000-character answer contract as one complete, explicitly
labeled plain-text post; they are neither truncated nor split into ambiguous
multiple deliveries. Preview unfurls stay disabled. Stored answers remain the
original Markdown so an existing bot reply can be reformatted in place.

Reference: [Slack chat.postMessage](https://docs.slack.dev/reference/methods/chat.postMessage/).
