# selftest.sh - report the shell's real behaviour as one JSON object.
# Keep in lockstep with selftest.ps1: same keys, same meanings (see ../references/pwsh-probe.md).
# Usage: shell.run {dialect: "bash", script: "bash ./scripts/selftest.sh", expects: "json"}
set -uo pipefail

json_escape() { sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\n'; }

tmp="$(mktemp -d 2>/dev/null || echo /tmp)"
enc="$(locale charmap 2>/dev/null || echo unknown)"
pipefail_on=0
if set -o | grep -q 'pipefail.*on'; then pipefail_on=1; fi

# does a failing native command inside $( ) abort the script under set -e?
capture_aborts="unknown"
if bash -c 'set -e; x=$(false); echo after' >/dev/null 2>&1; then capture_aborts="no"; else capture_aborts="yes"; fi

printf '{'
printf '"shell":"bash",'
printf '"version":"%s",' "$(bash --version 2>/dev/null | head -1 | json_escape)"
printf '"os_name":"%s",' "$(uname -s 2>/dev/null | json_escape)"
printf '"path_sep":"/",'
printf '"empty_var_prints":"%s",' "[${UNSET_MARKER_VAR}]"
printf '"null_device":"/dev/null",'
printf '"stdout_encoding":"%s",' "$enc"
printf '"redirect_is_byte_faithful":"yes",'
printf '"globstar_supported":"%s",' "$(bash -c 'shopt -s globstar && echo yes' 2>/dev/null || echo no)"
printf '"pipefail_supported":"%s",' "$( [ "$pipefail_on" = 1 ] && echo yes || echo no)"
printf '"command_subst_honours_errexit":"%s",' "$capture_aborts"
printf '"unicode_literal":"héllo → wörld",'
printf '"tmp_dir":"%s",' "$tmp"
printf '"env_marker_present":"%s",' "${SKELETONKEY_RUN:+yes}"
printf '"hostname_bytes":%d' "$(hostname 2>/dev/null | wc -c | tr -d ' ')"
printf '}\n'
rm -rf "$tmp" 2>/dev/null || true
