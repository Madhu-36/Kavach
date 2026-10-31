"""Fresh-clone end-to-end verification script.

Creates a fresh temporary directory, clones the repository, installs into a fresh venv,
runs the full test suite and interactive demo, and logs output to evidence/fresh_clone.txt.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def log_and_run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    print(f"[fresh_clone_test] > {' '.join(cmd)} (cwd={cwd})")
    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        print(f"Error output:\n{res.stderr}\n{res.stdout}")
        raise RuntimeError(f"Command failed with code {res.returncode}: {' '.join(cmd)}")
    return res


def main():
    repo_root = Path(__file__).resolve().parent.parent
    evidence_dir = repo_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    log_file = evidence_dir / "fresh_clone.txt"

    temp_dir = Path(tempfile.mkdtemp(prefix="kavach_fresh_clone_"))
    print(f"Running fresh clone test in temporary directory: {temp_dir}")

    log_lines = []
    try:
        # 1. Real git clone of repository
        dest_repo = temp_dir / "Kavach"
        r0 = log_and_run(["git", "clone", str(repo_root), str(dest_repo)], cwd=temp_dir)
        log_lines.append(f"[OK] git clone succeeded:\n{r0.stdout}\n{r0.stderr}")

        # 2. Create fresh virtualenv
        venv_dir = temp_dir / "venv"
        r1 = log_and_run([sys.executable, "-m", "venv", str(venv_dir)], cwd=temp_dir)
        log_lines.append(f"[OK] Created isolated virtualenv at {venv_dir}")

        # Determine venv python
        if sys.platform == "win32":
            venv_python = venv_dir / "Scripts" / "python.exe"
        else:
            venv_python = venv_dir / "bin" / "python"

        # 3. Install in editable mode
        r2 = log_and_run([str(venv_python), "-m", "pip", "install", "-e", ".[dev]"], cwd=dest_repo)
        log_lines.append(f"[OK] pip install -e .[dev] succeeded")

        # 4. Run pytest suite
        r3 = log_and_run([str(venv_python), "-m", "pytest", "tests/", "-v"], cwd=dest_repo)
        log_lines.append(f"[OK] Pytest passed:\n{r3.stdout}")

        # 5. Run demo
        r4 = log_and_run([str(venv_python), "demo.py", "--fast"], cwd=dest_repo)
        log_lines.append(f"[OK] Demo passed:\n{r4.stdout}")

        log_lines.append("\n==========================================")
        log_lines.append("FRESH CLONE VERIFICATION: PASSED (100% REPRODUCIBLE)")
        log_lines.append("==========================================")
        print("\n".join(log_lines))

    finally:
        # Write evidence file
        log_file.write_text("\n".join(log_lines), encoding="utf-8")
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"Cleaned up {temp_dir}. Proof saved to {log_file}")


if __name__ == "__main__":
    main()
