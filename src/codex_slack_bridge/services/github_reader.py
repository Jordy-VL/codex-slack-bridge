"""Read-only GitHub views scoped to one repository; credentials stay with gh."""

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import quote, urlencode

OPERATIONS = (
    "activity", "tree", "read_file", "list_files", "search_code", "search_issues",
    "issue", "issue_comments", "pull_request", "pr_files", "pr_reviews", "pr_comments", "checks",
)
TOOL = {
    "name": "form2agent_github_read",
    "description": (
        "Read live GitHub data only from the operator-configured repository. activity shows recent commits, "
        "issues, PRs and workflow runs. tree lists repository paths recursively (optional path prefix, page); "
        "use it first to find unfamiliar files without repeated directory reads. read_file/list_files accept path and ref (default repository default branch); "
        "read_file supports start_line/max_lines. search_code/search_issues accept plain query text. "
        "issue, issue_comments (also PR discussion), pull_request, pr_files, pr_reviews, pr_comments "
        "(inline PR comments) require number. checks accepts "
        "ref. page selects the next page of comments/files/search. Results include source links "
        "and retrieval time. This cannot read unpushed local changes or perform writes."
    ),
    "inputSchema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "operation": {"type": "string", "enum": list(OPERATIONS)},
            "path": {"type": "string"}, "ref": {"type": "string"},
            "query": {"type": "string"}, "number": {"type": "integer", "minimum": 1},
            "page": {"type": "integer", "minimum": 1, "maximum": 100},
            "start_line": {"type": "integer", "minimum": 1},
            "max_lines": {"type": "integer", "minimum": 1, "maximum": 250},
        }, "required": ["operation"],
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
}


def repository_name(value):
    """Accept one github.com owner/repository slug, never URLs or traversal."""
    if (not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}", value)
            or value.split("/")[1] in {".", ".."}):
        raise ValueError("Repository must be a github.com owner/repository slug")
    return value


def integer(value, name, maximum=100000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} is outside the supported integer range")
    return value


def repo_path(value, *, empty=False):
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("Invalid repository path")
    if value == "" and empty:
        return ""
    if (not value or "\\" in value or "%" in value or any(ord(c) < 32 for c in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Use a relative repository path without traversal")
    return quote(value, safe="/")


def git_ref(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", value)
            or ".." in value or "//" in value):
        raise ValueError("Invalid branch, tag or commit ref")
    return value


def search_text(value, repository):
    if not isinstance(value, str) or not re.fullmatch(r"[\w /.,-]{1,160}", value) or not value.strip():
        raise ValueError("Use plain search words without qualifiers, URLs or operators")
    return f'"{value.strip()}" repo:{repository}'


def brief(item, *, include_body=True):
    keys = ("number", "title", "state", "html_url", "updated_at", "merged_at", "draft", "sha",
            "name", "status", "conclusion", "head_sha", "head_branch", "path", "type", "size",
            "filename", "additions", "deletions", "changes", "review_state", "submitted_at",
            "line", "original_line", "in_reply_to_id")
    result = {k: item[k] for k in keys if k in item}
    if "user" in item:
        result["author"] = (item["user"] or {}).get("login")
    if "body" in item and include_body:
        body = item.get("body") or ""
        result.update(body=body[:12000], body_truncated=len(body) > 12000)
    if "commit" in item:
        result["message"] = item["commit"].get("message", "").split("\n", 1)[0][:500]
    if "patch" in item:
        patch = item.get("patch") or ""
        result.update(patch=patch[:12000], patch_truncated=len(patch) > 12000)
    if "pull_request" in item:
        result["kind"] = "pull_request"
    return result


class GitHubReader:
    def __init__(self, repository, gh=None):
        self.repository = repository_name(repository)
        self.root = f"repos/{self.repository}"
        self.web = f"https://github.com/{self.repository}"
        self._default_branch = None
        self.gh = gh or shutil.which("gh") or str(Path.home() / ".local/bin/gh")

    @property
    def tool(self):
        return {**TOOL, "description": f"Approved repository: {self.repository}. " + TOOL["description"]}

    def get(self, endpoint, **params):
        """Only internal fixed endpoint builders may call this method."""
        if not (endpoint == self.root or endpoint.startswith(self.root + "/")
                or endpoint in {"search/code", "search/issues"}):
            raise ValueError("Endpoint is outside the approved repository")
        if params:
            endpoint += "?" + urlencode(params)
        response = subprocess.run(
            [self.gh, "api", "--hostname", "github.com", "--method", "GET", endpoint,
             "--header", "Accept: application/vnd.github+json"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if response.returncode:
            # gh errors may contain request bodies or credential/config details.
            raise RuntimeError("GitHub read failed; check access, resource existence and rate limits locally")
        return json.loads(response.stdout)

    def read(self, arguments):
        if not isinstance(arguments, dict) or set(arguments) - set(TOOL["inputSchema"]["properties"]):
            raise ValueError("Unsupported GitHub tool arguments")
        operation = arguments.get("operation")
        if operation not in OPERATIONS:
            raise ValueError("Unsupported GitHub operation; only read views are exposed")
        page = integer(arguments.get("page", 1), "page", 100)
        ref = git_ref(arguments["ref"]) if "ref" in arguments else None
        if ref is None and operation in {"tree", "read_file", "list_files", "checks"}:
            if self._default_branch is None:
                self._default_branch = git_ref(self.get(self.root)["default_branch"])
            ref = self._default_branch
        source = self.web
        if operation == "activity":
            data = {
                "commits": [brief(x) for x in self.get(f"{self.root}/commits", per_page=8)],
                "updated_issues_and_prs": [brief(x, include_body=False) for x in self.get(
                    f"{self.root}/issues", state="all", sort="updated", direction="desc", per_page=12)],
                "open_prs": [brief(x, include_body=False) for x in self.get(f"{self.root}/pulls", state="open", per_page=12)],
                "workflow_runs": [brief(x) for x in self.get(
                    f"{self.root}/actions/runs", per_page=8).get("workflow_runs", [])],
                "scope": "Recent bounded activity, not a complete repository history",
            }
        elif operation == "tree":
            prefix = arguments.get("path", "")
            repo_path(prefix, empty=True)
            commit = ref if re.fullmatch(r"[0-9a-f]{40}", ref) else self.get(
                f"{self.root}/commits/{quote(ref, safe='')}")["sha"]
            tree = self.get(f"{self.root}/git/trees/{commit}", recursive=1)
            entries = sorted((x for x in tree.get("tree", []) if not prefix or x["path"] == prefix
                              or x["path"].startswith(prefix + "/")), key=lambda x: x["path"])
            start = (page - 1) * 200
            data = {"commit": commit, "entries": [{"path": x["path"], "type": x["type"]}
                                                    for x in entries[start:start+200]],
                    "page": page, "has_more": start + 200 < len(entries),
                    "total_entries": len(entries), "github_tree_truncated": tree.get("truncated", False)}
            source = f"{self.web}/tree/{commit}"
        elif operation in {"read_file", "list_files"}:
            path = repo_path(arguments.get("path", ""), empty=operation == "list_files")
            commit = ref if re.fullmatch(r"[0-9a-f]{40}", ref) else self.get(
                f"{self.root}/commits/{quote(ref, safe='')}")["sha"]
            file = self.get(f"{self.root}/contents/{path}", ref=commit)
            source = f"{self.web}/{'tree' if operation == 'list_files' else 'blob'}/{commit}/{path}"
            if operation == "list_files":
                if not isinstance(file, list):
                    raise ValueError("list_files requires a directory path")
                data = {"commit": commit, "entries": [brief(x) for x in file[:250]],
                        "entries_truncated": len(file) > 250}
            else:
                if (not isinstance(file, dict) or file.get("type") != "file"
                        or file.get("encoding") != "base64" or file.get("size", 1000001) > 1000000):
                    raise ValueError("Only regular UTF-8 text files up to 1 MB are supported")
                raw = base64.b64decode(file["content"])
                if b"\x00" in raw:
                    raise ValueError("Binary files are unsupported")
                try:
                    lines = raw.decode("utf-8").splitlines()
                except UnicodeDecodeError as exc:
                    raise ValueError("Only UTF-8 text files are supported") from exc
                start = integer(arguments.get("start_line", 1), "start_line")
                count = integer(arguments.get("max_lines", 120), "max_lines", 250)
                data = {"commit": commit, "blob_sha": file.get("sha"), "path": arguments["path"],
                        "total_lines": len(lines), "start_line": start,
                        "has_more": start - 1 + count < len(lines),
                        "content": "\n".join(f"{i}: {line}" for i, line in
                                             enumerate(lines[start-1:start-1+count], start))}
        elif operation in {"search_code", "search_issues"}:
            query = search_text(arguments.get("query"), self.repository)
            kind = "code" if operation == "search_code" else "issues"
            found = self.get(f"search/{kind}", q=query, page=page, per_page=20)
            items = []
            for item in found.get("items", []):
                repo = ((item.get("repository") or {}).get("full_name") or
                        item.get("repository_url", "").removeprefix("https://api.github.com/repos/"))
                if repo.casefold() != self.repository.casefold():
                    raise ValueError("GitHub search returned an unexpected repository")
                items.append(brief(item, include_body=False))
            data = {"items": items, "total_count": found.get("total_count"), "page": page,
                    "per_page": 20, "incomplete_results": found.get("incomplete_results", False)}
        elif operation == "checks":
            source = f"{self.web}/commit/{quote(ref, safe='')}"
            checks = self.get(f"{self.root}/commits/{quote(ref, safe='')}/check-runs", page=page, per_page=20)
            data = {"checks": [brief(x) for x in checks.get("check_runs", [])],
                    "total_count": checks.get("total_count"), "page": page}
        else:
            number = integer(arguments.get("number"), "number")
            kind = "issues" if operation in {"issue", "issue_comments"} else "pulls"
            endpoint = f"{self.root}/{kind}/{number}"
            source = f"{self.web}/{'issues' if kind == 'issues' else 'pull'}/{number}"
            suffix = {"issue_comments": "comments", "pr_files": "files", "pr_reviews": "reviews",
                      "pr_comments": "comments"}.get(operation)
            if suffix:
                items = self.get(f"{endpoint}/{suffix}", page=page, per_page=20)
                data = {"items": [brief(x) for x in items], "page": page, "per_page": 20,
                        "has_more": len(items) == 20}
            else:
                item = self.get(endpoint)
                data = brief(item)
                if operation == "pull_request":
                    data.update(head_sha=item.get("head", {}).get("sha"),
                                base_ref=item.get("base", {}).get("ref"), merged=item.get("merged"),
                                mergeable_state=item.get("mergeable_state"))
        result = {"repository": self.repository, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                  "source": source, "data": data}
        rendered = json.dumps(result, ensure_ascii=False)
        if len(rendered) > 30000:
            result = {"repository": self.repository, "source": source, "retrieved_at": result["retrieved_at"],
                      "output_truncated": True, "data_preview": rendered[:28000]}
        return result
