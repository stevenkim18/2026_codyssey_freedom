#!/bin/sh
# Codex lets a prompt through when a hook crashes, so every abnormal guard exit
# is converted here into exit 2, the documented blocking result.
mode=${1:-guard}
directory=$(/usr/bin/dirname -- "$0")
/usr/bin/python3 -I -S -B "$directory/main.py" "$mode"
status=$?
[ "$mode" = guard ] || exit 0
case "$status" in
    0|2) exit "$status" ;;
esac
printf '%s\n' 'PII 검사를 실행하지 못해 전송을 멈췄습니다.' >&2
exit 2
