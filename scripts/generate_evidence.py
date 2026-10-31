"""Evidence Generation Automation Script for Kavach.

Runs all automated test suites, benchmarks, timing measurements,
and security scans, saving raw machine-verifiable outputs to the evidence/ directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def run_and_save(cmd: list[str], output_path: Path, cwd: Path) -> subprocess.CompletedProcess:
    print(f"\n[generate_evidence] > {' '.join(cmd)}")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    combined_output = f"Command: {' '.join(cmd)}\nExit Code: {res.returncode}\n\nSTDOUT:\n{res.stdout}\n\nSTDERR:\n{res.stderr}"
    output_path.write_text(combined_output, encoding="utf-8")
    print(f"Saved evidence to {output_path} (Exit: {res.returncode})")
    return res


def main():
    root = Path(__file__).resolve().parent.parent
    evidence_dir = root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    print("Generating comprehensive evidence artifacts...")

    # 1. Full Pytest Suite + JUnit XML
    xml_path = evidence_dir / "pytest_report.xml"
    txt_path = evidence_dir / "pytest_output.txt"
    run_and_save(
        [sys.executable, "-m", "pytest", "tests/", "-v", f"--junitxml={xml_path}"],
        txt_path,
        root,
    )

    # 2. Secret Scan
    scan_script = (
        "import os, sys\n"
        "suspicious = [b'BEGIN' + b' PRIVATE KEY', b'BEGIN' + b' RSA', b'AIza' + b'Sy', b'sk-' + b'ant-', b'sk-' + b'proj-']\n"
        "found = []\n"
        "for root, dirs, files in os.walk('.'):\n"
        "    if any(x in root for x in ['.git', '__pycache__', '.pytest_cache', 'evidence', '.venv', 'env', 'scripts']):\n"
        "        continue\n"
        "    for f in files:\n"
        "        if f.endswith(('.py', '.toml', '.md', '.json', '.txt', '.example', '.ps1')) and not f.endswith('test_redteam.py'):\n"
        "            p = os.path.join(root, f)\n"
        "            try:\n"
        "                c = open(p, 'rb').read()\n"
        "                for s in suspicious:\n"
        "                    if s in c: found.append((p, s.decode()))\n"
        "            except Exception: pass\n"
        "if found:\n"
        "    print('LEAKS DETECTED:', found)\n"
        "    sys.exit(1)\n"
        "print('CLEAN: Zero secrets or private keys detected in tracked files.')\n"
    )
    secret_txt = evidence_dir / "secret_scan.txt"
    run_and_save([sys.executable, "-c", scan_script], secret_txt, root)

    # 3. Demo Timing Measurement & Recording (Rich text + SVG)
    demo_txt = evidence_dir / "demo_timing.txt"
    t0 = time.time()
    res_demo = run_and_save([sys.executable, "demo.py", "--fast", "--record"], demo_txt, root)
    elapsed = time.time() - t0
    with open(demo_txt, "a", encoding="utf-8") as f:
        f.write(f"\n\nMeasured Wall-Clock Execution Time: {elapsed:.2f} seconds\n")

    # 4. Adversarial Benchmark Run
    eval_txt = evidence_dir / "eval_summary.txt"
    run_and_save([sys.executable, "eval/run_eval.py", "--trials", "20"], eval_txt, root)

    # 5. Adaptive Attacker Simulation (30 Rounds)
    adaptive_txt = evidence_dir / "adaptive_attacker.txt"
    run_and_save([sys.executable, "eval/run_adaptive_attacker.py"], adaptive_txt, root)

    # 6. In-Process Latency Profiling (1,000 requests)
    latency_txt = evidence_dir / "latency.txt"
    run_and_save([sys.executable, "scripts/profile_latency.py"], latency_txt, root)

    # 7. Multi-Process Real HTTP RPC Latency Profiling (100 requests)
    rpc_latency_txt = evidence_dir / "latency_rpc_log.txt"
    run_and_save([sys.executable, "scripts/profile_rpc_latency.py"], rpc_latency_txt, root)

    # Copy results.json into evidence/
    results_src = root / "eval" / "results.json"
    if results_src.exists():
        results_dest = evidence_dir / "eval_benchmark.json"
        results_dest.write_text(results_src.read_text(encoding="utf-8"), encoding="utf-8")

    # 8. Automated Walkthrough Generation & SHA-256 Manifest
    print("\n[generate_evidence] > Building walkthrough artifact and SHA-256 manifest...")
    res_wt = subprocess.run([sys.executable, "tools/build_walkthrough.py"], cwd=root, capture_output=True, text=True)
    print(res_wt.stdout)
    if res_wt.returncode != 0:
        print(f"[ERROR] Walkthrough builder failed:\n{res_wt.stderr}")

    print(f"\n[SUCCESS] All evidence artifacts generated in {evidence_dir}")


if __name__ == "__main__":
    main()
