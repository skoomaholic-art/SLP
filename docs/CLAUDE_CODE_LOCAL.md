# Claude Code for SLP

SLP uses the official Claude Code CLI together with plugin sources from `skoomaholic-art/claude-code`. The fork is tooling, not a second application runtime; no credentials are stored in either repository.

## Why not use the fork's devcontainer unchanged?

The fork's README installs Claude Code as a separate CLI. Its devcontainer also installs `@anthropic-ai/claude-code` and then enables a restrictive outbound firewall. That sandbox is useful for generic coding, but it intentionally blocks arbitrary external hosts. SLP must reach Qazsport, Sport+, VseTV, Championat and other live sources, so live-source verification should run in the normal SLP Codespace/workspace instead.

## One-time CLI setup

Recommended native install for Linux/Codespaces:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude --version
claude doctor
```

Authenticate interactively:

```bash
claude auth login
claude auth status --text
```

Direct Claude Code access requires an eligible Claude/Console account. Google Cloud can be configured later as a Claude provider if desired.

## Start Claude for SLP

From the SLP repository root:

```bash
bash scripts/claude_slp.sh
```

The launcher clones/updates `skoomaholic-art/claude-code` under the user's cache and loads these plugins from that exact repository:

- `feature-dev`
- `code-review`
- `pr-review-toolkit`
- `security-guidance`
- `commit-commands`
- `ralph-wiggum`

It starts Claude in an isolated git worktree and uses `auto` permission mode by default. It never uses `bypassPermissions`. Override the permission mode with `SLP_CLAUDE_PERMISSION_MODE=default` when you want every sensitive action confirmed.

The security plugin's regex warnings and commit-time review stay enabled locally, but its extra LLM review after every turn is disabled by default to avoid unnecessary usage. Set `ENABLE_STOP_REVIEW=1` before launching if you explicitly want that additional review layer. In GitHub Actions, the plugin runs only its low-cost pattern layer by default.

`CLAUDE.md` and `AGENTS.md` remain the authoritative SLP operating rules.

## Useful commands

For a complex feature:

```text
/feature-dev <specific feature and acceptance criteria>
```

For review:

```text
/code-review
/pr-review-toolkit:review-pr all
```

For a tightly specified iterative bugfix with automatic verification:

```text
/ralph-loop "<one task, tests, completion criteria>" --max-iterations 10 --completion-promise "COMPLETE"
```

Always cap Ralph iterations. Do not use Ralph for ambiguous product decisions or production deployment.

## Parallel/background agents

Current Claude Code versions expose an agent view for background sessions:

```bash
claude agents --cwd "$(pwd)" --permission-mode auto
```

Use parallel agents for separate investigations or reviews, not for two agents editing the same P0 simultaneously. The one-writer rule in `AGENTS.md` still applies.

## GitHub agent mode

SLP also contains `.github/workflows/claude-code.yml`. Once the Claude GitHub App and `CLAUDE_CODE_OAUTH_TOKEN` repository secret are configured, an issue or PR comment containing `@claude` can assign work remotely. The workflow registers `https://github.com/skoomaholic-art/claude-code.git` as a Claude plugin marketplace through the action's native `plugin_marketplaces` input and installs selected plugins from its `claude-code-plugins` catalog. Claude may implement and push a branch/PR, but it must not merge its own PR or release Railway production.
