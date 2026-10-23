"""Offline routing/transport regressions with fictional Slack identities."""

import json
import queue
import sqlite3
import time

import pytest

from codex_slack_bridge.services.codex_rpc import (
    AppServer, DISABLED_FEATURES, child_environment, server_command,
)
from codex_slack_bridge.services.slack_bridge import BridgeConfig, Store, process_one


@pytest.fixture
def config(tmp_path):
    return BridgeConfig("T1", frozenset({"C1"}), frozenset({"U1", "U2"}),
                        "codex", tmp_path / "config.toml", str(tmp_path), tmp_path)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "state.sqlite3")
    yield value
    value.close()


def event(ts="100.001", *, root=None, user="U1", text="<@B1> discuss this", **extra):
    body = {"type": "message", "channel": "C1", "user": user, "text": text, "ts": ts, **extra}
    if root:
        body["thread_ts"] = root
    return {"team_id": "T1", "event": body}


class Runner:
    def __init__(self):
        self.sessions = []
        self.prompts = []

    def session(self, previous):
        value = previous or f"codex-{len(self.sessions) + 1}"
        self.sessions.append(value)
        return value

    def reply(self, codex_id, prompt):
        self.prompts.append((codex_id, json.loads(prompt)))
        return "A fictional response."


def test_same_thread_continues_another_is_isolated_and_duplicates_ignored(store, config):
    first = event()
    assert store.accept(config, first, "B1")
    first["event"]["type"] = "app_mention"
    assert not store.accept(config, first, "B1")
    assert store.accept(config, event("101.001", root="100.001", user="U2", text="Consider option B"), "B1")
    assert store.accept(config, event("102.001"), "B1")
    posts, runner = [], Runner()
    while process_one(store, config, runner, lambda *args: posts.append(args)):
        pass
    assert runner.sessions == ["codex-1", "codex-1", "codex-3"]
    assert [p[1] for p in posts] == ["100.001", "100.001", "102.001"]
    assert runner.prompts[1][1]["slack_user"] == "U2"
    assert runner.prompts[1][1]["message"] == "Consider option B"
    assert len(posts) == 3
    assert all(row["status"] == "done" for row in store.status())


@pytest.mark.parametrize("changes", [
    {"user": "stranger"}, {"channel": "private-channel"}, {"user": "B1"},
    {"bot_id": "another-bot"}, {"subtype": "message_changed"},
    {"hidden": True}, {"type": "reaction_added"}, {"text": "x" * 16001},
])
def test_unapproved_or_nonhuman_input_is_not_retained(store, config, changes):
    assert not store.accept(config, event(**changes), "B1")
    assert store.status() == []


def test_workspace_and_thread_scope(store, config):
    wrong_team = event()
    wrong_team["team_id"] = "T2"
    assert not store.accept(config, wrong_team, "B1")
    assert not store.accept(config, event(text="ordinary channel discussion"), "B1")
    assert not store.accept(config, event(root="99.000", text="untracked reply"), "B1")


def test_queued_work_survives_restart_with_saved_session(tmp_path, config):
    path = tmp_path / "state.sqlite3"
    store = Store(path)
    store.accept(config, event(), "B1")
    row = store.next(config)
    store.session(row, "existing-session")
    store.close()
    store = Store(path)
    try:
        runner = Runner()
        assert process_one(store, config, runner, lambda *_: None)
        assert runner.sessions == ["existing-session"]
    finally:
        store.close()


@pytest.mark.parametrize("status", ["running", "posting"])
def test_ambiguous_restart_blocks_only_affected_thread(tmp_path, config, status):
    path = tmp_path / "state.sqlite3"
    store = Store(path)
    store.accept(config, event(), "B1")
    first = store.next(config)
    store.set(first["id"], status)
    store.accept(config, event("101.001", root="100.001", text="followup"), "B1")
    store.accept(config, event("102.001"), "B1")
    store.close()
    store = Store(path)
    try:
        assert store.next(config)["root"] == "102.001"
        assert store.status()[-1]["status"] == "uncertain"
        store.skip(first["id"])
        assert store.next(config)["root"] == "100.001"
    finally:
        store.close()


def test_saved_answer_posts_after_restart_without_repeating_model(store, config):
    store.accept(config, event(), "B1")
    row = store.next(config)
    store.set(row["id"], "ready", answer="Already generated")
    posts, runner = [], Runner()
    assert process_one(store, config, runner, lambda *args: posts.append(args))
    assert runner.sessions == []
    assert posts == [("C1", "100.001", "Already generated")]


def test_post_error_does_not_retry_model_or_expose_message(store, config):
    store.accept(config, event(), "B1")
    runner = Runner()

    def broken(*args):
        raise TimeoutError("secret token should never be retained in errors")

    with pytest.raises(TimeoutError):
        process_one(store, config, runner, broken)
    assert not process_one(store, config, runner, broken)
    assert store.status()[0]["error"] == "TimeoutError"
    assert len(runner.prompts) == 1


def test_config_revocation_applies_to_already_queued_work(store, config):
    store.accept(config, event(), "B1")
    revoked = BridgeConfig("T1", config.channels, frozenset({"U2"}), "codex",
                           config.codex_config, config.codex_cwd, config.state_dir)
    assert store.next(revoked) is None


def test_tool_upgrade_replaces_engine_and_preserves_only_its_own_slack_context(store, config):
    original = Runner()
    store.accept(config, event(text="<@B1> Remember CEDAR"), "B1")
    process_one(store, config, original, lambda *_: None)
    store.accept(config, event("101.001", text="<@B1> Other thread private context"), "B1")
    process_one(store, config, original, lambda *_: None)
    store.accept(config, event("102.001", root="100.001", text="Read GitHub now"), "B1")
    upgraded = Runner()
    upgraded.tool_profile = "github-readonly-v1"
    upgraded.session = lambda previous: "new-github-session" if previous is None else previous
    process_one(store, config, upgraded, lambda *_: None)
    session, prompt = upgraded.prompts[0]
    assert session == "new-github-session"
    assert prompt["message"] == "Read GitHub now"
    assert prompt["earlier_thread_context"] == {"messages": [{
        "user": "U1", "ts": "100.001", "text": "<@B1> Remember CEDAR", "answer": "A fictional response."
    }], "older_messages_omitted": 0}
    assert store.session(store.db.execute("SELECT * FROM messages WHERE ts='102.001'").fetchone()) == session
    assert store.db.execute("SELECT old_codex_id,new_codex_id FROM session_changes").fetchone()[:] == (
        "codex-1", "new-github-session")
    assert store.db.execute("SELECT pending_history FROM threads WHERE root='100.001'").fetchone()[0] is None


def test_bootstrap_context_survives_failed_first_upgraded_turn_and_restart(tmp_path, config):
    path = tmp_path / "upgrade.sqlite3"
    store = Store(path)
    store.accept(config, event(), "B1")
    process_one(store, config, Runner(), lambda *_: None)
    store.accept(config, event("101.001", root="100.001", text="Read GitHub"), "B1")
    upgraded = Runner()
    upgraded.tool_profile = "github-readonly-v1"
    upgraded.session = lambda _: "new-session"
    upgraded.reply = lambda *_: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(TimeoutError):
        process_one(store, config, upgraded, lambda *_: None)
    store.close()
    store = Store(path)
    try:
        store.skip(store.status()[0]["id"])
        store.accept(config, event("102.001", root="100.001", text="Continue after review"), "B1")
        resumed = Runner()
        resumed.tool_profile = "github-readonly-v1"
        process_one(store, config, resumed, lambda *_: None)
        assert resumed.sessions == ["new-session"]
        assert resumed.prompts[0][1]["earlier_thread_context"]["messages"][0]["ts"] == "100.001"
        assert store.db.execute("SELECT count(*) FROM session_changes").fetchone()[0] == 1
    finally:
        store.close()


def test_legacy_database_upgrade_keeps_original_session_mapping(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE threads (team TEXT,channel TEXT,root TEXT,codex_id TEXT,PRIMARY KEY(team,channel,root))")
    db.execute("INSERT INTO threads VALUES ('T1','C1','100.001','legacy-session')")
    db.commit()
    db.close()
    store = Store(path)
    try:
        row = store.db.execute("SELECT codex_id,tool_profile,pending_history FROM threads").fetchone()
        assert row[:] == ("legacy-session", "discussion", None)
    finally:
        store.close()


def test_server_configuration_disables_named_mcp_and_removes_credentials(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text('[mcp_servers.example]\ncommand="example"\n')
    command = server_command("codex", p)
    assert "mcp_servers.example.enabled=false" in command
    assert 'forced_login_method="chatgpt"' in command
    assert 'service_tier="default"' in command
    assert all(f"features.{name}=false" in command for name in DISABLED_FEATURES)
    for name in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
                 "OPENAI_BASE_URL", "CODEX_HOME", "WSLENV", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(name, "fictional-secret")
        assert name not in child_environment()


def fake_rpc():
    rpc = AppServer.__new__(AppServer)
    rpc.sequence, rpc.timeout = 0, 1
    rpc.notifications, rpc.inbox, rpc.sent = [], queue.Queue(), []
    rpc.send = rpc.sent.append
    return rpc


def test_jsonrpc_retains_early_notifications_and_rejects_server_tool_requests():
    rpc = fake_rpc()
    notification = {"method": "item/completed", "params": {"item": {"text": "early"}}}
    rpc.inbox.put(notification)
    rpc.inbox.put({"id": 9, "method": "item/commandExecution/requestApproval", "params": {}})
    rpc.inbox.put({"id": 1, "result": {"ready": True}})
    assert rpc.request("test", {}) == {"ready": True}
    assert rpc.notifications == [notification]
    assert rpc.sent[-1]["id"] == 9 and "error" in rpc.sent[-1]


def test_jsonrpc_disconnect_and_deadline():
    rpc = fake_rpc()
    rpc.inbox.put(None)
    with pytest.raises(RuntimeError, match="disconnected"):
        rpc.receive(time.monotonic() + 1)
    with pytest.raises(TimeoutError):
        rpc.receive(time.monotonic())


@pytest.mark.parametrize("account_type,overrides", [
    ("apiKey", {}), ("chatgpt", {"model_provider": "other"}),
    ("chatgpt", {"mcp_servers": {"unsafe": {"enabled": True}}}),
    ("chatgpt", {"openai_base_url": "https://example.invalid"}),
    ("chatgpt", {"chatgpt_base_url": "https://example.invalid"}),
    ("chatgpt", {"features": {"shell_tool": True}}),
])
def test_preflight_rejects_wrong_auth_provider_or_active_tools(account_type, overrides):
    rpc = fake_rpc()
    config = {"features": {name: False for name in DISABLED_FEATURES}, **overrides}
    rpc.request = lambda method, _: ({"account": {"type": account_type}} if method == "account/read"
                                     else {"config": config})
    with pytest.raises(ValueError):
        rpc.preflight()


def test_reply_uses_final_message_and_matching_turn_only():
    rpc = fake_rpc()
    rpc.ensure_included_usage = lambda: None
    rpc.request = lambda *_: {"turn": {"id": "turn1"}}
    for thread, turn, text, phase in [
        ("other-thread", "turn1", "wrong", None),
        ("session1", "other-turn", "wrong", None),
        ("session1", "turn1", "progress", "commentary"),
        ("session1", "turn1", "Final answer", "final_answer"),
    ]:
        rpc.inbox.put({"method": "item/completed", "params": {
            "threadId": thread, "turnId": turn,
            "item": {"type": "agentMessage", "phase": phase, "text": text}}})
    rpc.inbox.put({"method": "turn/completed", "params": {
        "threadId": "session1", "turn": {"id": "turn1", "status": "completed"}}})
    assert rpc.reply("session1", "request") == "Final answer"


@pytest.mark.parametrize("credits,windows,allowed", [
    ({"hasCredits": False, "unlimited": False}, {"usedPercent": 9}, True),
    ({"hasCredits": True, "unlimited": False}, {"usedPercent": 9}, False),
    ({"hasCredits": False, "unlimited": False}, {"usedPercent": 100}, False),
    ({}, {"usedPercent": 9}, False),
    ({"hasCredits": False, "unlimited": False}, None, False),
])
def test_paid_credit_or_unknown_usage_state_stops_turn(credits, windows, allowed):
    rpc = fake_rpc()
    rpc.request = lambda method, _: ({"account": {"type": "chatgpt"}} if method == "account/read"
                                     else {"rateLimits": {"credits": credits, "primary": windows}})
    if allowed:
        rpc.ensure_included_usage()
    else:
        with pytest.raises(ValueError):
            rpc.ensure_included_usage()


@pytest.mark.parametrize("recoverable,close_fails", [(False, False), (True, False), (False, True)])
def test_live_worker_reports_timeout_in_slack_and_releases_lock(tmp_path, config, monkeypatch, recoverable, close_fails):
    """The reported failure: an accepted mention times out and Slack gets silence."""
    import dataclasses
    from codex_slack_bridge.jobs import slack_bridge as job
    slack_sdk = pytest.importorskip("slack_sdk")
    socket_mode = pytest.importorskip("slack_sdk.socket_mode")
    from types import SimpleNamespace
    from codex_slack_bridge.services.codex_rpc import TurnTimedOut
    import threading
    stopped = threading.Event()
    monkeypatch.setattr(job, "threading", SimpleNamespace(Event=lambda: stopped, Thread=threading.Thread))

    config = dataclasses.replace(config, included_usage_verified=True)
    monkeypatch.setattr(BridgeConfig, "load", lambda _: config)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fictional")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-fictional")
    monkeypatch.setattr(job, "server_command", lambda *_: [])
    posts = []

    class Web:
        def __init__(self, **_): pass
        def auth_test(self): return {"team_id": "T1", "user_id": "B1"}
        def chat_postMessage(self, **kwargs):
            posts.append(kwargs)
            if "Recovered answer" in kwargs.get("markdown_text", kwargs.get("text", "")):
                stopped.set()

    class Engine(Runner):
        closed = False
        def __init__(self, *_, **__): super().__init__()
        def preflight(self): return {}
        def reply(self, *_):
            if len(self.sessions) == 1:
                error = TurnTimedOut if recoverable else TimeoutError
                raise error("private provider details")
            return "Recovered answer"
        def close(self): Engine.closed = True

    class Socket:
        closes = 0
        def __init__(self, **_): self.socket_mode_request_listeners = []
        def connect(self):
            from types import SimpleNamespace
            request = SimpleNamespace(type="events_api", payload=event(), envelope_id="envelope")
            self.socket_mode_request_listeners[0](self, request)
            if recoverable:
                request.payload = event("101.001")
                self.socket_mode_request_listeners[0](self, request)
        def send_socket_mode_response(self, *_): pass
        def close(self):
            Socket.closes += 1
            if close_fails:
                raise AttributeError("simulated SDK cleanup race")

    monkeypatch.setattr(slack_sdk, "WebClient", Web)
    monkeypatch.setattr(socket_mode, "SocketModeClient", Socket)
    monkeypatch.setattr(job, "AppServer", Engine)
    with pytest.raises((RuntimeError, AttributeError)):
        job.run(tmp_path / "unused.toml")
    assert posts, "An accepted request failed without any Slack reply"
    assert all("markdown_text" in p and "text" not in p and "blocks" not in p for p in posts)
    assert any("couldn't finish" in p.get("markdown_text", p.get("text", "")) for p in posts)
    assert all("private provider" not in p.get("markdown_text", p.get("text", "")) for p in posts)
    assert Engine.closed
    assert Socket.closes == 1
    if recoverable:
        assert any("Recovered answer" in p.get("markdown_text", p.get("text", "")) for p in posts)
    assert not (config.state_dir / "runner.lock").exists()


def test_turn_deadline_interrupts_only_matching_turn_and_confirms_stop():
    from codex_slack_bridge.services.codex_rpc import TurnTimedOut
    rpc = fake_rpc()
    rpc.turn_timeout = 0
    rpc.ensure_included_usage = lambda: None
    requests = []
    def request(method, params):
        requests.append((method, params))
        if method == "turn/start":
            return {"turn": {"id": "t1"}}
        rpc.inbox.put({"method": "turn/completed", "params": {
            "threadId": "other", "turn": {"id": "t1", "status": "interrupted"}}})
        rpc.inbox.put({"method": "turn/completed", "params": {
            "threadId": "s1", "turn": {"id": "t1", "status": "interrupted"}}})
        return {}
    rpc.request = request
    with pytest.raises(TurnTimedOut):
        rpc.reply("s1", "request")
    assert requests[-1] == ("turn/interrupt", {"threadId": "s1", "turnId": "t1"})


def test_channel_expansion_keeps_sessions_separate_and_user_scope(store, config):
    from dataclasses import replace
    config = replace(config, channels=frozenset({"C1", "C2"}))
    assert store.accept(config, event(), "B1")
    assert store.accept(config, event(channel="C2"), "B1")
    assert not store.accept(config, event(channel="C3"), "B1")
    assert not store.accept(config, event(channel="C2", user="stranger"), "B1")
    runner, posts = Runner(), []
    while process_one(store, config, runner, lambda *a: posts.append(a)):
        pass
    assert runner.sessions == ["codex-1", "codex-2"]
    assert {p[0] for p in posts} == {"C1", "C2"}


def test_native_slack_markdown_preserves_code_links_and_tables_and_long_replies():
    from codex_slack_bridge.services.slack_messages import message_body
    answer = "**Shared models** [core](https://example.com/core)\n\n`a_b`\n\n| A | B |\n|---|---|\n| x | y |"
    payload = message_body("Fictional owner", answer)
    assert payload == {"markdown_text": "Fictional owner — discussion pilot\n\n" + answer}
    # Boundary counts the owner header too; oversized output remains complete,
    # explicitly plain, and uses one post so delivery recovery stays unambiguous.
    prefix = "Fictional owner — discussion pilot\n\n"
    assert "markdown_text" in message_body("Fictional owner", "x"*(12000-len(prefix)))
    long_answer = "x"*35000
    payload = message_body("Fictional owner", long_answer)
    assert payload["mrkdwn"] is False
    assert payload["text"].endswith(long_answer)
    assert payload["text"].startswith("Long reply (plain text):")
