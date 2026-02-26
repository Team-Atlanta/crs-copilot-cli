# crs-copilot-cli

A [CRS](https://github.com/oss-crs) (Cyber Reasoning System) that uses [GitHub Copilot CLI](https://github.com/github/copilot-cli) to autonomously find and patch vulnerabilities in open-source projects.

Given proof-of-vulnerability (POV) inputs that crash a target binary, the agent analyzes the crashes, edits source code, builds, tests, iterates, and submits a verified patch — all autonomously.

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│ patcher.py (orchestrator)                                           │
│                                                                     │
│  1. Fetch POVs & source         2. Reproduce crashes                │
│     crs.fetch(POV)                 libCRS run-pov (build-id: base)  │
│     crs.download(src)              → crash_log_*.txt                │
│         │                                │                          │
│         ▼                                ▼                          │
│  3. Launch Copilot CLI agent with crash logs + AGENTS.md            │
│     copilot --autopilot -p <prompt> --model <model> --yolo          │
└─────────┬───────────────────────────────────────────────────────────┘
          │ -p: prompt with crash log paths
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Copilot CLI (autonomous agent)                                      │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐                   │
│  │ Analyze  │───▶│   Fix    │───▶│   Verify     │                   │
│  │          │    │          │    │              │                   │
│  │ Read     │    │ Edit src │    │ apply-patch  │──▶ Builder        │
│  │ crash    │    │ git diff │    │   -build     │    sidecar        │
│  │ logs     │    │          │    │              │◀── build_id       │
│  └──────────┘    └──────────┘    │ run-pov ────│──▶ Builder        │
│                                  │   (all POVs)│◀── pov_exit_code  │
│                       ▲          │ run-test ───│──▶ Builder        │
│                       │          │             │◀── test_exit_code  │
│                       │          └──────┬───────┘                   │
│                       │                 │                           │
│                       └── retry ◀── fail?                           │
│                                         │ pass                      │
│                                         ▼                           │
│                              Write .diff to /patches/               │
└─────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────┐
│ Submission daemon        │
│ watches /patches/ ──────▶ oss-crs framework (auto-submit)
└─────────────────────────┘
```

1. **`run_patcher`** fetches POVs and source, reproduces all crashes against the unpatched binary via the builder sidecar.
2. All POVs are batched as variants of the same vulnerability and handed to **Copilot CLI** in a single session with crash logs and `AGENTS.md` instructions.
3. The agent autonomously analyzes crash logs, edits source, and uses **libCRS** tools (`apply-patch-build`, `run-pov`, `run-test`) to build and test patches through the builder sidecar — iterating until all POV variants pass.
4. A verified `.diff` is written to `/patches/`, where a daemon auto-submits it to the oss-crs framework.

The agent is language-agnostic — it edits source and generates diffs while the builder sidecar handles compilation. The sanitizer type (`address` or `undefined` in this CRS) is passed to the agent for context.

## Project structure

```
patcher.py             # Patcher module: scan POVs → agent
pyproject.toml         # Package config (run_patcher entry point)
bin/
  compile_target       # Builder phase: compiles the target project
agents/
  copilot_cli.py       # Copilot CLI agent (default)
  copilot_cli.md       # AGENTS.md template with libCRS tool docs
  template.py          # Stub for creating new agents
oss-crs/
  crs.yaml             # CRS metadata (supported languages, models, etc.)
  example-compose.yaml # Example crs-compose configuration
  base.Dockerfile      # Base image: Ubuntu + Node.js + Copilot CLI + Python
  builder.Dockerfile   # Build phase image
  patcher.Dockerfile   # Run phase image
  docker-bake.hcl      # Docker Bake config for the base image
  sample-litellm-config.yaml  # LiteLLM proxy config template
```

## Prerequisites

- **[oss-crs](https://github.com/oss-crs/oss-crs)** — the CRS framework (`crs-compose` CLI)

Builder sidecars for incremental builds are declared in `oss-crs/crs.yaml` (`snapshot: true` / `run_snapshot: true`) and handled automatically by the framework — no separate builder setup is needed.

## Quick start

### 1. Configure `crs-compose.yaml`

Copy `oss-crs/example-compose.yaml` and update the paths:

```yaml
crs-copilot-cli:
  source:
    local_path: /path/to/crs-copilot-cli
  cpuset: "2-7"
  memory: "16G"
  llm_budget: 10
  additional_env:
    CRS_AGENT: copilot_cli
    COPILOT_MODEL: gpt-5.3-codex
    COPILOT_GITHUB_TOKEN: ${COPILOT_GITHUB_TOKEN}

llm_config:
  litellm_config: /path/to/sample-litellm-config.yaml
```

### 2. Configure LiteLLM

Copy `oss-crs/sample-litellm-config.yaml` and set your API credentials. The LiteLLM config is provided for forward-compatibility (see [LLM endpoint limitation](#llm-endpoint-limitation)). Copilot CLI currently uses GitHub's hosted API, so a valid Copilot subscription is required.

### 3. Run with oss-crs

```bash
crs-compose up -f crs-compose.yaml
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `CRS_AGENT` | `copilot_cli` | Agent module name (maps to `agents/<name>.py`) |
| `COPILOT_MODEL` | `gpt-5.3-codex` | Model used by Copilot CLI (simplified IDs, see list below) |
| `COPILOT_GITHUB_TOKEN` | unset | GitHub token used for Copilot CLI authentication (recommended) |
| `COPILOT_SUBSCRIPTION_TOKEN` | unset | Compatibility alias for `COPILOT_GITHUB_TOKEN` |
| `AGENT_TIMEOUT` | `0` (no limit) | Agent timeout in seconds (0 = run until budget exhausted) |
| `BUILDER_MODULE` | `inc-builder-asan` | Builder sidecar module name (must match a `run_snapshot` entry in crs.yaml) |

Copilot CLI uses simplified model IDs (not dated snapshots). Unlike Claude Code which has multiple model env vars (`ANTHROPIC_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, etc.), Copilot CLI uses a single `COPILOT_MODEL` env var.

Available models:

**Anthropic:**
- `claude-sonnet-4.5`
- `claude-sonnet-4`
- `claude-sonnet-4.6`
- `claude-haiku-4.5`
- `claude-opus-4.1`
- `claude-opus-4.5`
- `claude-opus-4.6`

**OpenAI:**
- `gpt-5`
- `gpt-5-codex`
- `gpt-5.1`
- `gpt-5.1-codex`
- `gpt-5.1-codex-mini`
- `gpt-5.1-codex-max`
- `gpt-5.2`
- `gpt-5.2-codex`
- `gpt-5.3-codex` (default)

**Google:**
- `gemini-2.5-pro`
- `gemini-3-flash`
- `gemini-3-pro`

## Runtime behavior

- **Execution**: `copilot --autopilot -p <prompt> --model <model> --yolo` (non-interactive, full permissions)
- **Instruction file**: `AGENTS.md` generated per run in the target repo
- **Config directory**: `~/.copilot/` (default `/root/.copilot/`)

Debug artifacts:
- Shared directory: `/root/.copilot` (registered as `copilot-home`)
- Per-run logs: `/work/agent/copilot_stdout.log`, `/work/agent/copilot_stderr.log`

## LLM endpoint limitation

**Copilot CLI does not currently support custom LLM endpoints.** Unlike Claude Code (`ANTHROPIC_BASE_URL`) and Codex (`config.toml` model providers), Copilot CLI routes all API calls through GitHub's infrastructure. There is no documented mechanism to redirect requests to a LiteLLM proxy.

- GitHub's enterprise [BYOK](https://github.com/orgs/community/discussions/179954) (Bring Your Own Key) feature works in VS Code, JetBrains, Eclipse, and Xcode — but **not** the CLI.
- Open feature requests: [#973](https://github.com/github/copilot-cli/issues/973), [#1170](https://github.com/github/copilot-cli/issues/1170)

**What this means for CRS integration:**

The oss-crs framework provides LLM access via `OSS_CRS_LLM_API_URL` / `OSS_CRS_LLM_API_KEY` (a LiteLLM proxy). This CRS writes those values to `config.json` as a best-effort attempt, but Copilot CLI currently ignores them. Instead, Copilot CLI authenticates with a GitHub token and uses GitHub's hosted Copilot API.

Token precedence in this CRS agent:
- `COPILOT_GITHUB_TOKEN` (recommended explicit env var)
- `COPILOT_SUBSCRIPTION_TOKEN` (compatibility alias)
- `OSS_CRS_LLM_API_KEY` (backward-compatible fallback)

This means:
- LLM budget enforcement via LiteLLM is **not** applied — usage is governed by the GitHub Copilot subscription.
- The `sample-litellm-config.yaml` and `required_llms` in `crs.yaml` are provided for forward-compatibility when BYOK support reaches the CLI.
- A valid GitHub Copilot subscription (Pro, Business, or Enterprise) with CLI access is required.

## Patch validity

A patch is submitted only when it meets all criteria:

1. **Builds** — compiles successfully
2. **POVs don't crash** — all POV variants pass
3. **Tests pass** — project test suite passes (or skipped if none exists)
4. **Semantically correct** — fixes the root cause with a minimal patch

Submission is final once a `.diff` is written to `/patches/` and picked up by the watcher. Submitted patches cannot be edited or resubmitted, so complete a full pre-submit review first.

## Adding a new agent

1. Copy `agents/template.py` to `agents/my_agent.py`.
2. Implement `setup()` and `run()`.
3. Set `CRS_AGENT=my_agent`.

The agent receives:
- **source_dir** — clean git repo of the target project
- **povs** — list of `(pov_path, crash_log)` tuples (variants of the same bug)
- **harness** — harness name for `run-pov`
- **patches_dir** — write verified `.diff` files here
- **work_dir** — scratch space
- **language** — target language (c, c++, jvm)
- **sanitizer** — sanitizer type (`address` or `undefined`)
- **builder** — builder sidecar module name (keyword-only, required)
- **ref_diff** — reference diff showing the bug-introducing change (delta mode only, None in full mode)

The agent has access to three libCRS commands (the `--builder` flag specifies which builder sidecar module to use):
- `libCRS apply-patch-build <patch.diff> <response_dir> --builder <module>` — build a patch
- `libCRS run-pov <pov> <response_dir> --harness <h> --build-id <id> --builder <module>` — test against a POV
- `libCRS run-test <response_dir> --build-id <id> --builder <module>` — run the project's test suite

For transparent diagnostics, always inspect response_dir logs:
- Build: `build.log`, `build_stdout.log`, `build_stderr.log`
- POV: `pov_stdout.log`, `pov_stderr.log`
- Test: `test_stdout.log`, `test_stderr.log`
