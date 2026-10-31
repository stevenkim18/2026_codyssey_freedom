"""Read-only deployment-filesystem diagnosis; never repairs state or controls services."""
import argparse
import json
import os
from pathlib import Path
import re

from .remote import checked_release


_ACTIONS = {
    "consistent": ["VERIFY_SERVICE_BEFORE_USE"],
    "no_installation": ["PLAN_NEW_DEPLOYMENT"],
    "incomplete_staged": ["PRESERVE_STAGED_RELEASE", "REVIEW_INTERRUPTED_DEPLOYMENT"],
    "staged_unreferenced": ["PRESERVE_STAGED_RELEASE", "VERIFY_CURRENT_SERVICE", "REVIEW_INTERRUPTED_DEPLOYMENT"],
    "state_ahead_of_current": ["PRESERVE_BOTH_RELEASES", "VERIFY_CURRENT_SERVICE", "REVIEW_STATE_POINTER_MISMATCH"],
    "state_current_mismatch": ["PRESERVE_BOTH_RELEASES", "REVIEW_STATE_POINTER_MISMATCH"],
    "previous_missing": ["PRESERVE_CURRENT_RELEASE", "RESTORE_TRUSTED_PREVIOUS_ARTIFACT_OR_REPLAN"],
    "no_active_release": ["PRESERVE_FAILED_RELEASE", "REVIEW_FAILED_DEPLOYMENT"],
}


def _report(classification, *, valid=0, partial=0):
    return {"schema_version":"1.0", "classification":classification,
            "actions":_ACTIONS.get(classification, ["STOP_MUTATIONS", "REVIEW_TRUSTED_DEPLOYMENT_RECORDS"]),
            "valid_release_count":valid, "incomplete_release_count":partial,
            "service_verified":False, "mutations_performed":False,
            "scope":"filesystem_snapshot_only"}


def _json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 1024*1024:
        raise ValueError
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError
            result[key]=value
        return result
    result=json.loads(path.read_text(),object_pairs_hook=unique)
    if not isinstance(result,dict):
        raise ValueError
    return result


def _pointer(value, root):
    if value is None:
        return None
    if not isinstance(value,str):
        raise ValueError
    path=Path(value)
    if (not path.is_absolute() or path.parent != root / "releases"
            or str(path) != value or not re.fullmatch(r"[a-f0-9]{64}",path.name)):
        raise ValueError
    return path.name


def inspect_recovery(root, *, role):
    """Return fixed codes from a quiescent live tree or exact-path filesystem mirror.

    This is not an authenticated manifest, a service-health check, a lock, or a
    power-loss durability proof. Concurrent external writes may invalidate the
    snapshot; stop deployment writers before collecting it. No path or raw
    metadata/exception values are included in the returned report.
    """
    try:
        root=Path(root)
        if (role not in {"gpu","general"} or not root.is_absolute() or ".." in root.parts
                or any(path.is_symlink() for path in [root,*root.parents])):
            return _report("untrusted_root")
        if not root.exists():
            return _report("no_installation")
        if not root.is_dir():
            return _report("untrusted_root")
        marker=root / "managed.json"
        if not marker.exists() and not marker.is_symlink():
            return _report("unmanaged_root")
        try:
            if _json(marker) != {"manager":"codyssey-pii-v1","role":role}:
                raise ValueError
        except Exception:
            return _report("untrusted_marker")
        releases=root / "releases"
        if releases.is_symlink() or releases.exists() and not releases.is_dir():
            return _report("untrusted_release_tree")
        inventory={}
        if releases.exists():
            for item in releases.iterdir():
                if len(inventory) >= 1000:
                    return _report("diagnostic_limit")
                if item.is_symlink() or not item.is_dir() or not re.fullmatch(r"[a-f0-9]{64}",item.name):
                    return _report("untrusted_release_tree")
                metadata=item / "deployment-manifest.json"
                if not metadata.exists() and not metadata.is_symlink():
                    inventory[item.name]="partial"
                else:
                    try:
                        checked_release(root,str(item),role)
                        inventory[item.name]="valid"
                    except Exception:
                        inventory[item.name]="invalid"
        valid=sum(value == "valid" for value in inventory.values())
        partial=sum(value == "partial" for value in inventory.values())
        def report(code): return _report(code,valid=valid,partial=partial)
        state_path=root / "state.json"
        state=None
        if state_path.exists() or state_path.is_symlink():
            try:
                state=_json(state_path)
                if not {"current","previous"} <= set(state) or set(state)-{"current","previous","failed"}:
                    raise ValueError
                state={key:_pointer(value,root) for key,value in state.items()}
            except Exception:
                return report("untrusted_state")
        current=root / "current"
        actual=None
        if current.is_symlink():
            try:
                actual=_pointer(os.readlink(current),root)
            except Exception:
                return report("current_unknown")
            if actual not in inventory:
                return report("current_missing")
            if inventory[actual] != "valid":
                return report("untrusted_manifest")
        elif current.exists():
            return report("current_unknown")
        if state is not None:
            failed=state.get("failed")
            if failed is not None and failed not in inventory:
                return report("failed_release_missing")
            previous=state["previous"]
            if previous is not None:
                if previous not in inventory:
                    return report("previous_missing")
                if inventory[previous] != "valid":
                    return report("untrusted_manifest")
            declared=state["current"]
            if declared is not None:
                if declared not in inventory:
                    return report("state_target_missing")
                if inventory[declared] != "valid":
                    return report("untrusted_manifest")
            if declared != actual:
                return report("state_ahead_of_current" if declared is not None and actual == previous else "state_current_mismatch")
        elif actual is not None:
            return report("state_missing")
        if any(value == "invalid" for value in inventory.values()):
            return report("untrusted_manifest")
        if partial:
            return report("incomplete_staged")
        referenced={actual}
        if state:
            referenced.update(state.values())
        if set(inventory)-referenced:
            return report("staged_unreferenced")
        if actual is None:
            return report("no_active_release" if state and state.get("failed") else "incomplete_staged")
        return report("consistent")
    except Exception:
        return _report("diagnostic_unavailable")


class _Parser(argparse.ArgumentParser):
    def error(self,message):
        raise ValueError


def main(argv=None):
    parser=_Parser(description="Read-only managed deployment filesystem recovery report; no SSH or repair")
    parser.add_argument("--root",required=True)
    parser.add_argument("--role",required=True,choices=("gpu","general"))
    try:
        args=parser.parse_args(argv)
        result=inspect_recovery(args.root,role=args.role)
    except Exception:
        result=_report("invalid_diagnostic_arguments")
    print(json.dumps(result,sort_keys=True))
    return 0 if result["classification"] == "consistent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
