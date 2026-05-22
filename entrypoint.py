#!/usr/bin/env python3
import base64
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, TypedDict

import requests
import yaml

logger = logging.getLogger(__name__)


class RepoRemoteConfig(TypedDict):
    origin: str


class RepoConfig(TypedDict):
    remotes: RepoRemoteConfig
    merges: list[str]


def run_command(cmd: list[str], cwd: str | None = None) -> None:
    """Executes a system command and streams live output to the runner log."""
    logger.info("Running command: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=False)
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
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    content = base64.b64decode(response.json()["content"])
    return yaml.safe_load(content)


def commit_and_push(
    repo: str, target_branch: str, user: str, email: str, token: str, base_branch: str
) -> None:
    logger.info("Committing and pushing to branch: %s...", target_branch)
    run_command(["git", "config", "--global", "--add", "safe.directory", os.getcwd()])
    run_command(["git", "config", "--global", "user.name", user])
    run_command(["git", "config", "--global", "user.email", email])

    result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
    origin_url = result.stdout.strip()
    auth_url = origin_url.replace("https://", f"https://x-access-token:{token}@")
    run_command(["git", "remote", "set-url", "origin", auth_url])

    run_command(["git", "add", "-A"])

    result = subprocess.run(
        ["git", "commit", "-m", f"Automated build from {repo}@{base_branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logger.info("Commit created. Pushing to %s...", target_branch)
        run_command(["git", "push", "--force", "origin", f"HEAD:{target_branch}"])
    else:
        logger.info("Nothing new to commit.")


def extract_modules(base_temp_path: str, workspace: str, whitelist: list[str]) -> bool:
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

    for module in whitelist:
        if (Path(workspace) / module).exists():
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
            logger.warning("Module '%s' not found in any aggregated repository.", module)
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
        logger.warning(
            "pyproject_dependencies failed (exit %d): %s",
            result.returncode,
            result.stderr.strip(),
        )
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

    url = f"https://api.github.com/repos/{repo}/pulls?state=open&base={base_branch}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        for pr in response.json():
            labels = [label["name"] for label in pr.get("labels", [])]
            if target_label in labels:
                pr_num = pr["number"]
                pr_branch = pr["head"]["ref"]
                logger.info("Found matching PR #%d | Branch: '%s'", pr_num, pr_branch)

                repos_config[code_key]["merges"].append(f"origin {pr_branch}")
    except Exception as e:
        logger.warning("PR discovery failed: %s. Proceeding with base modules only.", e)

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

    pipeline_data = fetch_yaml(repo, "pipeline.yml", base_branch, github_token) or {}

    module_whitelist = pipeline_data.get("module_whitelist", [])
    automation_cfg = pipeline_data.get("automation", {})
    target_label = automation_cfg.get("pr_trigger_label", "to-staging")

    logger.info("Whitelisted target modules to pluck: %s", module_whitelist)

    repos_config = fetch_yaml(repo, "repos.yml", base_branch, github_token) or {}

    code_key = f"{base_temp_path}/code_repo"
    code_repo_url = f"https://x-access-token:{github_token}@github.com/{repo}"
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
    run_command(["gitaggregate", "-c", runtime_manifest], cwd=base_temp_path)

    logger.info("Cleaning stale modules from workspace root...")
    workspace = os.getcwd()
    clean_workspace(workspace)

    extracted = extract_modules(base_temp_path, workspace, module_whitelist)
    if not extracted:
        logger.error("Could not extract modules")
        sys.exit(1)

    generate_requirements(workspace)

    if not skip_push:
        commit_and_push(
            repo,
            target_branch,
            git_user_name,
            git_user_email,
            github_token,
            base_branch,
        )
    else:
        logger.info("SKIP_PUSH set - skipping commit and push")

    logger.info("Task terminated successfully.")


if __name__ == "__main__":
    main()
