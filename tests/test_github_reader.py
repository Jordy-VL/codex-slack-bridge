"""Fictional GitHub responses verify scope, read-only requests and MCP boundaries."""

import base64
import json
from types import SimpleNamespace

import pytest

from codex_slack_bridge.jobs.github_mcp import respond
from codex_slack_bridge.services.codex_rpc import AppServer, DISABLED_FEATURES, GITHUB_SERVER, server_command
from codex_slack_bridge.services.github_reader import GitHubReader, TOOL

REPOSITORY = "example-org/example.repo"
ROOT = f"repos/{REPOSITORY}"


class Reader(GitHubReader):
    def __init__(self, responses, repository=REPOSITORY):
        super().__init__(repository)
        self._default_branch = "main"
        self.responses, self.calls = responses, []

    def get(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return self.responses.pop(0)


def test_file_read_pins_branch_to_commit_and_returns_exact_line_numbers():
    reader = Reader([{"sha": "a" * 40}, {"type": "file", "encoding": "base64", "size": 30,
                                         "sha": "blob1", "content": base64.b64encode(
                                             b"first\nsecond\nthird\nfourth\n").decode()}])
    result = reader.read({"operation": "read_file", "path": "backend/example.py", "ref": "main",
                          "start_line": 2, "max_lines": 2})
    assert result["data"]["content"] == "2: second\n3: third"
    assert result["data"]["has_more"] is True
    assert result["source"] == f"https://github.com/{REPOSITORY}/blob/{'a'*40}/backend/example.py"
    assert reader.calls == [(f"{ROOT}/commits/main", {}),
                            (f"{ROOT}/contents/backend/example.py", {"ref": "a"*40})]


@pytest.mark.parametrize("arguments", [
    {"operation": "read_file", "path": "../private"},
    {"operation": "read_file", "path": "/etc/passwd"},
    {"operation": "read_file", "path": "a/../secret"},
    {"operation": "read_file", "path": "a\\secret"},
    {"operation": "read_file", "path": "%2e%2e/other"},
    {"operation": "read_file", "path": "a", "ref": "main?repo=other"},
    {"operation": "issue", "number": "1/../../other"},
    {"operation": "issue", "number": True},
    {"operation": "issue", "number": -1},
    {"operation": "activity", "repository": "elsewhere/private"},
    {"operation": "dispatch_workflow"},
    {"operation": "search_code", "query": "repo:elsewhere/private"},
    {"operation": "search_code", "query": 'x" OR repo:elsewhere/private'},
    {"operation": "search_code", "query": ""},
    {"operation": "activity", "page": 101},
])
def test_rejects_paths_writes_cross_repo_and_query_escape_before_network(arguments):
    reader = Reader([])
    with pytest.raises(ValueError):
        reader.read(arguments)
    assert reader.calls == []


def test_search_rejects_unexpected_repository_and_sends_scoped_phrase():
    reader = Reader([{"items": [{"repository": {"full_name": "other/private"}}]}])
    with pytest.raises(ValueError, match="unexpected repository"):
        reader.read({"operation": "search_code", "query": "sandbox_mode"})
    assert reader.calls == [("search/code", {"q": f'"sandbox_mode" repo:{REPOSITORY}',
                                             "page": 1, "per_page": 20})]


def test_pr_detail_and_inline_comments_use_fixed_repo_and_pagination():
    reader = Reader([{"number": 42, "title": "Fictional change", "head": {"sha": "f"*40},
                      "base": {"ref": "main"}, "merged": False},
                     [{"body": "Please preserve the old value", "path": "example.py", "line": 4}]])
    pr = reader.read({"operation": "pull_request", "number": 42})
    comment = reader.read({"operation": "pr_comments", "number": 42, "page": 2})
    assert pr["data"]["head_sha"] == "f"*40
    assert comment["data"]["items"][0]["line"] == 4
    assert reader.calls[-1] == (f"{ROOT}/pulls/42/comments", {"page": 2, "per_page": 20})


@pytest.mark.parametrize("file", [
    {"type": "symlink"}, {"type": "submodule"},
    {"type": "file", "encoding": "base64", "size": 1000001},
    {"type": "file", "encoding": "base64", "size": 2, "content": "AA=="},
])
def test_unsupported_binary_symlink_large_content_is_explicit(file):
    reader = Reader([{"sha": "a"*40}, file])
    with pytest.raises(ValueError):
        reader.read({"operation": "read_file", "path": "example"})


def test_output_is_bounded_and_marks_truncation():
    reader = Reader([[{"body": "x"*15000, "number": n} for n in range(20)]])
    result = reader.read({"operation": "issue_comments", "number": 42})
    assert result["output_truncated"] is True
    assert len(json.dumps(result)) < 32000


def test_activity_keeps_compact_metadata_when_issue_bodies_are_large():
    issue = {"number": 42, "title": "Fictional long discussion", "body": "x"*20000}
    reader = Reader([[{"sha": "a"*40, "commit": {"message": "Fictional commit\n" + "x"*20000}}],
                     [issue]*12, [issue]*12, {"workflow_runs": []}])
    result = reader.read({"operation": "activity"})
    assert result["data"]["commits"][0]["message"] == "Fictional commit"
    assert "body" not in result["data"]["open_prs"][0]
    assert len(json.dumps(result)) < 5000


def test_gh_transport_uses_get_fixed_hostname_and_no_shell_or_error_bodies(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"number":42}')

    monkeypatch.setattr("codex_slack_bridge.services.github_reader.subprocess.run", run)
    reader = GitHubReader(REPOSITORY, gh="/fictional/gh")
    reader.read({"operation": "issue", "number": 42})
    command, kwargs = calls[0]
    assert command[:7] == ["/fictional/gh", "api", "--hostname", "github.com", "--method", "GET",
                           f"{ROOT}/issues/42"]
    assert kwargs.get("shell", False) is False
    with pytest.raises(ValueError):
        reader.get("repos/other/private/issues")
    monkeypatch.setattr("codex_slack_bridge.services.github_reader.subprocess.run", lambda *_, **__: SimpleNamespace(
        returncode=1, stdout="", stderr="fictional-sensitive-response"))
    with pytest.raises(RuntimeError) as exc:
        reader.read({"operation": "issue", "number": 42})
    assert "sensitive" not in str(exc.value)


def test_mcp_has_one_read_tool_and_refuses_unknown_tools_methods():
    assert respond({"method": "notifications/initialized"}, Reader([])) is None
    tools = respond({"id": 1, "method": "tools/list"}, Reader([]))["result"]["tools"]
    assert [tool["name"] for tool in tools] == [TOOL["name"]]
    assert tools[0]["annotations"]["readOnlyHint"] is True
    result = respond({"id": 2, "method": "tools/call", "params": {"name": "shell"}}, Reader([]))
    assert result["result"]["isError"] is True
    assert respond({"id": 3, "method": "write"}, Reader([]))["error"]["code"] == -32601


def test_only_exact_github_worker_is_allowed_by_effective_config(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[mcp_servers.other]\ncommand="unapproved"\n')
    worker = ("wsl.exe", "-d", "Ubuntu", "--", "python", "-m", "codex_slack_bridge.jobs.github_mcp")
    command = server_command("codex", p, worker)
    assert "mcp_servers.other.enabled=false" in command
    assert f"mcp_servers.{GITHUB_SERVER}.enabled=true" in command
    rpc = AppServer.__new__(AppServer)
    rpc.github_command = worker
    config = {"features": {**{name: False for name in DISABLED_FEATURES}, "code_mode_host": True}, "mcp_servers": {
        GITHUB_SERVER: {"command": worker[0], "args": list(worker[1:]),
                        "enabled_tools": [TOOL["name"]], "enabled": True}}}
    rpc.request = lambda method, _: ({"account": {"type": "chatgpt"}} if method == "account/read"
                                     else {"config": config})
    assert rpc.preflight()["tools"] == GITHUB_SERVER
    config["mcp_servers"]["other"] = {"enabled": True}
    with pytest.raises(ValueError):
        rpc.preflight()
    del config["mcp_servers"]["other"]
    config["mcp_servers"][GITHUB_SERVER]["args"] = ["unapproved-worker"]
    with pytest.raises(ValueError):
        rpc.preflight()


def test_tree_filters_and_pages_at_one_commit_without_directory_walk():
    tree = {"truncated": False, "tree": [{"path": "other/file", "type": "blob"}] + [
        {"path": f"backend/file{n:03}.py", "type": "blob"} for n in range(205)]}
    reader = Reader([{"sha": "a"*40}, tree])
    result = reader.read({"operation": "tree", "path": "backend", "page": 2})
    assert result["data"]["entries"] == [{"path": f"backend/file{n:03}.py", "type": "blob"}
                                          for n in range(200, 205)]
    assert result["data"]["total_entries"] == 205
    assert result["data"]["has_more"] is False
    assert reader.calls == [(f"{ROOT}/commits/main", {}),
                            (f"{ROOT}/git/trees/{'a'*40}", {"recursive": 1})]


def test_immutable_file_ref_needs_no_repeated_commit_resolution():
    reader = Reader([{"type": "file", "encoding": "base64", "size": 2,
                      "content": base64.b64encode(b"ok").decode()}])
    assert reader.read({"operation": "read_file", "path": "README.md", "ref": "a"*40})["data"]["content"] == "1: ok"
    assert len(reader.calls) == 1


@pytest.mark.parametrize("repository", ["", None, "../private", "owner/..", "owner/.",
    "https://github.com/owner/repo", "owner/repo/other", "owner/repo?x=1",
    "owner/repo#ref", "owner/%2e%2e", "owner/repo\\other", "owner/repo\n"])
def test_repository_configuration_rejects_scope_escape(repository):
    with pytest.raises(ValueError, match="owner/repository"):
        GitHubReader(repository)


def test_two_readers_keep_independent_scopes_and_source_links():
    for repository in ("alpha/project-one", "beta/project.two"):
        reader = Reader([{"number": 8}, {"items": []}], repository)
        result = reader.read({"operation": "issue", "number": 8})
        assert result["repository"] == repository
        assert result["source"] == f"https://github.com/{repository}/issues/8"
        reader.read({"operation": "search_issues", "query": "bug"})
        assert reader.calls[0][0] == f"repos/{repository}/issues/8"
        assert reader.calls[1][1]["q"] == f'"bug" repo:{repository}'
        tool = respond({"id": 1, "method": "tools/list"}, reader)["result"]["tools"][0]
        assert repository in tool["description"]


def test_default_branch_is_discovered_and_cached_instead_of_assuming_main():
    reader = Reader([{"default_branch": "develop"}, {"check_runs": []}, {"check_runs": []}])
    reader._default_branch = None
    reader.read({"operation": "checks"})
    reader.read({"operation": "checks"})
    assert [call[0] for call in reader.calls] == [ROOT, f"{ROOT}/commits/develop/check-runs",
                                               f"{ROOT}/commits/develop/check-runs"]


def test_changing_repository_changes_session_tool_profile():
    from codex_slack_bridge.services.codex_rpc import github_tool_profile
    first = ("python", "-m", "codex_slack_bridge.jobs.github_mcp", "--repository", "alpha/one")
    second = (*first[:-1], "beta/two")
    assert github_tool_profile(first) != github_tool_profile(second)
    assert github_tool_profile(first) == github_tool_profile(tuple(first))
    assert github_tool_profile(()) == "discussion"


def test_worker_requires_explicit_repository_without_network():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-m", "codex_slack_bridge.jobs.github_mcp"],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "--repository" in result.stderr


@pytest.mark.parametrize("repository", ["alpha/repo-one", "beta/repo.two"])
def test_configured_transport_cannot_read_another_repository(repository, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='{"number": 3}')
    monkeypatch.setattr("codex_slack_bridge.services.github_reader.subprocess.run", run)
    reader = GitHubReader(repository, gh="/fake/gh")
    reader.read({"operation": "issue", "number": 3})
    assert calls[0][2:7] == ["--hostname", "github.com", "--method", "GET",
                             f"repos/{repository}/issues/3"]
    for endpoint in ["repos/other/private/issues/3", f"repos/{repository}-other/issues/3",
                     "https://other.example/repos/private"]:
        with pytest.raises(ValueError, match="outside"):
            reader.get(endpoint)
    assert len(calls) == 1


def test_search_accepts_only_the_configured_repository_case_insensitively():
    reader = Reader([{"items": [{"repository": {"full_name": "ALPHA/PROJECT"}, "path": "README.md"}]}],
                    "alpha/project")
    result = reader.read({"operation": "search_code", "query": "hello"})
    assert result["data"]["items"][0]["path"] == "README.md"
    assert result["repository"] == "alpha/project"
