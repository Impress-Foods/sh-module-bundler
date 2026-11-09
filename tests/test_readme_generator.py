from pathlib import Path

from readme_generator import generate_readme


def _module(root: Path, name: str, manifest: str) -> None:
    (root / name).mkdir()
    (root / name / "__manifest__.py").write_text(manifest)


def test_pipes_and_lists_are_escaped_in_table_cells(tmp_path: Path):
    _module(
        tmp_path,
        "mod_a",
        "{'name': 'A', 'summary': 'x | y\\nz', 'author': ['Foo', 'Bar | Baz'], 'version': '1'}",
    )
    generate_readme(str(tmp_path), "o/r", "main", "prod", "evt", "b1")
    readme = (tmp_path / "README.md").read_text()
    assert r"| [mod_a](mod_a/) | 1 |  | Foo, Bar \| Baz | x \| y z |" in readme


def test_unparseable_manifest_does_not_crash(tmp_path: Path):
    _module(tmp_path, "bad", "{'version': '16.0.' + '1'}")
    generate_readme(str(tmp_path), "o/r", "main", "prod", "evt", "b1")
    assert "[bad](bad/)" in (tmp_path / "README.md").read_text()


def test_mail_debranding_row_stays_on_one_line(tmp_path: Path):
    _module(
        tmp_path,
        "mail_debranding",
        "{'name': 'D', 'version': '19.0.1.0.0', 'license': 'AGPL-3',\n"
        " 'author': 'Tecnativa, Nexterp,\\n        Odoo Community Association (OCA)',\n"
        " 'summary': 'Removes anchor <a href odoo.com togheder > 20characters'}",
    )
    _module(tmp_path, "zzz_next", "{'name': 'N', 'version': '1'}")
    generate_readme(str(tmp_path), "o/r", "main", "prod", "evt", "b1")
    lines = (tmp_path / "README.md").read_text().splitlines()
    row = [i for i, line in enumerate(lines) if "[mail_debranding]" in line]
    assert len(row) == 1
    assert lines[row[0]].endswith("|")
    assert "<a" not in lines[row[0]]
    assert "[zzz_next]" in lines[row[0] + 1]
