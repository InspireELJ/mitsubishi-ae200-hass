"""Generate the self-contained HACS library from the canonical protocol source.

Run with --check in CI. Never edit custom_components/ae200/_pymelans directly.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pymelans" / "pymelans"
DESTINATION = ROOT / "custom_components" / "ae200" / "_pymelans"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = {p.name: p.read_bytes() for p in SOURCE.glob("*.py")}
    if args.check:
        existing = {p.name: p.read_bytes() for p in DESTINATION.glob("*.py")}
        if files != existing:
            raise SystemExit("Bundled library is out of date. Run scripts/sync_ha_library.py")
        print("Bundled library matches canonical source")
        return
    DESTINATION.mkdir(exist_ok=True)
    for old in DESTINATION.glob("*.py"):
        if old.name not in files:
            old.unlink()
    for name in files:
        shutil.copyfile(SOURCE / name, DESTINATION / name)
    print(f"Bundled {len(files)} modules")


if __name__ == "__main__":
    main()
