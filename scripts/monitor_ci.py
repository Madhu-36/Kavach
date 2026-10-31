import json
import time
import urllib.request

run_id = 41231437502
url = f"https://api.github.com/repos/Madhu-36/Kavach/actions/runs/{run_id}"
jobs_url = f"https://api.github.com/repos/Madhu-36/Kavach/actions/runs/{run_id}/jobs"

print(f"Monitoring GitHub Actions Run {run_id}...")

while True:
    req = urllib.request.Request(url, headers={"User-Agent": "Kavach-CI-Monitor"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    status = data.get("status")
    conclusion = data.get("conclusion")
    print(f"Status: {status} | Conclusion: {conclusion}")
    if status == "completed":
        break
    time.sleep(15)

# Fetch all jobs
req_jobs = urllib.request.Request(jobs_url, headers={"User-Agent": "Kavach-CI-Monitor"})
with urllib.request.urlopen(req_jobs) as resp:
    jobs_data = json.loads(resp.read().decode("utf-8"))

lines = [
    f"GitHub Actions CI Run: {data['html_url']}",
    f"Status: {status}",
    f"Conclusion: {conclusion}",
    f"Commit: {data['head_sha']}",
    f"Created At: {data['created_at']}",
    f"Updated At: {data['updated_at']}",
    "",
    "=== JOB SUMMARY ===",
]
for j in jobs_data.get("jobs", []):
    j_line = f"Job: {j['name']} | Status: {j['status']} | Conclusion: {j['conclusion']} | Steps: {len(j.get('steps', []))}"
    print(j_line)
    lines.append(j_line)

with open("evidence/ci_run.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("Saved to evidence/ci_run.txt")
