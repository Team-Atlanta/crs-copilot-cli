# Quick Task Plan: Implement crs-copilot-cli

## Overview

Implement `crs-copilot-cli` — a CRS that uses [GitHub Copilot CLI](https://github.com/github/copilot-cli) for autonomous vulnerability patching. Follows the identical architecture pattern established by `crs-claude-code` and `crs-codex`.

### Key Copilot CLI Details
- **Package**: `@github/copilot` (npm), pinned to `v0.0.411`
- **CLI command**: `copilot`
- **Non-interactive mode**: `copilot -p <prompt> --yolo`
- **Default model**: Claude Sonnet 4.5 (configurable via `COPILOT_MODEL` env var)
- **Auth**: `GH_TOKEN` or `GITHUB_TOKEN` env vars
- **Config directory**: `~/.copilot/`
- **Instruction file**: `AGENTS.md` (same as Codex — both use this convention)

## Task 1: Create core project files

**Files**: `patcher.py`, `pyproject.toml`, `.gitignore`, `.dockerignore`, `.claude/settings.local.json`

**Action**:
- Copy `patcher.py` from crs-codex, change:
  - Default `CRS_AGENT` to `copilot_cli`
  - Agent log directory: register `~/.copilot` as shared dir named `copilot-home`
  - Pass `copilot_home` in agent config dict
- Create `pyproject.toml` with name `crs-copilot-cli`
- Create `.gitignore` and `.dockerignore` matching the pattern
- Create `.claude/settings.local.json` with basic permissions

## Task 2: Create agent files

**Files**: `agents/__init__.py`, `agents/template.py`, `agents/copilot_cli.py`, `agents/copilot_cli.md`

**Action**:
- `__init__.py`: empty (same as references)
- `template.py`: identical to references (standard agent interface)
- `copilot_cli.py`: Implement setup() and run() for GitHub Copilot CLI:
  - **setup()**: Configure `GH_TOKEN`/`GITHUB_TOKEN` from LLM API key, set `COPILOT_MODEL` env var, write global gitignore for `AGENTS.md`, handle `copilot_home` directory
  - **run()**: Write crash logs, generate AGENTS.md from template, invoke `copilot -p <prompt> --yolo` with `cwd=source_dir`, handle timeout/kill, check for patches
  - Model: read from `COPILOT_MODEL` env var, default `claude-sonnet-4-5-20250929`
- `copilot_cli.md`: AGENTS.md template (identical to codex.md — same format)

## Task 3: Create oss-crs infrastructure files

**Files**: `oss-crs/crs.yaml`, `oss-crs/base.Dockerfile`, `oss-crs/builder.Dockerfile`, `oss-crs/patcher.Dockerfile`, `oss-crs/docker-bake.hcl`, `oss-crs/example-compose.yaml`, `oss-crs/sample-litellm-config.yaml`, `bin/compile_target`, `README.md`

**Action**:
- `crs.yaml`: name `crs-copilot-cli`, registry `ghcr.io/team-atlanta/crs-copilot-cli`, default agent `copilot_cli`, required LLMs include claude-sonnet-4-5-20250929 and other supported models
- `base.Dockerfile`: Ubuntu 22.04 + Python 3.12 + Docker CLI + Node.js 20 + `npm install -g @github/copilot@0.0.411`
- `builder.Dockerfile`: identical to references
- `patcher.Dockerfile`: FROM `copilot-cli-base`, install libCRS + crs-copilot-cli package
- `docker-bake.hcl`: target `copilot-cli-base`
- `example-compose.yaml`: crs-copilot-cli config with COPILOT_MODEL
- `sample-litellm-config.yaml`: Anthropic model entries (Copilot CLI uses Claude by default through GitHub's proxy, but via LiteLLM for CRS)
- `bin/compile_target`: identical to references
- `README.md`: comprehensive documentation following the pattern
