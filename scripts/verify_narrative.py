import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

phrases = [
    "Model intent unchanged",
    "Preserved utility",
    "Clean halt",
    "Interception Guaranteed",
    "Attack Prevented",
    "Zero funds lost",
    "100% BLOCKED",
    "Funds Drained",
]

files = [
    Path("scripts/generate_eval_tables.py"),
    Path("eval/RESULTS.md"),
]

hits = []
for f in files:
    if f.exists():
        text = f.read_text(encoding="utf-8")
        for p in phrases:
            matches = list(re.finditer(re.escape(p), text, re.IGNORECASE))
            for m in matches:
                # Find line
                lineno = text.count("\n", 0, m.start()) + 1
                hits.append(f"{f}:{lineno}: {p}")

out_path = Path("evidence/narrative_grep.txt")
out_path.parent.mkdir(parents=True, exist_ok=True)
if hits:
    out_path.write_text("\n".join(hits) + "\n", encoding="utf-8")
    print(f"FAILED: Found {len(hits)} narrative phrase occurrences:\n" + "\n".join(hits))
    sys.exit(1)
else:
    proof = "Zero narrative phrase hits found across generate_eval_tables.py and eval/RESULTS.md.\n"
    for p in phrases:
        proof += f"  - '{p}': 0 hits\n"
    out_path.write_text(proof, encoding="utf-8")
    print(proof)
