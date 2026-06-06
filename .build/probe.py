import json, os, sys

TASKS = r"C:\Users\sshub\AppData\Local\Temp\claude\C--Users-sshub-aisdlc-v2\d16b1065-ba67-4f2a-8192-e502d71fe314\tasks"
ids = ["adbc548d67ab573df","acf733b9e7050d27c","a61bd579fa2e2313d",
       "adc051e3ecb957862","a1275d81b7b9c4922","a6778312596fcfff3","a4127b2594aeed8bb"]

f = os.path.join(TASKS, ids[0] + ".output")
print("exists:", os.path.exists(f), "size:", os.path.getsize(f) if os.path.exists(f) else 0)
with open(f, encoding="utf-8") as fh:
    lines = fh.readlines()
print("lines:", len(lines))
# Inspect last 6 lines: top-level keys + role/type + text length only (NO content)
for i, ln in enumerate(lines[-6:]):
    ln = ln.strip()
    if not ln:
        continue
    try:
        o = json.loads(ln)
    except Exception as e:
        print(f"  [last-{6-i}] non-json line, len={len(ln)}")
        continue
    keys = list(o.keys()) if isinstance(o, dict) else type(o).__name__
    role = o.get("role") or o.get("type") if isinstance(o, dict) else None
    # find longest string anywhere shallowly
    def textlen(x):
        if isinstance(x, str): return len(x)
        if isinstance(x, dict): return sum(textlen(v) for v in x.values())
        if isinstance(x, list): return sum(textlen(v) for v in x)
        return 0
    print(f"  [last-{6-i}] keys={keys} role/type={role} totaltextlen={textlen(o)}")
