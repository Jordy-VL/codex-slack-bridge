"""Private newline-delimited MCP worker for the fixed read-only GitHub adapter."""

import argparse
import json
import sys

from codex_slack_bridge.services.github_reader import GitHubReader, TOOL


def respond(message, reader):
    request_id = message.get("id")
    if request_id is None:
        return None
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        version = params.get("protocolVersion")
        if version not in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}:
            version = "2025-06-18"
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "form2agent_github_readonly", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [reader.tool]}
    elif method == "tools/call":
        try:
            if params.get("name") != TOOL["name"]:
                raise ValueError("Unknown tool")
            data = reader.read(params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                      "isError": False}
        except Exception as exc:
            safe = str(exc) if isinstance(exc, ValueError) else "GitHub read failed; check local access and limits"
            result = {"content": [{"type": "text", "text": safe}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": "Method not available"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main():
    parser = argparse.ArgumentParser(description="Read-only MCP worker scoped to one GitHub repository")
    parser.add_argument("--repository", required=True, help="Approved github.com owner/repository")
    args = parser.parse_args()
    reader = GitHubReader(args.repository)
    for line in sys.stdin:
        try:
            message = json.loads(line)
            result = respond(message, reader)
        except (ValueError, TypeError, AttributeError):
            result = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid request"}}
        if result is not None:
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
