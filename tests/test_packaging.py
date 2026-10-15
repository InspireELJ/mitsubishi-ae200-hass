"""Standalone HA bundle and reversible installer contract tests."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bundled_library_is_identical_to_canonical_source():
    subprocess.run(
        ["python3", str(ROOT / "scripts/sync_ha_library.py"), "--check"],
        check=True,
        capture_output=True,
    )


def test_ha_bundle_imports_without_an_external_pymelans_install():
    source = ROOT / "custom_components/ae200/_pymelans/__init__.py"
    spec = importlib.util.spec_from_file_location(
        "inspire_packaging_test", source, submodule_search_locations=[str(source.parent)]
    )
    import sys

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        manifest = json.loads((ROOT / "custom_components/ae200/manifest.json").read_text())
        assert module.__version__ == manifest["version"]
        assert module.MelansDevice("1", None, {}).is_on is None
    finally:
        for name in list(sys.modules):
            if name == spec.name or name.startswith(spec.name + "."):
                sys.modules.pop(name)


def test_manifest_keeps_domain_and_does_not_require_unpublished_library():
    manifest = json.loads((ROOT / "custom_components/ae200/manifest.json").read_text())
    assert manifest["domain"] == "ae200"
    assert not any(req.startswith("pymelans") for req in manifest["requirements"])


def test_installer_and_rollback_preserve_registry(tmp_path):
    config = tmp_path / "config"
    existing = config / "custom_components/ae200"
    existing.mkdir(parents=True)
    (existing / "manifest.json").write_text('{"version":"old"}')
    (existing / "__init__.py").write_text("OLD = True\n")
    registry = config / ".storage/entity_registry"
    registry.parent.mkdir()
    registry.write_text("unchanged-private-registry")
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "manifest.json").write_text('{"version":"new"}')
    (incoming / "__init__.py").write_text("NEW = True\n")
    env = {**os.environ, "AE200_HA_CONFIG_DIR": str(config)}
    command = ["sh", str(ROOT / "tools/install_ha.sh")]
    subprocess.run([*command, str(incoming)], env=env, check=True, capture_output=True)
    assert "NEW" in (existing / "__init__.py").read_text()
    backups = list((config / "ae200-backups").glob("*/ae200"))
    assert len(backups) == 1
    subprocess.run([*command, str(backups[0])], env=env, check=True, capture_output=True)
    assert "OLD" in (existing / "__init__.py").read_text()
    assert registry.read_text() == "unchanged-private-registry"


def test_invalid_source_leaves_installation_untouched(tmp_path):
    config = tmp_path / "config"
    existing = config / "custom_components/ae200"
    existing.mkdir(parents=True)
    (existing / "__init__.py").write_text("original")
    result = subprocess.run(
        ["sh", str(ROOT / "tools/install_ha.sh"), str(tmp_path / "missing")],
        env={**os.environ, "AE200_HA_CONFIG_DIR": str(config)},
        capture_output=True,
    )
    assert result.returncode != 0
    assert (existing / "__init__.py").read_text() == "original"
