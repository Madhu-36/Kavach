import json
from collections import defaultdict
from pathlib import Path

log_path = Path("evidence/adaptive/llm_attacker_log.jsonl")
lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]

round_names = {
    1: "V1: Payee Substitution & Lookalikes",
    2: "V2: Allowed-Payee Overspend",
    3: "V3: Count Exhaustion",
    4: "V4: Budget Exhaustion",
    5: "V5: Replay Attacks",
    6: "V6: Argument Smuggling & Type Tampering",
    7: "V7: Category Mismatch",
}

matrix = defaultdict(lambda: defaultdict(int))
outcomes = set()

for r in lines:
    round_idx = r.get("round", 0)
    vec_name = round_names.get(round_idx, f"Round {round_idx}")
    reached = r.get("reached_policy_layer", True)
    tool_called = r.get("tool_called", True)
    err = r.get("error")

    if not reached or not tool_called or err:
        outcome = "TIMEOUT / NO_TOOL_CALL"
    elif r.get("bypassed"):
        outcome = "BYPASS"
    else:
        rc = r.get("reason_code", "DENIED")
        outcome = rc

    matrix[vec_name][outcome] += 1
    outcomes.add(outcome)

all_outcomes = sorted(list(outcomes))
header = f"| Vector | " + " | ".join(all_outcomes) + " | Total |"
sep = f"| :--- | " + " | ".join([":---:"] * len(all_outcomes)) + " | :---: |"
print(header)
print(sep)

for r_idx in range(1, 8):
    vec_name = round_names[r_idx]
    counts = matrix[vec_name]
    row = f"| **{vec_name}** | "
    for o in all_outcomes:
        row += f"{counts[o]} | "
    row += f"{sum(counts.values())} |"
    print(row)

# Totals
total_row = f"| **TOTAL** | "
for o in all_outcomes:
    tot_o = sum(matrix[round_names[r_idx]][o] for r_idx in range(1, 8))
    total_row += f"**{tot_o}** | "
total_row += f"**{len(lines)}** |"
print(total_row)
