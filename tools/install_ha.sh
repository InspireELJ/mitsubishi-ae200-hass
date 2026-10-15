#!/bin/sh
# Reversible file installation only. Does not restart HA or change its registry.
set -eu

source_dir=${1:?Usage: sh tools/install_ha.sh /path/to/custom_components/ae200}
config_dir=${AE200_HA_CONFIG_DIR:-/config}
target_dir=$config_dir/custom_components/ae200
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
backup_dir=$config_dir/ae200-backups/$stamp
stage_dir=$config_dir/custom_components/.ae200-stage-$stamp

test -d "$config_dir" || { echo "HA configuration directory does not exist" >&2; exit 1; }
test -f "$source_dir/manifest.json" || { echo "Source manifest.json missing" >&2; exit 1; }
test -f "$source_dir/__init__.py" || { echo "Source __init__.py missing" >&2; exit 1; }
# Older versions can be restored even if they did not have the bundled library.
test "$(cd "$source_dir" && pwd -P)" != "$(cd "$config_dir" && pwd -P)/custom_components/ae200" || {
    echo "Source must be outside the installed component directory" >&2; exit 1;
}
mkdir -p "$config_dir/custom_components" "$backup_dir"
cp -R "$source_dir" "$stage_dir"
if test -d "$target_dir"; then
    mv "$target_dir" "$backup_dir/ae200"
fi
if ! mv "$stage_dir" "$target_dir"; then
    if test -d "$backup_dir/ae200"; then mv "$backup_dir/ae200" "$target_dir"; fi
    echo "Installation failed; original files restored" >&2
    exit 1
fi
echo "Installed files: $target_dir"
echo "Previous files: $backup_dir/ae200 (if an earlier installation existed)"
echo "Next: ha core check; then restart HA during your maintenance window."
echo "Rollback: run this script with the Previous files directory as its source."
