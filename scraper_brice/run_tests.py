"""Run every offline test in scraper_brice/, each in its own process, and summarise.

test_classifications.py is excluded: it makes live Claude calls. Everything
else is offline (each file blocks the network before importing anything).

Run:  cd scraper_brice && python -X utf8 run_tests.py
Exit 1 if any file fails.
"""

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = {"test_classifications.py"}


def main() -> int:
    files = sorted(p for p in HERE.glob("test_*.py") if p.name not in LIVE)
    failed_files = 0
    passed = failed = 0
    for path in files:
        proc = subprocess.run([sys.executable, "-X", "utf8", "-B", path.name], cwd=HERE, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        out = proc.stdout
        m = re.search(r"(\d+) passed, (\d+) failed", out)
        if m:
            passed += int(m.group(1))
            failed += int(m.group(2))
            summary = m.group(0)
        elif out.strip().startswith("SKIP"):
            summary = out.strip().splitlines()[0]
        else:
            summary = f"exit {proc.returncode}, no summary line"
        status = "ok  " if proc.returncode == 0 else "FAIL"
        print(f"{status}  {path.name:28} {summary}")
        if proc.returncode != 0:
            failed_files += 1
            for line in out.splitlines():
                if line.startswith("  FAIL"):
                    print(f"        {line.strip()}")
            if not m and proc.stderr.strip():
                print("        " + proc.stderr.strip().splitlines()[-1])
    print(f"\n{len(files)} files, {len(files) - failed_files} ok, {failed_files} failed | "
          f"{passed} checks passed, {failed} failed")
    return 1 if failed_files else 0


if __name__ == "__main__":
    sys.exit(main())
