# Quick Task 1 Summary: Implement crs-copilot-cli

## What was done

Implemented `crs-copilot-cli` — a complete CRS that uses GitHub Copilot CLI for autonomous vulnerability patching, following the identical architecture pattern from `crs-claude-code` and `crs-codex`.

## Files created

### Core project files
- **`patcher.py`** — Patcher module: scans POVs, reproduces crashes, delegates to agent. Default agent: `copilot_cli`. Registers `~/.copilot` as shared dir for log collection.
- **`pyproject.toml`** — Package config with `run_patcher` entry point.
- **`.gitignore`** / **`.dockerignore`** — Standard patterns matching reference repos.
- **`.claude/settings.local.json`** — Dev-time Claude Code permissions (for working on the repo).

### Agent files
- **`agents/copilot_cli.py`** — Copilot CLI agent implementation:
  - `setup()`: Configures `GH_TOKEN`/`GITHUB_TOKEN`, writes `config.json` to `~/.copilot/` for LiteLLM proxy routing, sets global gitignore for `AGENTS.md`.
  - `run()`: Writes crash logs + AGENTS.md template, invokes `copilot -p <prompt> --yolo` non-interactively, handles timeout/kill, checks for patch output.
  - Model: `COPILOT_MODEL` env var, default `claude-sonnet-4-5-20250929`.
- **`agents/copilot_cli.md`** — AGENTS.md template with libCRS tool docs (identical format to codex.md).
- **`agents/template.py`** — Standard agent interface stub.
- **`agents/__init__.py`** — Empty package marker.

### Infrastructure files
- **`oss-crs/crs.yaml`** — CRS metadata: name, registry, build phases, run phases, supported targets, required LLMs.
- **`oss-crs/base.Dockerfile`** — Ubuntu 22.04 + Python 3.12 + Docker CLI + Node.js 20 + `@github/copilot@0.0.411`.
- **`oss-crs/builder.Dockerfile`** — Build phase image with libCRS + compile_target.
- **`oss-crs/patcher.Dockerfile`** — Run phase image from `copilot-cli-base` with libCRS + patcher package.
- **`oss-crs/docker-bake.hcl`** — Docker Bake config targeting `copilot-cli-base`.
- **`oss-crs/example-compose.yaml`** — Example crs-compose config with model env vars.
- **`oss-crs/sample-litellm-config.yaml`** — LiteLLM proxy config for both Anthropic and OpenAI models.
- **`bin/compile_target`** — Builder phase script (identical to references).
- **`README.md`** — Full documentation with architecture, setup, configuration, and agent docs.

## Key differences from reference repos

| Aspect | crs-claude-code | crs-codex | crs-copilot-cli |
|--------|----------------|-----------|-----------------|
| CLI tool | `claude` | `codex` | `copilot` |
| npm package | `@anthropic-ai/claude-code@2.1.42` | `@openai/codex@0.104.0` | `@github/copilot@0.0.411` |
| Non-interactive flag | `-p` + stdin | `exec --json` | `-p <prompt> --yolo` |
| Instruction file | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` |
| Model env var | `ANTHROPIC_MODEL` | `CODEX_MODEL` | `COPILOT_MODEL` |
| Default model | `claude-sonnet-4-5-20250929` | `gpt-5.2-codex` | `claude-sonnet-4-5-20250929` |
| Config dir | `~/.claude` | `~/.codex` | `~/.copilot` |
| Shared dir name | `claude-logs` | `codex-home` | `copilot-home` |
| Auth env vars | `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` | `OSS_CRS_LLM_API_KEY` (via config.toml) | `GH_TOKEN` + `GITHUB_TOKEN` |
