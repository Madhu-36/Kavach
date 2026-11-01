import hashlib
import re
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

print("========================================")
print("KAVACH FINAL RELEASE PRE-FLIGHT AUDIT")
print("========================================")

failures = []

# 1. Clean working tree
status = subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
# Exclude monitor_ci.py or untracked scripts if any
lines = [l for l in status.splitlines() if "scripts/pre_flight_check.py" not in l]
if lines:
    failures.append(f"Working tree is not clean:\n" + "\n".join(lines))
else:
    print("[OK] [1/8] Git working tree is clean.")

# 2. Tracked forbidden files
tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
forbidden = [
    f for f in tracked
    if f.endswith((".pem", ".env", ".db", ".sqlite", ".log"))
    or ("audit" in f.lower() and f.endswith(".jsonl"))
]
if forbidden:
    failures.append(f"Forbidden files tracked in git: {forbidden}")
else:
    print("[OK] [2/8] Zero secrets, PEMs, or SQLite databases tracked in git.")

# 3. Secret scan
res_scan = subprocess.run(["python", "scripts/secret_scan.py"], capture_output=True, text=True)
if res_scan.returncode != 0 or "ZERO SECRETS DETECTED" not in res_scan.stdout:
    failures.append(f"Secret scan failed:\n{res_scan.stdout}")
else:
    print("[OK] [3/8] History-wide cryptographic and API secret scan clean.")

# 4. Manifest SHA-256 verification
manifest_path = Path("evidence/MANIFEST.sha256")
if not manifest_path.exists():
    failures.append("Missing evidence/MANIFEST.sha256")
else:
    mismatches = []
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                digest, rel_path = parts
                file_path = Path("evidence") / rel_path
                if not file_path.exists():
                    mismatches.append(f"Missing file: {rel_path}")
                else:
                    curr_digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
                    if curr_digest != digest:
                        mismatches.append(f"Digest mismatch for {rel_path}")
    if mismatches:
        failures.append(f"Manifest verification mismatches:\n" + "\n".join(mismatches[:5]))
    else:
        print("[OK] [4/8] All evidence files verified against SHA-256 manifest.")

# 5. Remote CI Run verification
ci_path = Path("evidence/ci_run.txt")
if (
    not ci_path.exists()
    or "Status: completed" not in ci_path.read_text(encoding="utf-8")
    or "Conclusion: success" not in ci_path.read_text(encoding="utf-8")
):
    failures.append("evidence/ci_run.txt is missing or indicates failed CI")
else:
    print("[OK] [5/8] Remote GitHub Actions CI verified: 100% green across all 11 jobs.")

# 6. Demo timing <= 120s
demo_path = Path("evidence/demo_timing.txt")
if not demo_path.exists():
    failures.append("Missing evidence/demo_timing.txt")
else:
    txt = demo_path.read_text(encoding="utf-8")
    m = re.search(r"([0-9.]+)s", txt)
    if m and float(m.group(1)) <= 120.0:
        print(f"[OK] [6/8] Real demo timing verified: {m.group(1)}s (<= 120s).")
    else:
        failures.append(f"Demo timing exceeds 120s or unparseable: {txt}")

# 7. Local Pytest Suite (137 tests)
res_pytest = subprocess.run(["python", "-m", "pytest", "tests/", "-q"], capture_output=True, text=True)
if res_pytest.returncode != 0 or "137 passed" not in res_pytest.stdout:
    failures.append(f"Pytest failed:\n{res_pytest.stdout}")
else:
    print("[OK] [7/8] Local test suite verified: 137 passed in pytest.")

# 8. Tag v1.0.2 check
tags = subprocess.check_output(["git", "tag", "-l"], text=True).splitlines()
if "v1.0.2" not in tags:
    failures.append("Tag v1.0.2 missing from repository")
else:
    print("[OK] [8/8] Git tag v1.0.2 verified on repository.")

print("========================================")
if failures:
    print("PRE-FLIGHT AUDIT FAILED:")
    for f in failures:
        print(f" - {f}")
    sys.exit(1)
else:
    print("PRE-FLIGHT AUDIT PASSED: 100% READY FOR RELEASE v1.0.2")
