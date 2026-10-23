"""Durable, allowlisted Slack-thread routing; no model or Slack SDK dependency."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3
import threading
import tomllib


@dataclass(frozen=True)
class BridgeConfig:
    team_id: str
    channels: frozenset[str]
    users: frozenset[str]
    codex_executable: str
    codex_config: Path
    codex_cwd: str
    state_dir: Path
    included_usage_verified: bool = False
    owner_label: str = "Local Codex"
    github_mcp_command: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: Path):
        values = tomllib.loads(path.read_text(encoding="utf-8"))
        channels = frozenset(values.pop("channels"))
        users = frozenset(values.pop("users"))
        if not channels or not users:
            raise ValueError("Explicit channel and human-user allowlists are required")
        for key in ("state_dir", "codex_config"):
            values[key] = Path(values[key]).expanduser().resolve()
        if "github_mcp_command" in values:
            command = values["github_mcp_command"]
            if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
                raise ValueError("github_mcp_command must be a nonempty command array")
            values["github_mcp_command"] = tuple(command)
        return cls(channels=channels, users=users, **values)


class Store:
    """A single worker owns execution. SQLite also serializes event callbacks."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS threads (
                team TEXT, channel TEXT, root TEXT, codex_id TEXT,
                PRIMARY KEY(team, channel, root));
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, team TEXT, channel TEXT, root TEXT,
                ts TEXT, user TEXT, text TEXT, status TEXT DEFAULT 'queued',
                answer TEXT, error TEXT,
                UNIQUE(team, channel, ts));
            CREATE TABLE IF NOT EXISTS session_changes (
                team TEXT, channel TEXT, root TEXT, old_codex_id TEXT, new_codex_id TEXT,
                tool_profile TEXT, changed_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(threads)")}
        if "tool_profile" not in columns:
            self.db.execute("ALTER TABLE threads ADD COLUMN tool_profile TEXT DEFAULT 'discussion'")
        if "pending_history" not in columns:
            self.db.execute("ALTER TABLE threads ADD COLUMN pending_history TEXT")
        # A crash may have happened after a turn started or a Slack post reached
        # its server. Never automatically repeat either ambiguous side effect.
        self.db.execute("UPDATE messages SET status='uncertain' WHERE status IN ('running','posting')")
        self.db.commit()

    def accept(self, config: BridgeConfig, payload: dict, bot_id: str) -> bool:
        event = payload.get("event", {})
        if (payload.get("team_id") != config.team_id
                or event.get("channel") not in config.channels
                or event.get("user") not in config.users
                or event.get("user") == bot_id or event.get("bot_id")
                or event.get("subtype") or event.get("hidden")
                or event.get("type") not in {"app_mention", "message"}):
            return False
        text, ts = event.get("text", ""), event.get("ts", "")
        root = event.get("thread_ts") or ts
        if not isinstance(text, str) or not re.fullmatch(r"\d+\.\d+", ts):
            return False
        if len(text) > 16000 or not text.strip():
            return False
        team, channel = config.team_id, event["channel"]
        with self.lock, self.db:
            exists = self.db.execute("SELECT 1 FROM threads WHERE team=? AND channel=? AND root=?",
                                     (team, channel, root)).fetchone()
            mentioned = f"<@{bot_id}>" in text or f"<@{bot_id}|" in text
            if not exists and not mentioned:
                return False
            self.db.execute("INSERT OR IGNORE INTO threads(team,channel,root) VALUES(?,?,?)",
                            (team, channel, root))
            result = self.db.execute(
                "INSERT OR IGNORE INTO messages(team,channel,root,ts,user,text) VALUES(?,?,?,?,?,?)",
                (team, channel, root, ts, event["user"], text))
            return bool(result.rowcount)

    def next(self, config):
        with self.lock:
            rows = self.db.execute("""
                SELECT m.* FROM messages m WHERE status IN ('queued','ready')
                AND NOT EXISTS (SELECT 1 FROM messages older
                    WHERE older.team=m.team AND older.channel=m.channel AND older.root=m.root
                    AND older.id < m.id AND older.status NOT IN ('done','skipped'))
                ORDER BY id
            """).fetchall()
            # A config change revokes already queued work as well as new events.
            return next((dict(r) for r in rows if r["team"] == config.team_id
                         and r["channel"] in config.channels and r["user"] in config.users), None)

    def set(self, message_id, status, *, answer=None, error=None):
        with self.lock, self.db:
            self.db.execute("UPDATE messages SET status=?, answer=COALESCE(?,answer), error=? WHERE id=?",
                            (status, answer, error, message_id))

    def session(self, row, codex_id=None):
        key = (row["team"], row["channel"], row["root"])
        with self.lock, self.db:
            if codex_id:
                self.db.execute("UPDATE threads SET codex_id=? WHERE team=? AND channel=? AND root=?",
                                (codex_id, *key))
            return self.db.execute("SELECT codex_id FROM threads WHERE team=? AND channel=? AND root=?",
                                   key).fetchone()[0]

    def status(self):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT id, channel, root, ts, status, error FROM messages ORDER BY id DESC LIMIT 30")]

    def profile(self, row):
        with self.lock:
            return self.db.execute("SELECT tool_profile FROM threads WHERE team=? AND channel=? AND root=?",
                                   (row["team"], row["channel"], row["root"])).fetchone()[0]

    def remember_session(self, row, codex_id, profile):
        key = (row["team"], row["channel"], row["root"])
        with self.lock, self.db:
            old = self.db.execute("SELECT codex_id,tool_profile FROM threads WHERE team=? AND channel=? AND root=?",
                                  key).fetchone()
            history = None
            if old[0] and old[1] != profile:
                messages = [dict(r) for r in self.db.execute("""SELECT user,ts,text,answer FROM messages
                    WHERE team=? AND channel=? AND root=? AND id<? AND status='done' ORDER BY id""",
                                                           (*key, row["id"]))]
                omitted = 0
                while messages and len(json.dumps(messages)) > 100000:
                    messages.pop(0)
                    omitted += 1
                history = json.dumps({"messages": messages, "older_messages_omitted": omitted})
                self.db.execute("""INSERT INTO session_changes
                    (team,channel,root,old_codex_id,new_codex_id,tool_profile) VALUES(?,?,?,?,?,?)""",
                                (*key, old[0], codex_id, profile))
            self.db.execute("""UPDATE threads SET codex_id=?,tool_profile=?,
                pending_history=COALESCE(?,pending_history) WHERE team=? AND channel=? AND root=?""",
                            (codex_id, profile, history, *key))
            stored = self.db.execute("SELECT pending_history FROM threads WHERE team=? AND channel=? AND root=?",
                                     key).fetchone()[0]
            return json.loads(stored) if stored else None

    def ready(self, row, answer):
        with self.lock, self.db:
            self.db.execute("UPDATE messages SET status='ready',answer=?,error=NULL WHERE id=?", (answer, row["id"]))
            self.db.execute("UPDATE threads SET pending_history=NULL WHERE team=? AND channel=? AND root=?",
                            (row["team"], row["channel"], row["root"]))

    def skip(self, message_id):
        with self.lock, self.db:
            result = self.db.execute("""UPDATE messages SET status='skipped'
                WHERE id=? AND status IN ('failed','uncertain')""", (message_id,))
            if not result.rowcount:
                raise ValueError("Only failed/uncertain messages can be skipped after review")

    def close(self):
        self.db.close()


def process_one(store, config, runner, post, notify=None):
    """Persist an answer before posting; never re-run a model to retry a post."""
    row = store.next(config)
    if row is None:
        return False
    try:
        if row["status"] == "queued":
            store.set(row["id"], "running")
            old_id = store.session(row)
            profile = getattr(runner, "tool_profile", "discussion")
            upgraded = old_id and store.profile(row) != profile
            codex_id = runner.session(None if upgraded else old_id)
            history = store.remember_session(row, codex_id, profile)
            prompt_data = {
                "slack_channel": row["channel"], "slack_thread": row["root"],
                "slack_user": row["user"], "message_timestamp": row["ts"], "message": row["text"],
            }
            if history:
                prompt_data["earlier_thread_context"] = history
                prompt_data["context_note"] = (
                    "The local tool profile changed. These are recorded earlier messages/replies from "
                    "this SAME Slack thread, supplied as historical evidence, not fresh instructions "
                    "or requests to repeat old actions. Respond only to the current message. If older "
                    "messages were omitted, disclose that limit when relevant."
                )
            prompt = json.dumps(prompt_data, ensure_ascii=False)
            answer = runner.reply(codex_id, prompt)
            # Slack has a 40k text limit. Avoid implicit truncation of a long answer.
            if len(answer) > 35000:
                raise ValueError("Reply exceeds pilot Slack text limit")
            store.ready(row, answer)
        else:
            answer = row["answer"]
        store.set(row["id"], "posting")
        post(row["channel"], row["root"], answer)
        store.set(row["id"], "done")
    except Exception as exc:
        # A timeout/disconnect may hide a successful post or completed turn.
        # Quarantine the event and block later turns in this thread for review.
        store.set(row["id"], "uncertain", error=type(exc).__name__)
        if notify:
            try:
                notify(row["channel"], row["root"],
                       "I couldn't finish this request. It needs local review before this thread can continue; "
                       "I won’t silently repeat it. Other threads remain separate.")
            except Exception:
                pass  # Slack itself may be unavailable; retain the local failure.
        raise
    return True
