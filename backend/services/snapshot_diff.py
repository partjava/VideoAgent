MISSING = object()


def compare_states(before, after, prefix=""):
    changes = []
    for field in sorted(before.keys() | after.keys()):
        old = before.get(field, MISSING)
        new = after.get(field, MISSING)
        path = f"{prefix}.{field}" if prefix else field
        if isinstance(old, dict) and isinstance(new, dict):
            changes.extend(compare_states(old, new, path))
        elif old != new:
            changes.append({
                "field": path,
                "kind": "added" if old is MISSING else "removed" if new is MISSING else "changed",
                "before": None if old is MISSING else old,
                "after": None if new is MISSING else new,
            })
    return changes
