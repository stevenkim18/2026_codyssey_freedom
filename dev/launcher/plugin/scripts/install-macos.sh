#!/bin/bash
# Install only a trusted, checksum-pinned macOS arm64 archive into a fresh path.
set -euo pipefail
umask 077
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
fail() { printf '%s\n' 'pii-gateway installation refused.' >&2; exit 2; }
archive=''; url=''; digest=''; destination="${HOME}/Library/Application Support/AI PII Gateway"
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || fail
    case "$1" in
        --archive) [ -z "$archive" ] || fail; archive=$2 ;;
        --url) [ -z "$url" ] || fail; url=$2 ;;
        --sha256) [ -z "$digest" ] || fail; digest=$2 ;;
        --destination) destination=$2 ;;
        *) fail ;;
    esac
    shift 2
done
[ "$(/usr/bin/uname -s)" = Darwin ] && [ "$(/usr/bin/uname -m)" = arm64 ] || fail
[ -x /bin/realpath ] || fail
[[ "$digest" =~ ^[0-9a-fA-F]{64}$ ]] || fail
if [ -n "$archive" ]; then
    [ -z "$url" ] && [[ "$archive" = /* ]] && [ -f "$archive" ] || fail
else
    [[ "$url" = https://* ]] || fail
fi
[[ "$destination" = /* ]] && [ "$destination" != / ] || fail
case "$destination" in *$'\n'*|*$'\r'*|*/../*|*/..|*/./*|*/.|*/) fail ;; esac
[ ! -e "$destination" ] && [ ! -L "$destination" ] || fail
parent=$(/usr/bin/dirname "$destination")
probe=$parent
while :; do
    [ ! -L "$probe" ] || fail
    if [ -e "$probe" ]; then
        [ -d "$probe" ] || fail
        owner=$(/usr/bin/stat -f '%u' "$probe")
        mode=$(/usr/bin/stat -f '%Lp' "$probe")
        [ "$owner" = 0 ] || [ "$owner" = "$(/usr/bin/id -u)" ] || fail
        # Root-owned sticky temporary ancestors are permitted for isolated tests.
        if (( (8#$mode & 0022) != 0 )); then
            [ "$owner" = 0 ] && (( (8#$mode & 01000) != 0 )) || fail
        fi
    fi
    [ "$probe" != / ] || break
    probe=$(/usr/bin/dirname "$probe")
done
/bin/mkdir -p "$parent" 2>/dev/null || fail
stage=$(/usr/bin/mktemp -d "$parent/.pii-gateway-install.XXXXXXXX") || fail
# Only this newly created private staging directory is ever removed.
cleanup() { [ -z "${stage:-}" ] || /bin/rm -rf -- "$stage"; }
trap cleanup EXIT
trap 'exit 130' INT TERM
if [ -n "$url" ]; then
    archive="$stage/release.tar.gz"
    /usr/bin/curl --fail --silent --location --proto '=https' --proto-redir '=https' \
        --output "$archive" "$url" 2>/dev/null || fail
else
    # Snapshot first: verification and extraction must read the same private bytes.
    /bin/cp "$archive" "$stage/release.tar.gz" 2>/dev/null || fail
    archive="$stage/release.tar.gz"
fi
actual=$(/usr/bin/shasum -a 256 "$archive" | /usr/bin/awk '{print $1}')
expected=$(printf '%s' "$digest" | /usr/bin/tr 'A-F' 'a-f')
[ "$actual" = "$expected" ] || fail
/usr/bin/tar -tzf "$archive" > "$stage/members" 2>/dev/null || fail
[ -s "$stage/members" ] || fail
while IFS= read -r member; do
    case "$member" in
        pii-gateway|pii-gateway/|pii-gateway/*) ;;
        *) fail ;;
    esac
    case "$member" in /*|*/../*|*/..|*$'\r'*|*\\*) fail ;; esac
done < "$stage/members"
/bin/mkdir "$stage/extracted"
# macOS bsdtar's default secure extraction rejects traversal through symlinks.
/usr/bin/tar -xzf "$archive" -C "$stage/extracted" 2>/dev/null || fail
bundle="$stage/extracted/pii-gateway"
[ -d "$bundle" ] && [ ! -L "$bundle" ] || fail
# Keep legitimate PyInstaller relative links, but reject external/dangling links.
/usr/bin/find "$bundle" -type l -print > "$stage/links"
while IFS= read -r link; do
    target=$(/bin/realpath "$link" 2>/dev/null) || fail
    case "$target" in "$bundle"/*) ;; *) fail ;; esac
done < "$stage/links"
[ -x "$bundle/pii-gateway" ] && [ ! -L "$bundle/pii-gateway" ] || fail
version=$("$bundle/pii-gateway" version 2>/dev/null) || fail
[[ "$version" = *'"version": "0.1.0-engineering.1"'* || "$version" = *'"version":"0.1.0-engineering.1"'* ]] || fail
[ ! -e "$destination" ] && [ ! -L "$destination" ] || fail
# Same-parent stage and destination keep the final directory rename atomic.
/bin/mv -n "$bundle" "$destination" 2>/dev/null || fail
[ ! -e "$bundle" ] || fail
printf '%s\n' "$destination/pii-gateway"
