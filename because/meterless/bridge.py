"""Stateless JSON bridge so the official Meterless TypeScript runners can drive
the Python engines unmodified.

    python -m because.meterless.bridge hmem <state.json> <method> '<json args>' <now_ms>
    python -m because.meterless.bridge scout <registry.json> decide '<json input>'

State is loaded from and written back to <state.json> on every call, so a
synchronous TS adapter (execFileSync) can hold an HMEM "instance" per file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _hmem(state_path: str, method: str, args: list, now: float) -> object:
    from because.meterless.hmem import HMEM

    clock_box = {"now": now}
    h = HMEM(clock=lambda: clock_box["now"])
    p = Path(state_path)
    if p.exists() and p.stat().st_size:
        h.load_state(json.loads(p.read_text()))
    target, _, name = method.rpartition(".")
    obj = getattr(h, target) if target else h
    if method == "store.set":
        rid, key, value = args
        h.store.get(rid)[key] = value
        out = True
    elif method == "conflicts.autoResolve":
        out = h.conflicts.autoResolve(args[0])
    else:
        out = getattr(obj, name)(*args)
    p.write_text(json.dumps(h.state()))
    return out


def _scout(registry: str, method: str, args: list) -> object:
    from because.meterless.scout import Scout

    reg_path, _, packs = registry.partition("::")
    s = Scout(intentRegistry=reg_path, policyPack=packs.split(",") if packs else "default")
    return s.decide_sync(args if isinstance(args, dict) else args[0])


def main() -> int:
    engine = sys.argv[1]
    if engine == "hmem":
        out = _hmem(sys.argv[2], sys.argv[3], json.loads(sys.argv[4]), float(sys.argv[5]))
    elif engine == "scout":
        out = _scout(sys.argv[2], sys.argv[3], json.loads(sys.argv[4]))
    else:
        raise SystemExit(f"unknown engine {engine}")
    sys.stdout.write(json.dumps({"ok": True, "result": out}, default=str))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # surfaced to the TS side as a thrown Error
        sys.stdout.write(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        sys.exit(0)
