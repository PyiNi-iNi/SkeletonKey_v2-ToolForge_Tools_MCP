# selftest.sh - report the shell's real behaviour as one JSON object.
# Keep in lockstep with selftest.ps1: same keys, same meanings - except strict_mode_default,
# which has no POSIX analogue (see ../references/pwsh-probe.md).
# Executed by the `shell.selftest` tool (tool.toml inlines this file into the payload); the
# manual equivalent is shell.run {dialect: "bash", script: "<this file>", expects: "json"}
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
# the probe that broke when this file got an executor: under `set -u` an unset variable is not
# printed, it aborts the script - so ask the question twice and report both answers
empty_prints="$(bash -c 'printf "%s" "[${UNSET_MARKER_VAR:-}]"' 2>/dev/null || echo 'aborts')"
set_u_aborts="no"
if ! bash -c 'set -u; printf "%s" "${UNSET_MARKER_VAR}"' >/dev/null 2>&1; then set_u_aborts="yes"; fi
printf '"empty_var_prints":"%s",' "$empty_prints"
printf '"unset_var_under_set_u":"%s",' "$set_u_aborts"
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
