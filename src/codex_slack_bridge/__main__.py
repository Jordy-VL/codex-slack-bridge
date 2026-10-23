"""Standalone bridge command."""
import argparse
from pathlib import Path
from codex_slack_bridge.jobs.slack_bridge import run, inspect

def main():
    parser = argparse.ArgumentParser(description="Local Slack to Codex discussion bridge")
    parser.add_argument("--config", type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--skip", type=int)
    args = parser.parse_args()
    if args.status or args.skip is not None:
        print(inspect(args.config, skip=args.skip))
    else:
        result = run(args.config, check=args.check)
        if result is not None:
            print(result)

if __name__ == "__main__":
    main()
