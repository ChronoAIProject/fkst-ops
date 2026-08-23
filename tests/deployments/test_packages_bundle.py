from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_repository_contains_no_deployment_declaration():
    declarations = [
        path for path in (ROOT / "deployments").rglob("*.toml")
        if path.is_file()
        and tomllib.loads(path.read_text(encoding="utf-8")).get("schema")
        == "fkst.ops.deployment.v1"
    ]
    assert declarations == []


def test_every_install_document_repository_path_exists():
    install = ROOT / "deployments" / "packages" / "INSTALL.md"
    text = install.read_text(encoding="utf-8")
    inline_references = re.findall(r"`([^`\n]+(?:/|\.[A-Za-z0-9]+))`", text)
    copy_sources = re.findall(r"^cp(?: -R)? (\S+)", text, flags=re.MULTILINE)
    repository_paths = {
        reference for reference in inline_references + copy_sources
        if not reference.startswith("<deployment-repository>")
    }

    missing = [
        reference for reference in sorted(repository_paths)
        if not (ROOT / reference).exists()
    ]
    assert missing == []
