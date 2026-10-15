"""Validate the versioned HACS source and prepare release notes without publishing."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "InspireELJ/mitsubishi-ae200-hass"


def source_version(path: Path) -> str:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError(f"Missing VERSION in {path}")


def release_metadata(root: Path) -> tuple[dict[str, str], str]:
    component = root / "custom_components/ae200"
    manifest = json.loads((component / "manifest.json").read_text())
    version = manifest["version"]
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:b\d+)?", version):
        raise ValueError("Use a numeric stable or beta version, for example 0.2.0b2")
    versions = {
        "HA manifest": version,
        "protocol package": tomllib.loads((root / "pymelans/pyproject.toml").read_text())["project"][
            "version"
        ],
        "canonical source": source_version(root / "pymelans/pymelans/const.py"),
        "HA bundled source": source_version(component / "_pymelans/const.py"),
    }
    if len(set(versions.values())) != 1:
        raise ValueError(f"Release version mismatch: {versions}")
    if manifest["domain"] != "ae200":
        raise ValueError("The existing ae200 integration domain must be preserved")
    changelog = (root / "CHANGELOG.md").read_text()
    section = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    if section is None or not section[1].strip():
        raise ValueError(f"Missing changelog section for {version}")
    metadata = {
        "version": version,
        "tag": f"v{version}",
        "prerelease": "true" if "b" in version else "false",
    }
    guide = (
        f"https://github.com/{REPOSITORY}/blob/{metadata['tag']}/docs/hacs-deployment-zh.md"
    )
    notes = (
        section[1].strip()
        + f"\n\n[HACS installation, migration and rollback]({guide})\n\n"
        + "HACS downloads the integration from this release's source tag. "
        + "The ZIP asset is an alternative for manual installation.\n"
    )
    return metadata, notes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata, notes = release_metadata(ROOT)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "notes.md").write_text(notes)
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            for key, value in metadata.items():
                stream.write(f"{key}={value}\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
