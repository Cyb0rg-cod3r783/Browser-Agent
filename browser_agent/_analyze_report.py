import json
from pathlib import Path
from collections import Counter

p = Path(__file__).parent / "reports" / "company_updation3_20260722_105602.json"
data = json.loads(p.read_text(encoding="utf-8"))
print("SUMMARY", data["summary"])
print("DURATION_S", data["duration_seconds"])
print("=" * 80)

categories = Counter()

for t in data["test_results"]:
    if t["status"] not in ("failed", "errored"):
        continue
    print(f"\n[{t['status'].upper()}] {t['test_name']} ({t['category']}) duration={t['duration_ms']}ms")
    print(f"  error_detail: {t.get('error_detail')}")
    for s in t.get("step_results") or []:
        if s.get("status") != "passed":
            err = (s.get("error") or "")[:500]
            print(f"  STEP {s.get('sequence')} {s.get('action')} label={s.get('element_label')!r} status={s.get('status')}")
            print(f"    locator={s.get('locator_used')}")
            print(f"    error={err}")
            low = err.lower()
            if "timeout" in low:
                categories["timeout"] += 1
            elif "not found" in low or "elementnotfound" in low or "locator" in low:
                categories["locator/self-heal"] += 1
            elif "toast" in low or "text" in low:
                categories["toast/text"] += 1
            else:
                categories["other_step"] += 1
    for a in t.get("assertion_results") or []:
        if not a.get("passed", True):
            print(f"  ASSERT FAIL type={a.get('type')} expected={a.get('expected')!r} actual={a.get('actual')!r}")
            exp = str(a.get("expected") or "").lower()
            act = str(a.get("actual") or "").lower()
            if "toast" in exp or "error" in exp or "invalid" in exp or "required" in exp:
                categories["toast/assertion"] += 1
            elif a.get("type") in ("url_contains",):
                categories["url_assertion"] += 1
            else:
                categories["other_assertion"] += 1

print("\n" + "=" * 80)
print("CATEGORY COUNTS", dict(categories))
