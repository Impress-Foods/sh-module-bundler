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
