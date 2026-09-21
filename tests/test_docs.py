"""Documentation consistency checks.

These tests fail when the code and the docs drift apart, which is how the
docs stay up to date. See docs/DEVELOPMENT.md ("Keeping docs in sync").
"""
from pathlib import Path

import pytest

import app.main as main_module
from app.config import Settings
from app.export.csv_export import column_headers

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

REQUIRED_DOCS = [
    "README.md",
    "docs/README.md",
    "docs/ARCHITECTURE.md",
    "docs/API.md",
    "docs/SCRAPING.md",
    "docs/ENRICHMENT.md",
    "docs/DATA_MODEL.md",
    "docs/DEVELOPMENT.md",
    "docs/TROUBLESHOOTING.md",
    "docs/CHANGELOG.md",
]


@pytest.mark.parametrize("path", REQUIRED_DOCS)
def test_required_docs_exist(path):
    assert (ROOT / path).is_file(), f"{path} is missing"


def test_data_model_lists_every_csv_column():
    text = (DOCS / "DATA_MODEL.md").read_text(encoding="utf-8")
    for header in column_headers(extended=True):
        assert f"`{header}`" in text, f"column {header!r} is not documented in docs/DATA_MODEL.md"


def test_api_doc_mentions_every_route():
    text = (DOCS / "API.md").read_text(encoding="utf-8")
    for route in main_module.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        assert f"`{path}`" in text or path in text, f"route {path} is not documented in docs/API.md"


def test_development_doc_lists_every_setting():
    text = (DOCS / "DEVELOPMENT.md").read_text(encoding="utf-8")
    for name in Settings.model_fields:
        assert f"`{name}`" in text, f"setting {name} is not documented in docs/DEVELOPMENT.md"


def test_readme_links_to_docs():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for path in REQUIRED_DOCS[2:]:
        assert path.split("/")[-1] in text, f"README does not link to {path}"


def test_docs_index_links_every_doc():
    text = (DOCS / "README.md").read_text(encoding="utf-8")
    for path in REQUIRED_DOCS[2:]:
        assert path.split("/")[-1] in text, f"docs/README.md does not link to {path}"


def test_changelog_mentions_current_version():
    from app import __version__

    text = (DOCS / "CHANGELOG.md").read_text(encoding="utf-8")
    assert __version__ in text, f"docs/CHANGELOG.md has no entry for {__version__}"


def test_mermaid_diagrams_present_in_architecture_docs():
    for name in ("ARCHITECTURE.md", "SCRAPING.md", "ENRICHMENT.md"):
        text = (DOCS / name).read_text(encoding="utf-8")
        assert "```mermaid" in text, f"docs/{name} should contain at least one mermaid diagram"
