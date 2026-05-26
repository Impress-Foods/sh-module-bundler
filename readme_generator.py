import ast
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

MANIFEST_FILES = ("__openerp__.py", "__manifest__.py")

_TEMPLATE_DIR = Path(__file__).parent
_TEMPLATE_NAME = "readme_template.md.j2"

_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=False)
_template = _env.get_template(_TEMPLATE_NAME)


def _sanitize(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split())


def _format_author(author: str | list[str]) -> str:
    if isinstance(author, list):
        return ", ".join(author)
    return author or ""


def _to_module_dict(
    row: tuple[str, str, str, str, str, bool, str, bool],
) -> dict[str, str]:
    return {
        "link": row[0],
        "version": row[1],
        "license_": row[2],
        "author": row[3],
        "summary": row[4],
    }


def generate_readme(
    workspace: str,
    repo: str,
    base_branch: str,
    target_branch: str,
    event_type: str,
    build_tag: str,
    external_modules: set[str] | None = None,
    source_sha: str = "",
    source_ref: str = "",
) -> None:
    workspace_path = Path(workspace)
    if not workspace_path.is_dir():
        logger.warning("Workspace %s does not exist, skipping README generation", workspace)
        return

    rows: list[tuple[str, str, str, str, str, bool, str, bool]] = []
    for item in sorted(workspace_path.iterdir()):
        if not item.is_dir():
            continue
        manifest = None
        for manifest_file in MANIFEST_FILES:
            manifest_path = item / manifest_file
            if manifest_path.is_file():
                manifest = manifest_path
                break
        if manifest is None:
            continue
        try:
            with open(manifest) as f:
                data = ast.literal_eval(f.read())
        except Exception:
            logger.warning("Could not parse manifest in %s, using defaults", item.name)
            data: dict[str, Any] = {}
        name = data.get("name") or item.name
        version = data.get("version", "")
        license_ = data.get("license", "")
        author = _format_author(data.get("author", ""))
        summary = _sanitize(data.get("summary", ""))
        installable = data.get("installable", True)
        is_external = external_modules is not None and item.name in external_modules
        link = f"[{item.name}]({item.name}/)"
        rows.append((link, version, license_, author, summary, installable, name, is_external))

    installable_rows = [r for r in rows if r[5]]
    unported_rows = [r for r in rows if not r[5]]
    custom_installable = [r for r in installable_rows if not r[7]]
    external_installable = [r for r in installable_rows if r[7]]
    total = len(rows)
    has_both = external_modules is not None and custom_installable and external_installable
    installable_count = len(installable_rows)
    unported_count = len(unported_rows)

    context = {
        "build_info": {
            "timestamp": datetime.now().isoformat(),
            "source": f"{repo}@{base_branch}",
            "target": target_branch,
            "event": event_type,
            "build_tag": build_tag,
            "ref": source_ref or (source_sha[:7] if source_sha else ""),
        },
        "total": total,
        "installable_count": installable_count,
        "unported_count": unported_count,
        "has_both": has_both,
        "custom_modules": [_to_module_dict(r) for r in custom_installable],
        "external_modules": [_to_module_dict(r) for r in external_installable],
        "installable_modules": [_to_module_dict(r) for r in installable_rows],
        "unported_modules": [
            {
                "link": r[0],
                "version": r[1],
                "license_": r[2],
                "author": r[3],
                "summary": r[4],
                "source": "External" if r[7] else "Custom",
            }
            for r in unported_rows
        ],
        "has_requirements": (workspace_path / "requirements.txt").is_file(),
    }

    readme_content = _template.render(context)

    readme_path = workspace_path / "README.md"
    readme_path.write_text(readme_content)
    logger.info("Generated README.md with %d modules", total)
