"""Socket Mode lifecycle for the explicitly started local discussion bridge."""

import json
import logging
import os
from pathlib import Path
import threading

from codex_slack_bridge.services.codex_rpc import AppServer, TurnTimedOut, server_command
from codex_slack_bridge.services.slack_messages import message_body
from codex_slack_bridge.services.slack_bridge import BridgeConfig, Store, process_one

LOG = logging.getLogger(__name__)


def run(config_path: Path, *, check=False):
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse

    config = BridgeConfig.load(config_path)
    # Starting a second bridge against the same state can repeat external work.
    # O_EXCL is portable on Windows/WSL; a stale lock requires operator review.
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = config.state_dir / "runner.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    runner = store = socket = None
    stopped = threading.Event()
    try:
        runner = AppServer(server_command(config.codex_executable, config.codex_config, config.github_mcp_command),
                           config.codex_cwd, github_command=config.github_mcp_command)
        preflight = runner.preflight()
        if check:
            return preflight
        if config.included_usage_verified is not True:
            raise ValueError("Verify included ChatGPT usage and disable paid overages before enabling the bridge")
        bot_token, app_token = os.environ.get("SLACK_BOT_TOKEN", ""), os.environ.get("SLACK_APP_TOKEN", "")
        if not bot_token.startswith("xoxb-") or not app_token.startswith("xapp-"):
            raise ValueError("Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN outside Git")
        web = WebClient(token=bot_token, retry_handlers=[])
        identity = web.auth_test()
        if identity["team_id"] != config.team_id:
            raise ValueError("Slack token belongs to a different workspace")
        store = Store(config.state_dir / "state.sqlite3")
        socket = SocketModeClient(app_token=app_token, web_client=web, concurrency=1)

        def receive(client, request):
            accepted = False
            if request.type == "events_api":
                # Commit accepted input before acknowledging Slack's delivery.
                accepted = store.accept(config, request.payload, identity["user_id"])
            client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
            if accepted:
                event = request.payload["event"]
                try:
                    post(event["channel"], event.get("thread_ts") or event["ts"],
                         "I’ve queued this request. I process requests one at a time; "
                         "repository questions can take a few minutes.")
                except Exception:
                    LOG.warning("Could not post queue acknowledgement; accepted input remains saved")

        def post(channel, root, answer):
            web.chat_postMessage(channel=channel, thread_ts=root,
                                 **message_body(config.owner_label, answer),
                                 unfurl_links=False, unfurl_media=False)

        def worker():
            while not stopped.is_set():
                try:
                    worked = process_one(store, config, runner, post, notify=post)
                except TurnTimedOut:
                    LOG.warning("A timed-out thread needs review; continuing other threads")
                    continue
                except Exception:
                    # Never log Slack text, prompts, token values or provider bodies.
                    LOG.error("A message needs review; inspect slack-bridge --status")
                    stopped.set()
                    return
                if not worked:
                    stopped.wait(0.5)

        socket.socket_mode_request_listeners.append(receive)
        socket.connect()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        LOG.warning("Local discussion bridge connected; Ctrl+C stops it")
        while not stopped.wait(0.5):
            pass
        raise RuntimeError("Bridge stopped after a message error; review local status before restarting")
    finally:
        stopped.set()
        # Only the lifecycle owner closes the SDK; worker/main concurrent closes
        # can race. A cleanup failure must not strand later resources/the lock.
        try:
            if socket:
                socket.close()
        finally:
            try:
                if runner:
                    runner.close()
            finally:
                try:
                    if "thread" in locals():
                        thread.join(timeout=10)
                    if store:
                        store.close()
                finally:
                    lock.unlink(missing_ok=True)


def inspect(config_path: Path, skip: int | None = None):
    config = BridgeConfig.load(config_path)
    if (config.state_dir / "runner.lock").exists():
        raise ValueError("Stop the bridge before inspecting/recovering its state")
    store = Store(config.state_dir / "state.sqlite3")
    try:
        if skip is not None:
            store.skip(skip)
        return json.dumps(store.status(), indent=2)
    finally:
        store.close()
