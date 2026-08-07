from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_repository_contains_no_deployment_declaration():
    declarations = [
        path for path in (ROOT / "deployments").rglob("*.toml")
        if path.is_file()
    ]
    assert declarations == []


def test_installation_document_points_to_deployment_owned_inputs():
    text = (ROOT / "deployments" / "packages" / "INSTALL.md").read_text(encoding="utf-8")
    assert "no deployment declaration" in text
    assert "deployment-owned lock" in text
