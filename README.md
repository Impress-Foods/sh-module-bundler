# Sh Module Bundler

A Docker-based GitHub Action that compiles Odoo third-party modules from a `repos.yml` manifest and pushes the result to an Odoo.sh deployment repository.

## How it works

1. Fetches `pipeline.yml` and `repos.yml` from the source repo's `base_branch`
2. Optionally discovers PRs labeled for staging builds and includes their branches
3. Runs [git-aggregator](https://github.com/OCA/git-aggregator) to clone and compile all specified repos
4. Extracts whitelisted Odoo modules from the aggregated codebases
5. Generates a combined `requirements.txt` from module-level external dependencies
6. Generates a `README.md` inventory of all bundled modules
7. Commits everything and force-pushes to the deployment repo's `target_branch` with a `build-{timestamp}` tag

## Usage

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: impress-foods/sh-module-bundler@v1
        with:
          repo: ${{ github.repository }}
          target_branch: production
          event_type: trigger_production_build
          base_branch: main
```

## Inputs

| Name | Required | Description |
|------|----------|-------------|
| `repo` | Yes | Source repository (`org/repo`) |
| `target_branch` | Yes | Branch to push compiled addons to |
| `event_type` | Yes | `trigger_staging_build` or `trigger_production_build` |
| `base_branch` | Yes | Branch to fetch pipeline configs from |
| `git_user_name` | No | Git user name for commits (default: `Odoo.sh Bundler`) |
| `git_user_email` | No | Git user email for commits (default: `bundler@odoo.sh`) |

## Config files

### `pipeline.yml`

Defines which modules to extract and automation settings:

```yaml
module_whitelist:
  - module_a
  - module_b
automation:
  pr_trigger_label: to-staging
```

### `repos.yml`

Standard git-aggregator manifest listing third-party Odoo module sources:

```yaml
./thirdparty/my-repo:
  remotes:
    origin: https://github.com/OCA/my-repo.git
  merges:
    - origin 16.0
```

## Development

```bash
make install    # Install dependencies
make lint       # Run ruff linter
make test       # Run tests
make precommit  # Run pre-commit hooks
```

## License

See [LICENSE](LICENSE).
