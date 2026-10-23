# Extraction provenance

- Date: 2026-10-23.
- Source: Form2Agent local Slack pilot, issue 185.
- Source checkout HEAD: `459af94ee58bfa6f4eb9286e2bd7e706ad7ed620`; bridge modules and tests were untracked local additions.
- Included: four service modules, two job modules, two focused test modules,
  sanitized Slack manifest and adapted operational documentation.
- Adaptations: standalone Python namespace/CLI/build metadata, Bash launcher,
  generic example configuration and bot display name.
- Preserved: routing/storage semantics, account/tool guards, original Form2Agent
  GitHub reader and MCP identifiers at extraction time. This is not a live-runner migration.
- Excluded: credentials, local configuration/state, logs, authentication files,
  application code, private Slack messages and PowerShell credential storage.

## Configurable repository update

- The standalone worker now requires `--repository owner/repo` in the private
  bridge TOML's `github_mcp_command`; no repository is implicitly selected.
- Repository validation, scoped endpoints/search/results, default-branch
  discovery and worker-command-sensitive session profiles replace the fixed scope.
- Existing live pilot and its state were not migrated or restarted.
