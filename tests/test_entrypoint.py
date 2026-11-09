from pathlib import Path
from unittest.mock import patch

import yaml


def test_fetch_yaml_returns_none_on_empty():
    from entrypoint import fetch_yaml

    mock_response = type(
        "Response",
        (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {"content": ""},
        },
    )()

    with patch("entrypoint.requests.get", return_value=mock_response):
        result = fetch_yaml("org/repo", "pipeline.yml", "main", "token")

    assert result is None


def test_write_runtime_manifest_creates_yaml(tmp_path: Path):
    from entrypoint import RepoConfig, write_runtime_manifest

    config: dict[str, RepoConfig] = {
        "key": {"remotes": {"origin": "url"}, "merges": ["origin main"]}
    }
    result = write_runtime_manifest(str(tmp_path), config)

    expected = tmp_path / "runtime_repos.yml"
    assert result == str(expected)
    assert expected.exists()
    with open(expected) as f:
        data = yaml.safe_load(f)
    assert data == {"key": {"remotes": {"origin": "url"}, "merges": ["origin main"]}}


def test_clean_workspace_removes_manifest_dirs(tmp_path: Path):
    from entrypoint import clean_workspace

    module_dir = tmp_path / "test_module"
    module_dir.mkdir()
    (module_dir / "__manifest__.py").touch()
    keep_file = tmp_path / "keep_me.txt"
    keep_file.touch()

    clean_workspace(str(tmp_path))

    assert not module_dir.exists()
    assert keep_file.exists()


def test_extract_modules_returns_false_when_missing(tmp_path: Path):
    from entrypoint import extract_modules

    result = extract_modules("/nonexistent", str(tmp_path), [])
    assert result is False


def test_run_command_exits_on_failure():
    from entrypoint import run_command

    with patch("entrypoint.sys.exit") as mock_exit:
        with patch("entrypoint.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.text = ""
            run_command(["false"])
            mock_exit.assert_called_once_with(1)


def test_git_credentials_hides_token_and_cleans_up():
    import os
    import subprocess

    from entrypoint import git_credentials

    with patch.dict(os.environ, {"GITHUB_TOKEN": "s3cret"}):
        with git_credentials("s3cret'tok") as env:
            helper = env["GIT_ASKPASS"]
            assert env["GITHUB_TOKEN"] == "s3cret"  # repos.yml may use ${GITHUB_TOKEN}
            run = lambda prompt: subprocess.run(  # noqa: E731
                [helper, prompt], capture_output=True, text=True
            ).stdout.strip()
            assert run("Username for 'https://github.com': ") == "x-access-token"
            assert run("Password for 'https://x@github.com': ") == "s3cret'tok"
            assert run("Password for 'https://evil.example': ") == ""
        assert not os.path.exists(helper)


def test_git_credentials_cleans_up_on_error():
    import os

    import pytest

    from entrypoint import git_credentials

    with pytest.raises(RuntimeError):
        with git_credentials("t") as env:
            helper = env["GIT_ASKPASS"]
            raise RuntimeError
    assert not os.path.exists(helper)


def test_validate_module_whitelist():
    import pytest

    from entrypoint import validate_module_whitelist

    assert validate_module_whitelist(["sale_x", "web-1.0"]) == ["sale_x", "web-1.0"]
    for bad in (["../x"], ["/etc"], ["a/b"], [""], [1], ["a..b"]):
        with pytest.raises(SystemExit):
            validate_module_whitelist(bad)
    with pytest.raises(SystemExit):
        validate_module_whitelist("sale_x")


def test_validate_target_branch():
    import pytest

    from entrypoint import validate_target_branch

    validate_target_branch("production")
    validate_target_branch("release/1.0")
    for bad in ("", "-f", "refs/heads/x", "HEAD", "a..b", "x y"):
        with pytest.raises(SystemExit):
            validate_target_branch(bad)


def _pr(num, label, repo_name):
    return {
        "number": num,
        "labels": [{"name": label}],
        "head": {"ref": f"b{num}", "repo": {"full_name": repo_name} if repo_name else None},
    }


def test_get_prs_skips_forks_and_unlabeled():
    from entrypoint import get_prs_to_include

    prs = [
        _pr(1, "to-staging", "org/repo"),
        _pr(2, "to-staging", "evil/repo"),
        _pr(3, "to-staging", None),
        _pr(4, "other", "org/repo"),
    ]
    resp = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: prs})()
    cfg = {"k": {"remotes": {"origin": "u"}, "merges": ["origin main"]}}
    with patch("entrypoint.requests.get", return_value=resp):
        out = get_prs_to_include(cfg, "main", "to-staging", "Org/Repo", "t", "k")
    assert out["k"]["merges"] == ["origin main", "origin b1"]


def test_extract_modules_exits_when_whitelisted_module_missing(tmp_path: Path):
    import pytest

    from entrypoint import extract_modules

    base = tmp_path / "base"
    (base / "repo_a").mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(SystemExit):
        extract_modules(str(base), str(ws), ["nope"])


def test_generate_requirements_exits_on_failure(tmp_path: Path):
    import pytest

    from entrypoint import generate_requirements

    with patch("entrypoint.subprocess.run") as run:
        run.return_value.returncode = 2
        run.return_value.stderr = "boom"
        with pytest.raises(SystemExit):
            generate_requirements(str(tmp_path))
        run.return_value.returncode = 1  # no pyproject files: not an error
        generate_requirements(str(tmp_path))


def test_get_prs_exits_on_discovery_failure():
    import pytest

    from entrypoint import get_prs_to_include

    with patch("entrypoint.requests.get", side_effect=OSError("down")):
        with pytest.raises(SystemExit):
            get_prs_to_include({}, "main", "to-staging", "org/repo", "t", "k")


def test_action_image_tag_matches_project_version():
    import re
    import tomllib

    root = Path(__file__).resolve().parent.parent
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    image = re.search(r"image:\s*\"?docker://\S+?:([^\s\"]+)", (root / "action.yml").read_text())
    assert image and image.group(1) == version
