"""Fail if the code uses a name that is never defined or imported, apart from the ones
upstream already has (listed in undefined_names_baseline.txt).

A name that does not exist only fails when its line runs, for example a helper upstream
renamed after a change was written. Upstream's own code has a few (mostly forward
references in SQLAlchemy annotations), so this compares with that list instead of
expecting none.

    python check_undefined_names.py              # check
    python check_undefined_names.py --update     # rewrite the baseline (after a sync with upstream)
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "undefined_names_baseline.txt"
PATTERN = re.compile(r"^(?P<file>[^:]+):\d+:\d+: undefined name '(?P<name>[^']+)'")


def findings() -> list[str]:
    out = subprocess.run([sys.executable, "-m", "pyflakes", "."], cwd=HERE, capture_output=True, text=True).stdout
    found = []
    for line in out.splitlines():
        m = PATTERN.match(line)
        if m:
            found.append(f"{m['file'].removeprefix('./')} {m['name']}")
    return sorted(found)


def main() -> int:
    now = findings()
    if "--update" in sys.argv:
        BASELINE.write_text("\n".join(now) + "\n")
        print(f"wrote {len(now)} entries to {BASELINE.name}")
        return 0
    known = BASELINE.read_text().split("\n") if BASELINE.exists() else []
    remaining = list(known)
    new = []
    for item in now:
        if item in remaining:
            remaining.remove(item)
        else:
            new.append(item)
    if new:
        print("Undefined names that upstream does not have:")
        for item in new:
            print("  " + item)
        return 1
    print(f"no new undefined names ({len(now)} already in upstream)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
