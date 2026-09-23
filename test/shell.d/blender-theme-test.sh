#!/bin/bash

set -euo pipefail

# omarchy-theme-set-blender keeps a startup module in every Blender version's config, and
# that module is what applies the generated blender.json. The install side runs against a
# throwaway HOME with Blender's presence and the opt-out toggle stubbed. When Blender itself
# is available, every stock theme's rendered blender.json is also applied inside it, so a
# template path that a Blender release renamed or retyped fails here instead of silently
# leaving part of the UI unthemed.

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"

test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT

fake_bin="$test_tmp/bin"
home="$test_tmp/home"
mkdir -p "$fake_bin" "$home/.config/blender/5.1" "$home/.config/blender/5.2/scripts/startup"

cat >"$fake_bin/omarchy-cmd-present" <<'SH'
#!/bin/bash
[[ $1 == "blender" && ${OMARCHY_TEST_BLENDER_INSTALLED:-1} == "1" ]]
SH

cat >"$fake_bin/omarchy-toggle-enabled" <<'SH'
#!/bin/bash
[[ $1 == "skip-blender-theme-changes" && ${OMARCHY_TEST_SKIP_BLENDER:-0} == "1" ]]
SH

chmod +x "$fake_bin"/*

sync_blender() {
  PATH="$fake_bin:$ROOT/bin:$PATH" HOME="$home" OMARCHY_PATH="$ROOT" "$ROOT/bin/omarchy-theme-set-blender"
}

installed_modules() {
  find "$home/.config/blender" -name omarchy_theme.py | sort
}

OMARCHY_TEST_BLENDER_INSTALLED=0 sync_blender
[[ -z $(installed_modules) ]] || fail "nothing is installed when Blender is absent"
pass "nothing is installed when Blender is absent"

printf 'stale\n' >"$home/.config/blender/5.2/scripts/startup/omarchy_theme.py"
sync_blender
for version in 5.1 5.2; do
  cmp -s "$ROOT/default/blender/omarchy_theme.py" "$home/.config/blender/$version/scripts/startup/omarchy_theme.py" ||
    fail "the current module is installed for Blender $version"
done
pass "every Blender version gets the current module, including one never started with it"

OMARCHY_TEST_SKIP_BLENDER=1 sync_blender
[[ -z $(installed_modules) ]] || fail "opting out removes the module" "$(installed_modules)"
pass "opting out removes the module so Blender stops syncing"

if ! command -v blender >/dev/null; then
  skip "stock themes apply cleanly inside Blender"
  exit 0
fi

rendered="$test_tmp/rendered"
mkdir -p "$rendered"
for colors in "$ROOT"/themes/*/colors.toml; do
  theme=$(basename "$(dirname "$colors")")
  next_theme="$home/.local/state/omarchy/current/next-theme"
  rm -rf "$next_theme"
  mkdir -p "$next_theme"
  cp "$colors" "$next_theme/colors.toml"
  HOME="$home" OMARCHY_PATH="$ROOT" PATH="$ROOT/bin:$PATH" "$ROOT/bin/omarchy-theme-set-templates"
  cp "$next_theme/blender.json" "$rendered/$theme.json"
done

cat >"$test_tmp/apply.py" <<'PY'
import logging
import sys
from pathlib import Path

module_dir, rendered_dir = sys.argv[sys.argv.index("--") + 1:]
sys.path.insert(0, module_dir)
import omarchy_theme

problems = []
handler = logging.Handler(logging.WARNING)
handler.emit = lambda record: problems.append(record.getMessage())
logging.getLogger("omarchy_theme").addHandler(handler)

for theme_file in sorted(Path(rendered_dir).glob("*.json")):
    try:
        omarchy_theme.apply_theme(theme_file)
    except Exception as error:
        problems.append(f"{theme_file.stem}: {error}")

for problem in problems:
    print(problem, file=sys.stderr)
sys.exit(1 if problems else 0)
PY

blender --background --factory-startup --python-exit-code 1 \
  --python "$test_tmp/apply.py" -- "$ROOT/default/blender" "$rendered" >"$test_tmp/blender.log" 2>&1 ||
  fail "stock themes apply cleanly inside Blender" "$(grep -F '[Omarchy]' "$test_tmp/blender.log" || tail -20 "$test_tmp/blender.log")"
pass "stock themes apply cleanly inside Blender"
