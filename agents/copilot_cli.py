"""
GitHub Copilot CLI agent for autonomous vulnerability patching.

Implements the agent interface (setup / run) using GitHub Copilot CLI
in non-interactive mode. Copilot CLI reads AGENTS.md for workflow
instructions, then autonomously: analyzes evidence -> edits source ->
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
if AGENT_TIMEOUT < 0:
    AGENT_TIMEOUT = 0

_TEMPLATE_PATH = Path(__file__).with_suffix(".md")
_SECTIONS_DIR = _TEMPLATE_PATH.with_name("sections")


def _load_section(section_name: str) -> str:
    section_path = _SECTIONS_DIR / section_name
    return section_path.read_text()


def _load_prompt_templates() -> dict[str, str]:
    return {
        "agents_md": _TEMPLATE_PATH.read_text(),
        "workflow_pov": _load_section("workflow_pov.md"),
        "workflow_static": _load_section("workflow_static.md"),
        "pov_present": _load_section("pov_present.md"),
        "pov_absent": _load_section("pov_absent.md"),
        "bug_candidates_present": _load_section("bug_candidates_present.md"),
        "bug_candidates_absent": _load_section("bug_candidates_absent.md"),
        "diff_present": _load_section("diff_present.md"),
        "pre_submit": _load_section("pre_submit.md"),
    }


def _md_inline(value: str) -> str:
    """Return a markdown-safe inline code span."""
    ticks = 1
    while "`" * ticks in value:
        ticks += 1
    fence = "`" * ticks
    return f"{fence}{value}{fence}"


def _snapshot_patch_state(patches_dir: Path) -> dict[str, tuple[int, int]]:
    """Capture patch file state by name -> (mtime_ns, size)."""
    state: dict[str, tuple[int, int]] = {}
    for p in patches_dir.glob("*.diff"):
        try:
            st = p.stat()
        except OSError:
            continue
        state[p.name] = (st.st_mtime_ns, st.st_size)
    return state


def _changed_patches(
    before: dict[str, tuple[int, int]], patches_dir: Path
) -> list[str]:
    """Return sorted patch names that are new or modified since snapshot."""
    now = _snapshot_patch_state(patches_dir)
    return sorted(name for name, state in now.items() if before.get(name) != state)


def _make_fenced_block(body: str, language: str = "") -> str:
    """Return a markdown fenced block with a safe backtick fence."""
    fence_len = 3
    while "`" * fence_len in body:
        fence_len += 1
    fence = "`" * fence_len
    lang = language.strip()
    if lang:
        return f"{fence}{lang}\n{body}\n{fence}"
    return f"{fence}\n{body}\n{fence}"


def setup(source_dir: Path, config: dict) -> None:
    """One-time agent configuration.

    - Sets Copilot CLI env vars (COPILOT_MODEL, auth token)
    - Writes copilot config.json for non-interactive autonomous mode
    - Writes AGENTS.md into source_dir with libCRS tool docs + workflow
    """
    github_token = config.get("copilot_github_token", COPILOT_GITHUB_TOKEN)
    subscription_token = config.get("copilot_subscription_token", COPILOT_SUBSCRIPTION_TOKEN)
    copilot_home = Path(config.get("copilot_home", Path.home() / ".copilot"))
    copilot_home.mkdir(parents=True, exist_ok=True)

    os.environ["IS_SANDBOX"] = "1"

    token_source = None
    auth_token = ""
    if github_token:
        auth_token = github_token
        token_source = "COPILOT_GITHUB_TOKEN"
    elif subscription_token:
        auth_token = subscription_token
        token_source = "COPILOT_SUBSCRIPTION_TOKEN"

    if auth_token:
        os.environ["COPILOT_GITHUB_TOKEN"] = auth_token
        logger.info("Configured Copilot auth token from %s", token_source)
    else:
        logger.warning(
            "No COPILOT_GITHUB_TOKEN or COPILOT_SUBSCRIPTION_TOKEN set. "
            "Copilot CLI authentication may fail unless token env vars are already present."
        )

    copilot_config = {
        "model": COPILOT_MODEL,
    }
    config_path = copilot_home / "config.json"
    config_path.write_text(json.dumps(copilot_config, indent=2))
    config_path.chmod(0o600)
    logger.info("Wrote config.json to %s (model=%s)", config_path, COPILOT_MODEL)

    logger.info("Model: %s", COPILOT_MODEL)

    global_gitignore = Path.home() / ".gitignore"
    existing = ""
    if global_gitignore.exists():
        existing = global_gitignore.read_text(errors="replace")
    lines = [line.rstrip("\n") for line in existing.splitlines()]
    if "AGENTS.md" not in lines:
        lines.append("AGENTS.md")
    global_gitignore.write_text("\n".join(lines).rstrip("\n") + "\n")
    subprocess.run(
        ["git", "config", "--global", "core.excludesFile", str(global_gitignore)],
        capture_output=True,
    )

    logger.info("Agent setup complete")


def run(
    source_dir: Path,
    povs: list[Path],
    bug_candidates: list[Path],
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

    povs is a list of POV paths (possibly empty).
    bug_candidates is a list of static finding files (possibly empty).
    Writes available evidence and AGENTS.md (with concrete paths), then sends a prompt.

    Returns True if a patch file was produced in patches_dir.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        templates = _load_prompt_templates()
    except OSError as e:
        logger.error("Failed to load prompt template(s): %s", e)
        return False

    pov_sections = []
    for pov_path in povs:
        pov_sections.append(
            f"- POV: {_md_inline(str(pov_path))}\n"
            f"  Reproduce/Test: {_md_inline(f'libCRS run-pov {pov_path} <response_dir> --harness {harness} --build-id <build_id> --builder {builder}')}"
        )

    if pov_sections:
        pov_list = "\n".join(pov_sections)
        pov_section = templates["pov_present"].format(
            pov_count=len(povs),
            pov_list=pov_list,
        )
        workflow_section = templates["workflow_pov"]
        pre_submit_pov = "- [ ] `pov_exit_code` = 0 for EVERY provided POV variant\n"
    else:
        pov_section = templates["pov_absent"]
        workflow_section = templates["workflow_static"]
        pre_submit_pov = ""

    bug_candidate_list = "\n".join(f"- {_md_inline(str(p))}" for p in bug_candidates)
    if bug_candidate_list:
        bug_candidate_section = templates["bug_candidates_present"].format(
            bug_candidate_list=bug_candidate_list
        )
    else:
        bug_candidate_section = templates["bug_candidates_absent"]

    if ref_diff:
        changed_files = [
            line.split("b/", 1)[1]
            for line in ref_diff.splitlines()
            if line.startswith("+++ b/")
        ]
        changed_files_str = ", ".join(_md_inline(f) for f in changed_files) if changed_files else "(see diff)"
        diff_section = templates["diff_present"].format(
            changed_files_str=changed_files_str,
            diff_block=_make_fenced_block(ref_diff, "diff"),
        )
    else:
        diff_section = ""

    if ref_diff:
        diff_validation_hint = (
            "- [ ] Patch addresses the vulnerable change context from the reference diff\n"
        )
    else:
        diff_validation_hint = ""

    pre_submit_section = templates["pre_submit"].format(
        pov_line=pre_submit_pov,
        diff_line=diff_validation_hint,
    )

    agents_md = templates["agents_md"].format(
        language=language,
        sanitizer=sanitizer,
        work_dir=work_dir,
        harness=harness,
        patches_dir=patches_dir,
        workflow_section=workflow_section,
        pov_section=pov_section,
        bug_candidate_section=bug_candidate_section,
        pre_submit_section=pre_submit_section,
        builder=builder,
        diff_section=diff_section,
    )
    (source_dir / "AGENTS.md").write_text(agents_md)

    target = os.environ.get("OSS_CRS_TARGET", source_dir.name)

    prompt_lines = [
        f"Fix the {sanitizer} vulnerability in project {_md_inline(target)} (harness: {_md_inline(harness)}).",
        "",
        "Available evidence:",
        f"- POV variants: {len(povs)}",
        f"- Bug-candidate files: {len(bug_candidates)}",
        f"- Reference diff: {'yes' if ref_diff else 'no'}",
    ]
    if povs:
        pov_files = " ".join(_md_inline(str(p)) for p in povs)
        prompt_lines.append(f"- POV files: {pov_files}")
    if bug_candidates:
        bug_files = " ".join(_md_inline(str(p)) for p in bug_candidates)
        prompt_lines.append(f"- Bug-candidate report files: {bug_files}")
    prompt_lines.extend(
        [
            "",
            "Read AGENTS.md for workflow, tools, and submission instructions.",
        ]
    )
    prompt = "\n".join(prompt_lines)

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

    existing_patches = _snapshot_patch_state(patches_dir)

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
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
    except Exception as e:
        logger.error("Error running Copilot CLI: %s", e)
        return False

    if proc.returncode != 0:
        logger.warning("Copilot CLI failed (rc=%d), see %s", proc.returncode, stderr_log)

    changed_patches = _changed_patches(existing_patches, patches_dir)
    if changed_patches:
        logger.info(
            "Agent produced %d updated/new patch(es): %s",
            len(changed_patches),
            changed_patches,
        )
        return True

    logger.info("Agent did not produce a patch")
    return False
