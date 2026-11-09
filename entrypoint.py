#!/usr/bin/env python3
import base64
import contextlib
import logging
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

import requests
import yaml

from readme_generator import generate_readme

logger = logging.getLogger(__name__)


def get_version() -> str:
    pyproject = Path(__file__).resolve().parent / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


class RepoRemoteConfig(TypedDict):
    origin: str


class RepoConfig(TypedDict):
    remotes: RepoRemoteConfig
    merges: list[str]


def run_command(cmd: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> None:
    """Executes a system command and streams live output to the runner log."""
    logger.info("Running command: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=False)
    if result.returncode != 0:
        logger.error("Command failed with exit code %d", result.returncode)
        sys.exit(result.returncode)


def fetch_yaml(repo: str, path: str, ref: str, token: str) -> Any:
    """Fetch a YAML file from the code repo via GitHub Contents API."""
    logger.info("Fetching %s from %s@%s...", path, repo, ref)
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    content = base64.b64decode(response.json()["content"])
    return yaml.safe_load(content)


def setup_git(user: str, email: str) -> None:
    run_command(["git", "config", "--global", "--add", "safe.directory", os.getcwd()])
    run_command(["git", "config", "--global", "user.name", user])
    run_command(["git", "config", "--global", "user.email", email])


@contextlib.contextmanager
def git_credentials(token: str) -> Iterator[dict[str, str]]:
    """Yield an environment that authenticates git to github.com via GIT_ASKPASS.

    The token lives only in a mode-0700 temp script (removed on exit), never in a
    URL, in argv, or in an environment variable, so it cannot end up in
    ``.git/config``, logs, or ``--expand-env`` substitutions. The helper only
    answers prompts for github.com.
    """
    fd, helper_path = tempfile.mkstemp(suffix=".sh", prefix="git-askpass-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("#!/bin/sh\n")
            f.write('case "$1" in\n')
            f.write("  *github.com*)\n")
            f.write('    case "$1" in\n')
            f.write("      Username*) echo x-access-token ;;\n")
            f.write(f"      *) echo {shlex.quote(token)} ;;\n")
            f.write("    esac ;;\n")
            f.write("  *) echo ;;\n")
            f.write("esac\n")
        os.chmod(helper_path, stat.S_IRUSR | stat.S_IXUSR)
        env = os.environ.copy()
        env.pop("GITHUB_TOKEN", None)
        env["GIT_ASKPASS"] = helper_path
        env["GIT_TERMINAL_PROMPT"] = "0"
        yield env
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(helper_path)


def validate_target_branch(target_branch: str) -> None:
    """Refuse values that are not a plain branch name (this branch is force-pushed)."""
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", target_branch],
        capture_output=True,
        text=True,
    )
    if (
        result.returncode != 0
        or target_branch.startswith(("refs/", "-"))
        or target_branch == "HEAD"
    ):
        logger.error("Invalid target branch: %r", target_branch)
        sys.exit(1)


_MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def validate_module_whitelist(whitelist: Any) -> list[str]:
    """Ensure whitelist entries are plain directory names (no path traversal)."""
    if not isinstance(whitelist, list):
        logger.error("module_whitelist must be a list")
        sys.exit(1)
    for name in whitelist:
        if not isinstance(name, str) or not _MODULE_NAME_RE.match(name) or ".." in name:
            logger.error("Invalid module name in module_whitelist: %r", name)
            sys.exit(1)
    return whitelist


def commit_and_push(
    repo: str,
    target_branch: str,
    token: str,
    base_branch: str,
    build_tag: str,
) -> None:
    logger.info("Committing and pushing to branch: %s...", target_branch)
    with git_credentials(token) as git_env:
        run_command(["git", "add", "-A"])

        result = subprocess.run(
            ["git", "commit", "-m", f"Automated build from {repo}@{base_branch}"],
            env=git_env,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("Commit created. Pushing to %s...", target_branch)
            run_command(
                ["git", "push", "--force", "origin", f"HEAD:refs/heads/{target_branch}"],
                env=git_env,
            )
            run_command(["git", "tag", "-f", build_tag, "HEAD"], env=git_env)
            run_command(
                ["git", "push", "--force", "origin", f"refs/tags/{build_tag}"],
                env=git_env,
            )
            logger.info("Tagged commit as %s", build_tag)
        else:
            logger.info("Nothing new to commit.")


def extract_modules(base_temp_path: str, workspace: str, whitelist: list[str]) -> bool:
    """Copy modules into the workspace. Exits non-zero if a whitelisted module is missing."""
    logger.info("Commencing module extraction...")
    search_root = Path(base_temp_path)

    if not search_root.exists():
        logger.warning("No codebases compiled by git-aggregator.")
        return False

    code_repo_dir = search_root / "code_repo"

    if code_repo_dir.exists():
        for item in code_repo_dir.iterdir():
            if item.is_dir() and (item / "__manifest__.py").exists():
                logger.info("Extracted code repo module: %s", item.name)
                shutil.copytree(item, Path(workspace) / item.name, dirs_exist_ok=True)

    missing: list[str] = []
    for module in whitelist:
        module_path = Path(workspace) / module
        if module_path.exists() and any(
            (module_path / mf).exists() for mf in ("__manifest__.py", "__openerp__.py")
        ):
            continue
        found = False
        for repo_dir in search_root.iterdir():
            if repo_dir.name == "code_repo" or not repo_dir.is_dir():
                continue
            potential_module_path = repo_dir / module
            if potential_module_path.exists() and potential_module_path.is_dir():
                logger.info("Extracted module '%s' from: %s", module, repo_dir.name)
                shutil.copytree(potential_module_path, Path(workspace) / module, dirs_exist_ok=True)
                found = True
                break
        if not found:
            missing.append(module)
    if missing:
        logger.error("Whitelisted modules not found in any aggregated repository: %s", missing)
        sys.exit(1)
    return True


def clean_workspace(workspace: str) -> None:
    for item in os.listdir(workspace):
        item_path = os.path.join(workspace, item)
        if os.path.isdir(item_path) and (Path(item_path) / "__manifest__.py").exists():
            logger.info("Removing stale module: %s", item)
            shutil.rmtree(item_path)


def generate_requirements(workspace: str) -> None:
    """Generate requirements.txt from plucked modules' external dependencies."""
    logger.info("Generating aggregated requirements.txt...")
    result = subprocess.run(
        ["oca-gen-external-dependencies"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        logger.info("No pyproject.toml files found, skipping requirements generation")
    elif result.returncode != 0:
        logger.error(
            "oca-gen-external-dependencies failed (exit %d): %s",
            result.returncode,
            result.stderr.strip(),
        )
        sys.exit(1)
    else:
        logger.info("Generated requirements.txt")


def get_prs_to_include(
    repos_config: dict[str, RepoConfig],
    base_branch: str,
    target_label: str,
    repo: str,
    github_token: str,
    code_key: str,
) -> dict[str, RepoConfig]:
    logger.info(
        "Staging context identified. Scanning PRs targeting '%s' labeled: '%s'",
        base_branch,
        target_label,
    )

    url = f"https://api.github.com/repos/{repo}/pulls"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        page = 1
        while True:
            response = requests.get(
                url,
                headers=headers,
                params={"state": "open", "base": base_branch, "per_page": 100, "page": page},
                timeout=30,
            )
            response.raise_for_status()
            prs = response.json()
            for pr in prs:
                labels = [label["name"] for label in pr.get("labels", [])]
                if target_label not in labels:
                    continue
                head_repo = (pr["head"].get("repo") or {}).get("full_name", "")
                if head_repo.lower() != repo.lower():
                    logger.warning(
                        "Skipping PR #%d: head is from '%s', not '%s'",
                        pr["number"],
                        head_repo or "unknown repo",
                        repo,
                    )
                    continue
                pr_num = pr["number"]
                pr_branch = pr["head"]["ref"]
                logger.info("Found matching PR #%d | Branch: '%s'", pr_num, pr_branch)

                repos_config[code_key]["merges"].append(f"origin {pr_branch}")
            if len(prs) < 100:
                break
            page += 1
    except Exception as e:
        logger.error("PR discovery failed: %s. Refusing to build staging without labeled PRs.", e)
        sys.exit(1)

    return repos_config


def write_runtime_manifest(base_temp_path: str, repos_config: dict[str, RepoConfig]) -> str:
    runtime_manifest = f"{base_temp_path}/runtime_repos.yml"
    os.makedirs(os.path.dirname(runtime_manifest), exist_ok=True)
    with open(runtime_manifest, "w") as f:
        yaml.dump(repos_config, f)
    logger.info("Runtime manifest written to: %s", runtime_manifest)
    return runtime_manifest


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) > 1 and sys.argv[1] == "--version":
        print(get_version())
        return

    base_temp_path = "/tmp/bundler"

    github_token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("INPUT_REPO", "")
    event_type = os.environ.get("INPUT_EVENT_TYPE", "")
    target_branch = os.environ.get("INPUT_TARGET_BRANCH", "")
    base_branch = os.environ.get("INPUT_BASE_BRANCH", "")
    git_user_name = os.environ.get("INPUT_GIT_USER_NAME", "Odoo.sh Bundler")
    git_user_email = os.environ.get("INPUT_GIT_USER_EMAIL", "bundler@odoo.sh")
    skip_push = os.environ.get("SKIP_PUSH", "").lower() in ("1", "true", "yes")

    required = [github_token, repo, event_type, target_branch, base_branch]
    if not all(required):
        if not github_token:
            logger.error("Missing Github token")
            sys.exit(1)
        if not repo:
            logger.error("Missing repo name")
            sys.exit(1)
        if not event_type:
            logger.error("Missing event type")
            sys.exit(1)
        if not target_branch:
            logger.error("Missing target branch")
            sys.exit(1)
        if not base_branch:
            logger.error("Missing base branch")
            sys.exit(1)

    validate_target_branch(target_branch)
    setup_git(git_user_name, git_user_email)

    pipeline_data = fetch_yaml(repo, "pipeline.yml", base_branch, github_token) or {}

    module_whitelist = validate_module_whitelist(pipeline_data.get("module_whitelist") or [])
    automation_cfg = pipeline_data.get("automation", {})
    target_label = automation_cfg.get("pr_trigger_label", "to-staging")

    logger.info("Whitelisted target modules to pluck: %s", module_whitelist)

    repos_config = fetch_yaml(repo, "repos.yml", base_branch, github_token) or {}

    code_key = f"{base_temp_path}/code_repo"
    code_repo_url = f"https://github.com/{repo}"
    repos_config[code_key] = {
        "remotes": {"origin": code_repo_url},
        "merges": [f"origin {base_branch}"],
    }

    if event_type == "trigger_staging_build":
        repos_config = get_prs_to_include(
            repos_config, base_branch, target_label, repo, github_token, code_key
        )

    runtime_manifest = write_runtime_manifest(base_temp_path, repos_config)

    logger.info("Invoking git-aggregator core dependency engine...")
    with git_credentials(github_token) as aggregate_env:
        run_command(
            ["gitaggregate", "-c", runtime_manifest, "--expand-env"],
            cwd=base_temp_path,
            env=aggregate_env,
        )

    logger.info("Cleaning stale modules from workspace root...")

    ws_override = os.environ.get("WORKSPACE_OVERRIDE", None)
    workspace = os.getcwd()
    if ws_override:
        if not Path(ws_override).is_dir():
            os.mkdir(ws_override)
        workspace = ws_override

    clean_workspace(workspace)

    extracted = extract_modules(base_temp_path, workspace, module_whitelist)
    if not extracted:
        logger.error("Could not extract modules")
        sys.exit(1)

    generate_requirements(workspace)

    source_sha = os.environ.get("INPUT_SOURCE_SHA", "") or os.environ.get("GITHUB_SHA", "")
    source_ref = os.environ.get("INPUT_SOURCE_REF", "") or os.environ.get("GITHUB_REF_NAME", "")
    build_tag = f"build-{datetime.now():%Y%m%d%H%M%S}"
    generate_readme(
        workspace,
        repo,
        base_branch,
        target_branch,
        event_type,
        build_tag,
        set(module_whitelist),
        source_sha=source_sha,
        source_ref=source_ref,
    )

    if not skip_push:
        commit_and_push(
            repo,
            target_branch,
            github_token,
            base_branch,
            build_tag,
        )
    else:
        logger.info("SKIP_PUSH set - skipping commit and push")

    logger.info("Task terminated successfully.")


if __name__ == "__main__":
    main()
