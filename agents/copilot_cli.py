"""
GitHub Copilot CLI agent for autonomous vulnerability patching.

Implements the agent interface (setup / run) using GitHub Copilot CLI
in non-interactive mode. Copilot CLI reads AGENTS.md for workflow
instructions, then autonomously: analyzes the crash -> edits source ->
builds via libCRS -> tests via libCRS -> iterates -> writes final .diff
to patches_dir.
"""

import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("agent.copilot_cli")

_raw_model = os.environ.get("COPILOT_MODEL", "gpt-5.3-codex").strip()
COPILOT_MODEL = _raw_model.removeprefix("anthropic/").removeprefix("openai/").removeprefix("google/")
COPILOT_GITHUB_TOKEN = os.environ.get("COPILOT_GITHUB_TOKEN", "").strip()
COPILOT_SUBSCRIPTION_TOKEN = os.environ.get("COPILOT_SUBSCRIPTION_TOKEN", "").strip()

# 0 = no timeout (run until budget is exhausted)
try:
    AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", "0"))
except ValueError:
    AGENT_TIMEOUT = 0

_TEMPLATE_PATH = Path(__file__).with_suffix(".md")
AGENTS_MD_TEMPLATE = _TEMPLATE_PATH.read_text()


def setup(source_dir: Path, config: dict) -> None:
    """One-time agent configuration.

    - Sets Copilot CLI env vars (COPILOT_MODEL, auth token)
    - Writes copilot config.json for non-interactive autonomous mode
    - Writes AGENTS.md into source_dir with libCRS tool docs + workflow
    """
    llm_api_url = config.get("llm_api_url", "")
    llm_api_key = config.get("llm_api_key", "")
    github_token = config.get("copilot_github_token", COPILOT_GITHUB_TOKEN)
    subscription_token = config.get("copilot_subscription_token", COPILOT_SUBSCRIPTION_TOKEN)
    copilot_home = Path(config.get("copilot_home", Path.home() / ".copilot"))
    copilot_home.mkdir(parents=True, exist_ok=True)

    # CRS patcher container is intentionally permissive (autonomous mode).
    os.environ["IS_SANDBOX"] = "1"

    token_source = None
    auth_token = ""
    if github_token:
        auth_token = github_token
        token_source = "COPILOT_GITHUB_TOKEN"
    elif subscription_token:
        auth_token = subscription_token
        token_source = "COPILOT_SUBSCRIPTION_TOKEN"
    elif llm_api_key:
        # Backward-compat: OSS_CRS_LLM_API_KEY was previously reused as the Copilot token.
        auth_token = llm_api_key
        token_source = "OSS_CRS_LLM_API_KEY"

    if auth_token:
        # Copilot CLI-native auth env var.
        os.environ["COPILOT_GITHUB_TOKEN"] = auth_token
        logger.info("Configured Copilot auth token from %s", token_source)
    else:
        logger.warning(
            "No COPILOT_GITHUB_TOKEN/COPILOT_SUBSCRIPTION_TOKEN set and no OSS_CRS_LLM_API_KEY fallback available. "
            "Copilot CLI authentication may fail unless token env vars are already present."
        )

    if llm_api_url and llm_api_key:
        # NOTE: Copilot CLI does NOT currently support custom LLM endpoints.
        # Unlike Claude Code (ANTHROPIC_BASE_URL) and Codex (config.toml model_providers),
        # Copilot CLI routes all API calls through GitHub's infrastructure and has no
        # documented mechanism to redirect to a LiteLLM proxy.
        # BYOK (Bring Your Own Key) is an enterprise-only feature available in VS Code,
        # JetBrains, Eclipse, and Xcode — but NOT in the CLI.
        # See: https://github.com/github/copilot-cli/issues/973
        #      https://github.com/github/copilot-cli/issues/1170
        #
        # We set the auth tokens and write config.json as a best-effort attempt.
        # If/when Copilot CLI adds BYOK or custom endpoint support, this should
        # start working. Until then, Copilot CLI uses GitHub's hosted API with
        # the user's Copilot subscription.
        logger.warning(
            "Copilot CLI does not currently support custom LLM endpoints. "
            "LiteLLM proxy URL (%s) will be written to config.json but may not be used. "
            "Copilot CLI will route through GitHub's API with the configured auth token.",
            llm_api_url,
        )

    else:
        logger.info(
            "No OSS_CRS_LLM_API_URL/KEY provided. "
            "Copilot CLI will use GitHub's Copilot API with the configured subscription token."
        )

    # Write config.json with model selection.
    # baseUrl is NOT a documented config.json field but is included in case
    # future Copilot CLI versions support it.
    copilot_config = {
        "model": COPILOT_MODEL,
    }
    if llm_api_url:
        copilot_config["baseUrl"] = llm_api_url
    config_path = copilot_home / "config.json"
    config_path.write_text(json.dumps(copilot_config, indent=2))
    config_path.chmod(0o600)
    logger.info("Wrote config.json to %s (model=%s)", config_path, COPILOT_MODEL)

    logger.info("Model: %s", COPILOT_MODEL)

    # Global gitignore so runtime instructions never leak into patches.
    global_gitignore = Path.home() / ".gitignore"
    global_gitignore.write_text("AGENTS.md\n")
    subprocess.run(
        ["git", "config", "--global", "core.excludesFile", str(global_gitignore)],
        capture_output=True,
    )

    logger.info("Agent setup complete")


def run(
    source_dir: Path,
    povs: list[tuple[Path, str]],
    harness: str,
    patches_dir: Path,
    work_dir: Path,
    *,
    language: str = "c",
    sanitizer: str = "address",
    builder: str,
    ref_diff: str | None = None,
) -> bool:
    """Launch Copilot CLI in non-interactive mode to autonomously fix the vulnerability.

    povs is a list of (pov_path, crash_log) tuples — variants of the same bug.
    Writes all crash logs and AGENTS.md (with concrete paths), then sends a prompt.
    Copilot CLI autonomously analyzes, edits, builds, tests, iterates, and
    writes the final .diff to patches_dir.

    Returns True if a patch file was produced in patches_dir.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    # Write each crash log to a file and build POV sections for AGENTS.md
    pov_sections = []
    for i, (pov_path, crash_log) in enumerate(povs):
        crash_log_path = work_dir / f"crash_log_{i}.txt"
        crash_log_path.write_text(crash_log)
        logger.info("Wrote crash log to %s", crash_log_path)

        pov_sections.append(
            f"- POV: `{pov_path}` — crash log: `{crash_log_path}`\n"
            f"  Test: `libCRS run-pov {pov_path} <response_dir> --harness {harness} --build-id <build_id> --builder {builder}`"
        )

    pov_list = "\n".join(pov_sections)

    # Build optional diff section for delta mode
    if ref_diff:
        # Extract changed file list from diff headers
        changed_files = [
            line.split("b/", 1)[1]
            for line in ref_diff.splitlines()
            if line.startswith("+++ b/")
        ]
        changed_files_str = ", ".join(f"`{f}`" for f in changed_files) if changed_files else "(see diff)"
        diff_section = (
            "\n## Reference Diff (Delta Mode)\n\n"
            "This diff shows the change that introduced the vulnerability.\n"
            "Fix the flaw in this change — don't blindly revert it.\n\n"
            f"Changed files: {changed_files_str}\n\n"
            f"```diff\n{ref_diff}\n```\n"
        )
    else:
        diff_section = ""

    # Write AGENTS.md with concrete paths for all POVs.
    agents_md = AGENTS_MD_TEMPLATE.format(
        language=language,
        sanitizer=sanitizer,
        work_dir=work_dir,
        harness=harness,
        patches_dir=patches_dir,
        pov_list=pov_list,
        pov_count=len(povs),
        builder=builder,
        diff_section=diff_section,
    )
    (source_dir / "AGENTS.md").write_text(agents_md)

    target = os.environ.get("OSS_CRS_TARGET", source_dir.name)

    # Build crash log file list for the prompt
    crash_log_files = " ".join(f"`{work_dir}/crash_log_{i}.txt`" for i in range(len(povs)))
    prompt = (
        f"Fix the {sanitizer} vulnerability in project `{target}` "
        f"(harness: `{harness}`). {len(povs)} POV variant(s).\n\n"
        f"Crash logs: {crash_log_files}\n"
        f"Read AGENTS.md for workflow, tools, and submission instructions."
    )

    stdout_log = work_dir / "copilot_stdout.log"
    stderr_log = work_dir / "copilot_stderr.log"

    cmd = [
        "copilot",
        "--autopilot",
        "-p",
        prompt,
        "--model",
        COPILOT_MODEL,
        "--yolo",
    ]

    # Stream stdout/stderr to files. Additional Copilot debug logs are under copilot_home.

    try:
        with open(stdout_log, "w") as out_f, open(stderr_log, "w") as err_f:
            proc = subprocess.Popen(
                cmd,
                stdout=out_f,
                stderr=err_f,
                text=True,
                cwd=source_dir,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=AGENT_TIMEOUT or None)
                logger.info("Copilot CLI exit code: %d", proc.returncode)
            except subprocess.TimeoutExpired:
                logger.warning("Copilot CLI timed out (%ds), killing process tree", AGENT_TIMEOUT)
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    time.sleep(2)
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
    except Exception as e:
        logger.error("Error running Copilot CLI: %s", e)
        return False

    if proc.returncode != 0:
        logger.warning("Copilot CLI failed (rc=%d), see %s", proc.returncode, stderr_log)

    # Check if agent produced any patch files
    patches = list(patches_dir.glob("*.diff"))
    if patches:
        logger.info("Agent produced %d patch(es): %s", len(patches), [p.name for p in patches])
        return True

    logger.info("Agent did not produce a patch")
    return False
