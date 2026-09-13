# Claude Code repository toolkit for SLP

SLP uses only the repository content from `skoomaholic-art/claude-code` as a development toolkit. We do not install or invoke the official Claude Code CLI, GitHub Action, OAuth flow, or Anthropic runtime.

The repository is connected to SLP as a git submodule at `tools/claude-code`. This keeps the exact upstream/fork contents available inside the project without copying or modifying them.

## What is actually usable without the Claude runtime

The repository contains plugin definitions, agent prompts, commands, hooks, marketplace metadata, examples, mods, and devcontainer assets. These files can be read and used as development playbooks by the existing SLP engineering workflow.

Useful sources include:
- `tools/claude-code/plugins/feature-dev/`
- `tools/claude-code/plugins/code-review/`
- `tools/claude-code/plugins/pr-review-toolkit/`
- `tools/claude-code/plugins/security-guidance/`
- `tools/claude-code/plugins/commit-commands/`
- `tools/claude-code/plugins/ralph-wiggum/`

Without the external Claude Code engine these plugins do not execute themselves. Treat them as source-controlled agent/workflow specifications, not as a running AI service.

## Clone/update

Clone SLP with the toolkit:

```bash
git clone --recurse-submodules https://github.com/skoomaholic-art/SLP.git
```

For an existing clone:

```bash
git submodule update --init --recursive
```

To move the toolkit to a newer commit from the fork, update the submodule pointer in a normal SLP branch/PR and run the regular regression checks before merging.

## SLP rule

Do not add an official Claude CLI/action dependency unless the project owner explicitly changes this decision. `CLAUDE.md` and `AGENTS.md` remain the SLP operating contract; the repository toolkit is supplemental reference material only.
