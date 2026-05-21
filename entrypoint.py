#!/usr/bin/env python3
import base64
import logging
import os
import sys
import yaml
import shutil
import subprocess
import requests
from pathlib import Path

def run_command(cmd, cwd=None):
    """Executes a system command and streams live output to the runner log."""
    print(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=False)
    if result.returncode != 0:
        print(f"Error: Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)

def fetch_yaml(repo, path, ref, token):
    """Fetch a YAML file from the code repo via GitHub Contents API."""
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    content = base64.b64decode(response.json()["content"])
    return yaml.safe_load(content)

def main():

    base_temp_path = "/tmp/bundler"
    dest_path = "./compiled_addons"

    # 1. Gather environmental configurations from the GitHub Action context
    github_token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("INPUT_REPO", "")
    event_type = os.environ.get("INPUT_EVENT_TYPE", "")
    target_branch = os.environ.get("INPUT_TARGET_BRANCH", "")
    base_branch = os.environ.get("INPUT_BASE_BRANCH", "")
    git_user_name = os.environ.get("INPUT_GIT_USER_NAME", "Odoo.sh Bundler")
    git_user_email = os.environ.get("INPUT_GIT_USER_EMAIL", "bundler@odoo.sh")

    required = [github_token, repo, event_type, target_branch, base_branch]
    if not all(required):
        if not github_token:
            logging.error("Error: missing Github token")
            sys.exit(1)
        if not repo:
            logging.error("Error: missing repo name")
            sys.exit(1)
        if not event_type:
            logging.error("Error: missing event type")
            sys.exit(1)
        if not target_branch:
            logging.error("Error: missing target branch")
            sys.exit(1)
        if not base_branch:
            logging.error("Error: missing base branch")
            sys.exit(1)

    # 2. Fetch pipeline.yml from the code repo
    print(f"Fetching pipeline.yml from {repo}@{base_branch}...")
    pipeline_data = fetch_yaml(repo, "pipeline.yml", base_branch, github_token) or {}

    module_whitelist = pipeline_data.get("module_whitelist", [])
    automation_cfg = pipeline_data.get("automation", {})
    target_label = automation_cfg.get("pr_trigger_label", "to-staging")

    print(f"Whitelisted target modules to pluck: {module_whitelist}")

    # 3. Fetch repos.yml from the code repo
    print(f"Fetching repos.yml from {repo}@{base_branch}...")
    repos_config = fetch_yaml(repo, "repos.yml", base_branch, github_token) or {}

    # Auto-inject the code repo itself so its modules are aggregated alongside third-party repos
    code_repo_url = f"https://x-access-token:{github_token}@github.com/{repo}"
    repos_config[f"{base_temp_path}/tmp_git_aggregate/code_repo"] = {
        "remotes": {"origin": code_repo_url},
        "merges": [f"origin {base_branch}"]
    }

    # 4. Discover PRs labeled for staging injection
    if event_type == "trigger_staging_build":
        print(f"Staging context identified. Scanning PRs targeting '{base_branch}' labeled: '{target_label}'")

        url = f"https://api.github.com/repos/{repo}/pulls?state=open&base={base_branch}"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            for pr in response.json():
                labels = [l["name"] for l in pr.get("labels", [])]
                if target_label in labels:
                    pr_num = pr["number"]
                    pr_branch = pr["head"]["ref"]
                    pr_repo_url = pr["head"]["repo"]["clone_url"]
                    authenticated_url = pr_repo_url.replace("https://", f"https://x-access-token:{github_token}@")

                    print(f"   ↳ Found matching PR #{pr_num} | Branch: '{pr_branch}'")

                    repos_config[code_key]["merges"].append(f"origin {pr_branch}")
        except Exception as e:
            print(f"Warning: PR discovery failed: {e}. Proceeding with base modules only.")

    # 5. Write runtime manifest
    runtime_manifest = f"{base_temp_path}/runtime_repos.yml"
    os.makedirs(os.path.dirname(runtime_manifest), exist_ok=True)
    with open(runtime_manifest, "x") as f:
        yaml.dump(repos_config, f)
    print(f"Runtime manifest written to: {runtime_manifest}")

    # 6. Run git-aggregator
    print("Invoking git-aggregator core dependency engine...")
    run_command(["gitaggregate", "-c", runtime_manifest], cwd=base_temp_path)

    # 7. Prepare destination directory
    print(f"Resetting target output directory: {dest_path}")
    if os.path.exists(dest_path):
        shutil.rmtree(dest_path)
    os.makedirs(dest_path, exist_ok=True)

    # 8. Pluck modules from aggregated repos
    print("Commencing module extraction...")
    search_root = Path(base_temp_path)

    if not search_root.exists():
        print("Warning: No codebases compiled by git-aggregator.")
        sys.exit(0)

    code_repo_dir = search_root / "code_repo"

    # Pass 1: Copy ALL Odoo modules from the code repo (bypasses whitelist)
    if code_repo_dir.exists():
        for item in code_repo_dir.iterdir():
            if item.is_dir() and (item / "__manifest__.py").exists():
                print(f"   Extracted code repo module: {item.name}")
                shutil.copytree(item, Path(dest_path) / item.name, dirs_exist_ok=True)

    # Pass 2: Copy whitelisted modules from other repos (skip if already copied)
    for module in module_whitelist:
        if (Path(dest_path) / module).exists():
            continue
        found = False
        for repo_dir in search_root.iterdir():
            if repo_dir.name == "code_repo" or not repo_dir.is_dir():
                continue
            potential_module_path = repo_dir / module
            if potential_module_path.exists() and potential_module_path.is_dir():
                print(f"   Extracted module '{module}' from: {repo_dir.name}")
                shutil.copytree(potential_module_path, Path(dest_path) / module, dirs_exist_ok=True)
                found = True
                break
        if not found:
            print(f"Warning: Module '{module}' not found in any aggregated repository.")

    # 9. Move modules to repo root and clean up
    print("Assembling final directory structure...")
    if os.path.exists(dest_path):
        for item in os.listdir(dest_path):
            shutil.move(os.path.join(dest_path, item), os.path.join(os.getcwd(), item))
        shutil.rmtree(dest_path)

    # 10. Commit and push to deployment repo
    print(f"Committing and pushing to branch: {target_branch}...")
    run_command(["git", "config", "--global", "--add", "safe.directory", "/github/workspace"])
    run_command(["git", "config", "--global", "user.name", git_user_name])
    run_command(["git", "config", "--global", "user.email", git_user_email])

    result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
    origin_url = result.stdout.strip()
    auth_url = origin_url.replace("https://", f"https://x-access-token:{github_token}@")
    run_command(["git", "remote", "set-url", "origin", auth_url])

    run_command(["git", "add", "-A"])

    result = subprocess.run(
        ["git", "commit", "-m", f"Automated build from {repo}@{base_branch}"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"   Commit created. Pushing to {target_branch}...")
        run_command(["git", "push", "--force", "origin", f"HEAD:{target_branch}"])
    else:
        print("   Nothing new to commit.")

    print("Task terminated successfully.")

if __name__ == "__main__":
    main()
