import ast
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MANIFEST_FILES = ("__openerp__.py", "__manifest__.py")


def _sanitize(text: str) -> str:
    if not text:
        return ""
    return " ".join(text.split())


def _format_author(author: str | list[str]) -> str:
    if isinstance(author, list):
        return ", ".join(author)
    return author or ""


def generate_readme(
    workspace: str,
    repo: str,
    base_branch: str,
    target_branch: str,
    event_type: str,
    build_tag: str,
    external_modules: set[str] | None = None,
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

    lines: list[str] = []
    lines.append("# Module Bundle")
    lines.append("")
    lines.append("Automatically generated bundle of Odoo modules.")
    lines.append("")
    lines.append("## Build Info")
    lines.append("")
    lines.append(f"- **Generated:** {datetime.now().isoformat()}")
    lines.append(f"- **Source:** {repo}@{base_branch}")
    lines.append(f"- **Target:** {target_branch}")
    lines.append(f"- **Event:** {event_type}")
    lines.append(f"- **Build Tag:** {build_tag}")
    lines.append("")
    if total == 0:
        lines.append("*No modules found in this bundle.*")
    else:
        installable_count = len(installable_rows)
        unported_count = len(unported_rows)
        stats = f"**{total} module{'s' if total != 1 else ''} — "
        if unported_count == 0:
            stats += "all installable"
        else:
            stats += f"{installable_count} installable, {unported_count} unported"
        stats += ".**"
        lines.append(stats)
    lines.append("")

    def _write_table(
        heading: str, rows: list[tuple[str, str, str, str, str, bool, str, bool]]
    ) -> None:
        lines.append(f"## {heading}")
        lines.append("")
        lines.append("| Module | Version | License | Author | Summary |")
        lines.append("|--------|---------|---------|--------|---------|")
        for link, version, license_, author, summary, _, _, _ in rows:
            lines.append(f"| {link} | {version} | {license_} | {author} | {summary} |")
        lines.append("")

    if has_both:
        if custom_installable:
            _write_table("Custom Modules", custom_installable)
        if external_installable:
            _write_table("External Modules", external_installable)
    elif installable_rows:
        _write_table("Available Modules", installable_rows)

    if unported_rows:
        lines.append("## Unported Modules")
        lines.append("")
        lines.append("| Module | Version | License | Author | Summary | Source |")
        lines.append("|--------|---------|---------|--------|---------|--------|")
        for link, version, license_, author, summary, _, _, is_external in unported_rows:
            source = "External" if is_external else "Custom"
            lines.append(f"| {link} | {version} | {license_} | {author} | {summary} | {source} |")
        lines.append("")

    requirements = workspace_path / "requirements.txt"
    if requirements.is_file():
        lines.append("## Requirements")
        lines.append("")
        lines.append("See [requirements.txt](requirements.txt) for Python package dependencies.")
        lines.append("")

    readme_path = workspace_path / "README.md"
    readme_path.write_text("\n".join(lines))
    logger.info("Generated README.md with %d modules", total)
