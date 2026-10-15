"""Release validation prevents incompatible or unversioned HACS publications."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ae200_release", ROOT / "scripts/prepare_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def source_tree(tmp_path):
    files = {
        "custom_components/ae200/manifest.json": json.dumps({"version": "1.2.3b4", "domain": "ae200"}),
        "pymelans/pyproject.toml": '[project]\nversion = "1.2.3b4"\n',
        "pymelans/pymelans/const.py": 'VERSION = "1.2.3b4"\n',
        "custom_components/ae200/_pymelans/const.py": 'VERSION = "1.2.3b4"\n',
        "CHANGELOG.md": "# Changelog\n\n## [1.2.3b4] - 2026-01-01\n\nTest release.\n\n## [1.2.3b3]\n\nOld.\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def test_current_source_has_one_consistent_release_version():
    metadata, notes = release.release_metadata(ROOT)
    assert metadata["tag"] == "v" + metadata["version"]
    assert "InspireELJ/mitsubishi-ae200-hass" in notes


def test_beta_release_notes_exclude_previous_versions(source_tree):
    metadata, notes = release.release_metadata(source_tree)
    assert metadata == {"version": "1.2.3b4", "tag": "v1.2.3b4", "prerelease": "true"}
    assert "Test release." in notes
    assert "Old." not in notes


def test_inconsistent_bundled_version_blocks_release(source_tree):
    (source_tree / "custom_components/ae200/_pymelans/const.py").write_text('VERSION = "1.2.3b3"')
    with pytest.raises(ValueError, match="version mismatch"):
        release.release_metadata(source_tree)


def test_missing_release_notes_block_release(source_tree):
    (source_tree / "CHANGELOG.md").write_text("# Changelog\n")
    with pytest.raises(ValueError, match="Missing changelog"):
        release.release_metadata(source_tree)


def test_changed_domain_blocks_release(source_tree):
    (source_tree / "custom_components/ae200/manifest.json").write_text(
        json.dumps({"version": "1.2.3b4", "domain": "other"})
    )
    with pytest.raises(ValueError, match="domain"):
        release.release_metadata(source_tree)


@pytest.mark.parametrize("version", ["latest", "v1.2.3", "1.2.3\nBAD=1", "../other"])
def test_non_numeric_versions_cannot_become_tag_or_action_outputs(source_tree, version):
    (source_tree / "custom_components/ae200/manifest.json").write_text(
        json.dumps({"version": version, "domain": "ae200"})
    )
    with pytest.raises(ValueError, match="numeric"):
        release.release_metadata(source_tree)
