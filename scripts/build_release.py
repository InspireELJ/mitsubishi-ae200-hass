"""Build a reviewable HACS/manual-install archive without network access."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    component = ROOT / "custom_components" / "ae200"
    manifest = json.loads((component / "manifest.json").read_text())
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"ae200-{manifest['version']}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(component.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                z.write(p, p.relative_to(component.parent.parent))
        for relative in [
            "LICENSE",
            "docs/deployment-zh.md",
            "docs/no-java-diagnostics.md",
            "tools/ae200_readonly_browser.js",
            "tools/install_ha.sh",
        ]:
            p = ROOT / relative
            z.write(p, relative)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
    print(archive)
    print(digest)


if __name__ == "__main__":
    main()
