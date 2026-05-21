#!/usr/bin/env python3
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

def main():

    base_temp_path = "/tmp/bundler"

    # 1. Gather environmental configurations from the GitHub Action context
    github_token = os.environ.get("GITHUB_TOKEN","")
    repo = os.environ.get("INPUT_REPO", "")
    config_path = os.environ.get("INPUT_CONFIG_PATH","")       # e.g., './src/repos.yaml'
    pipeline_path = os.environ.get("INPUT_PIPELINE_PATH", "")   # e.g., './src/pipeline.yml'
    event_type = os.environ.get("INPUT_EVENT_TYPE","")         # 'trigger_staging_build' or 'trigger_production_build'
    dest_path = os.environ.get("INPUT_DESTINATION_PATH", "")   # e.g., './compiled_addons'
    target_branch = os.environ.get("INPUT_TARGET_BRANCH", "")
    git_user_name = os.environ.get("INPUT_GIT_USER_NAME", "Impress Foods")
    git_user_email = os.environ.get("INPUT_GIT_USER_EMAIL", "info@impressfoods")

    required = [github_token, repo, config_path, pipeline_path, event_type, dest_path, target_branch]
    if not all(required):
        if not github_token:
            logging.error("Error: missing Github token")
            sys.exit(1)
        if not repo:
            logging.error("Error: missing repo name")
            sys.exit(1)
        if not config_path:
            logging.error("Error: missing config file path")
            sys.exit(1)
        if not pipeline_path:
            logging.error("Error: missing pipeline file path")
            sys.exit(1)
        if not event_type:
            logging.error("Error: missing event type")
            sys.exit(1)
        if not dest_path:
            logging.error("Error: missing destination file path")
            sys.exit(1)
        if not target_branch:
            logging.error("Error: missing target branch")
            sys.exit(1)


    # 2. Parse the custom pipeline layout metadata
    print(f"Reading pipeline setup blueprint from: {pipeline_path}")
    with open(pipeline_path, "r") as f:
        pipeline_data = yaml.safe_load(f) or {}
    
    module_whitelist = pipeline_data.get("module_whitelist", [])
    automation_cfg = pipeline_data.get("automation", {})
    target_label = automation_cfg.get("pr_trigger_label", "to-staging")
    
    print(f"Whitelisted target modules to pluck: {module_whitelist}")

    # 3. Read the base repository dependency tree configuration
    print(f"Reading baseline dependency rules from: {config_path}")
    with open(config_path, "r") as f:
        repos_config = yaml.safe_load(f) or {}

    # 4. Ingest and inject active tagged PRs ONLY if executing a staging build
    if event_type == "trigger_staging_build":
        if not github_token:
            print("Error: GITHUB_TOKEN environment variable is missing. Cannot call API.")
            sys.exit(1)
            
        print(f"Staging context identified. Scanning Code Repo for open PRs labeled: '{target_label}'")
        
        # Target repository path for your private codebase
        code_repo = repo
        url = f"https://api.github.com/repos/{code_repo}/pulls?state=open"
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            prs = response.json()
            
            for pr in prs:
                labels = [l["name"] for l in pr.get("labels", [])]
                if target_label in labels:
                    pr_num = pr["number"]
                    pr_branch = pr["head"]["ref"]
                    pr_repo_url = pr["head"]["repo"]["clone_url"]
                    
                    # Inject organization token credentials to authorize gitaggregate's private clone sequence
                    authenticated_url = pr_repo_url.replace("https://", f"https://x-access-token:{github_token}@")
                    
                    print(f"   ↳ Found matching PR #{pr_num} | Branch: '{pr_branch}'")
                    
                    # Allocate unique isolated staging targets inside our compilation sandbox
                    repos_config[f"{base_temp_path}/tmp_git_aggregate/pr_{pr_num}"] = {
                        "remotes": {
                            "origin": authenticated_url
                        },
                        "target": f"origin {pr_branch}"
                    }
        except Exception as e:
            print(f"Warning: GitHub API evaluation failed: {e}. Proceeding with fallback mode (base modules only).")

    # 5. Flush the compiled memory layout into a temporary operational runtime manifest
    runtime_manifest = f"{base_temp_path}/runtime_repos.yml"
    with open(runtime_manifest, "x") as f:
        yaml.dump(repos_config, f)
    print(f"Runtime manifest calculated and written to: {runtime_manifest}")

    # 6. Fire git-aggregator to compile external branches into the sandbox root
    print("Invoking git-aggregator core dependency engine...")
    run_command(["gitaggregate", "-c", runtime_manifest], cwd=base_temp_path)

    # 7. Purge stale targets and establish the clean destination directory
    print(f"Resetting target output compilation directory: {dest_path}")
    if os.path.exists(dest_path):
        shutil.rmtree(dest_path)
    os.makedirs(dest_path, exist_ok=True)

    # 8. Filter, slice, and flatten out requested modules (The Plucking Phase)
    print("Commencing module filtering phase based on explicit whitelist tracking...")
    search_root = Path(base_temp_path)
    
    if not search_root.exists():
        print("Warning: No codebases compiled by git-aggregator. Check your configuration parameters.")
        sys.exit(0)

    # Cross-reference the whitelist against our downloaded source matrix directories
    for module in module_whitelist:
        found = False
        # Sweep all codebases downloaded into the compilation sandbox root folder
        for repo_dir in search_root.iterdir():
            if repo_dir.is_dir():
                potential_module_path = repo_dir / module
                # Extract and duplicate the inner module root directory if discovered
                if potential_module_path.exists() and potential_module_path.is_dir():
                    print(f"   Extracted module '{module}' directly out of package directory: {repo_dir.name}")
                    shutil.copytree(potential_module_path, Path(dest_path) / module, dirs_exist_ok=True)
                    found = True
                    break
        if not found:
            print(f"Warning: Whitelisted module '{module}' was requested, but was completely absent from all source repositories.")

    # 9. Move modules from compiled_addons to repo root and clean scaffolding
    print("Assembling final directory structure...")
    if os.path.exists(dest_path):
        for item in os.listdir(dest_path):
            shutil.move(os.path.join(dest_path, item), os.path.join(os.getcwd(), item))
        shutil.rmtree(dest_path)

    if os.path.exists("./src/requirements.txt"):
        shutil.copy("./src/requirements.txt", "./requirements.txt")

    for cleanup in ["./src", "./.github", "./.gitignore"]:
        path = Path(cleanup)
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    # 10. Commit compiled addons to deployment repo and push to target branch
    print(f"Committing and pushing to branch: {target_branch}...")
    run_command(["git", "config", "user.name", git_user_name])
    run_command(["git", "config", "user.email", git_user_email])

    result = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
    origin_url = result.stdout.strip()
    auth_url = origin_url.replace("https://", f"https://x-access-token:{github_token}@")
    run_command(["git", "remote", "set-url", "origin", auth_url])

    run_command(["git", "add", "-A"])

    result = subprocess.run(
        ["git", "commit", "-m", f"Automated build from {repo}"],
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