# crs-copilot-cli

A [CRS](https://github.com/oss-crs) (Cyber Reasoning System) that uses [GitHub Copilot CLI](https://github.com/github/copilot-cli) to autonomously find and patch vulnerabilities in open-source projects.

Given any boot-time subset of vulnerability evidence (POVs, bug-candidate reports, diff files, and/or seeds), the agent analyzes the inputs, edits source code, builds, tests, iterates, and writes one final patch for submission.

## How it works

```
┌─────────────────────────────────────────────────────────────────────┐
│ patcher.py (orchestrator)                                           │
│                                                                     │
│  1. Fetch startup inputs & source                                    │
│     crs.fetch(POV/BUG_CANDIDATE/DIFF/SEED)                           │
│     crs.download(src)                                                │
│         │                                                            │
│         ▼                                                            │
│  2. Launch Copilot CLI agent with fetched paths + AGENTS.md          │
│     copilot --autopilot -p <prompt> --model <model> --yolo          │
└─────────┬───────────────────────────────────────────────────────────┘
          │ -p: prompt with startup evidence paths
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Copilot CLI (autonomous agent)                                      │
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐                   │
│  │ Analyze  │───▶│   Fix    │───▶│   Verify     │                   │
│  │          │    │          │    │              │                   │
│  │ Read     │    │ Edit src │    │ apply-patch  │──▶ Builder        │
│  │ startup  │    │ git diff │    │   -build     │    sidecar        │
│  │ evidence │    │          │    │              │◀── build_id       │
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
│ patcher.py               │
│ submit(first patch) ───▶ oss-crs framework
└─────────────────────────┘
```

1. **`run_patcher`** fetches available startup inputs (`POV`, `BUG_CANDIDATE`, `DIFF`, `SEED`) once, downloads source, and passes the fetched paths to the agent.
2. The evidence is handed to **Copilot CLI** in a single session with generated `AGENTS.md` instructions. No additional inputs are fetched after startup.
3. The agent autonomously analyzes evidence, edits source, and uses **libCRS** tools (`apply-patch-build`, `run-pov`, `run-test`) to iterate through the builder sidecar.
4. When the first final `.diff` is written to `/patches/`, the patcher submits that single file with `crs.submit(DataType.PATCH, patch_path)` and exits. Later patch files or modifications are ignored.

The agent is language-agnostic — it edits source and generates diffs while the builder sidecar handles compilation. The sanitizer type (`address` only in this CRS) is passed to the agent for context.

## Project structure

```
patcher.py             # Patcher module: one-time fetch of optional inputs → agent → first-patch submit
pyproject.toml         # Package config (run_patcher entry point)
bin/
  compile_target       # Builder phase: compiles the target project
agents/
  copilot_cli.py       # Copilot CLI agent (default)
  copilot_cli.md       # AGENTS.md template with libCRS tool docs
  sections/            # Dynamic AGENTS.md section partial templates
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
  # Optional: uncomment only if you explicitly want OSS-CRS to inject
  # an external LiteLLM endpoint.
  # litellm:
  #   mode: external
  #   external:
  #     url_env: EXTERNAL_LITELLM_API_BASE
  #     key_env: EXTERNAL_LITELLM_API_KEY
```

### 2. Optional LiteLLM setup

By default, Copilot CLI should use `COPILOT_GITHUB_TOKEN` and GitHub-hosted inference directly. If you explicitly want OSS-CRS to inject an external LiteLLM endpoint, uncomment the `llm_config` block and make sure `EXTERNAL_LITELLM_API_BASE` and `EXTERNAL_LITELLM_API_KEY` are set. `oss-crs/sample-litellm-config.yaml` remains available as a reference template.

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
| `BUILDER_MODULE` | `inc-builder` | Builder sidecar module name (must match a `run_snapshot` entry in crs.yaml) |
| `OSS_CRS_SNAPSHOT_IMAGE` | framework-provided | Required snapshot image reference used by patcher startup checks |

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
- Log directory: `/root/.copilot` (registered via `register-log-dir`)
- Per-run logs: `/work/agent/copilot_stdout.log`, `/work/agent/copilot_stderr.log`

## LLM endpoint limitation

**Copilot CLI does not currently support custom LLM endpoints.** Unlike Claude Code (`ANTHROPIC_BASE_URL`) and Codex (`config.toml` model providers), Copilot CLI routes all API calls through GitHub's infrastructure. There is no documented mechanism to redirect requests to a LiteLLM proxy.

- GitHub's enterprise [BYOK](https://github.com/orgs/community/discussions/179954) (Bring Your Own Key) feature works in VS Code, JetBrains, Eclipse, and Xcode — but **not** the CLI.
- Open feature requests: [#973](https://github.com/github/copilot-cli/issues/973), [#1170](https://github.com/github/copilot-cli/issues/1170)

**What this means for CRS integration:**

The oss-crs framework may provide `OSS_CRS_LLM_API_URL` / `OSS_CRS_LLM_API_KEY` (LiteLLM), but Copilot CLI does not consume them. This CRS only writes model selection to `~/.copilot/config.json` and authenticates with GitHub token env vars.

Token precedence in this CRS agent:
- `COPILOT_GITHUB_TOKEN` (recommended explicit env var)
- `COPILOT_SUBSCRIPTION_TOKEN` (compatibility alias)

This means:
- LLM budget enforcement via LiteLLM is **not** applied — usage is governed by the GitHub Copilot subscription.
- The `sample-litellm-config.yaml` and `required_llms` in `crs.yaml` are provided for forward-compatibility when BYOK support reaches the CLI.
- A valid GitHub Copilot subscription (Pro, Business, or Enterprise) with CLI access is required.

## Patch submission

The agent is instructed to satisfy these criteria before writing a patch:

1. **Builds** — compiles successfully
2. **POVs don't crash** — all provided POV variants pass (if POVs were provided)
3. **Tests pass** — project test suite passes (or skipped if none exists)
4. **Semantically correct** — fixes the root cause with a minimal patch

Runtime remains trust-based: the patcher does not re-run final verification. Once the first `.diff` is written to `/patches/`, the patcher submits that single file and exits. Submitted patches cannot be edited or resubmitted, so the agent should only write to `/patches/` when it considers the patch final.

## Adding a new agent

1. Copy `agents/template.py` to `agents/my_agent.py`.
2. Implement `setup()` and `run()`.
3. Set `CRS_AGENT=my_agent`.

The agent receives:
- **setup(source_dir, config)** config keys:
  - `copilot_github_token` — GitHub token for Copilot CLI auth
  - `copilot_subscription_token` — compatibility alias
  - `copilot_home` — path for Copilot CLI state/logs
- **source_dir** — clean git repo of the target project
- **pov_dir** — boot-time POV input directory (may be empty)
- **bug_candidate_dir** — boot-time bug-candidate directory (may be empty)
- **diff_dir** — boot-time diff directory (may be empty)
- **seed_dir** — boot-time seed directory (may be empty)
- **harness** — harness name for `run-pov`
- **patches_dir** — write exactly one final `.diff` here
- **work_dir** — scratch space
- **language** — target language (c, c++, jvm)
- **sanitizer** — sanitizer type (`address` only)
- **builder** — builder sidecar module name (keyword-only, required)

All optional inputs are boot-time only. The patcher fetches them once and passes directory paths to the agent; no new POVs, bug-candidates, diff files, or seeds appear during the run.

The agent has access to three libCRS commands (the `--builder` flag specifies which builder sidecar module to use):
- `libCRS apply-patch-build <patch.diff> <response_dir> --builder <module>` — build a patch
- `libCRS run-pov <pov> <response_dir> --harness <h> --build-id <id> --builder <module>` — test against a POV
- `libCRS run-test <response_dir> --build-id <id> --builder <module>` — run the project's test suite

For transparent diagnostics, always inspect response_dir logs:
- Build: `build.log`, `build_stdout.log`, `build_stderr.log`
- POV: `pov_stdout.log`, `pov_stderr.log`
- Test: `test_stdout.log`, `test_stderr.log`
