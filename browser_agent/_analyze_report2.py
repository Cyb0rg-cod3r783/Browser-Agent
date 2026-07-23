import json
from pathlib import Path

p = Path(__file__).parent / "reports" / "company_updation3_20260722_105602.json"
data = json.loads(p.read_text(encoding="utf-8"))

# For each failure, dump full assertion results and last few passed steps
for t in data["test_results"]:
    if t["status"] not in ("failed", "errored"):
        continue
    print("=" * 80)
    print(t["test_name"], "|", t["status"], "|", t["category"])
    print("error_detail:", t.get("error_detail"))
    print("--- steps ---")
    for s in t.get("step_results") or []:
        status = s.get("status")
        marker = "OK" if status == "passed" else "!!"
        print(f"  [{marker}] {s.get('sequence'):2} {s.get('action'):8} {s.get('element_label')!r:40} loc={s.get('locator_used')} err={(s.get('error') or '')[:180]}")
    print("--- assertions ---")
    for a in t.get("assertion_results") or []:
        print(f"  passed={a.get('passed')} type={a.get('type')} expected={a.get('expected')!r}")
        print(f"         actual={a.get('actual')!r}")
