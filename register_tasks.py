import subprocess, sys, os

python = sys.executable
script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slack_alert.py")

tasks = [
    ("Stock_Weekly_Friday",  f'"{python}" -X utf8 "{script}" weekly', "WEEKLY",  "FRI", "16:00", None),
    ("Stock_Monthly_28",     f'"{python}" -X utf8 "{script}" auto',   "MONTHLY", None,  "16:10", "28"),
    ("Stock_Monthly_29",     f'"{python}" -X utf8 "{script}" auto',   "MONTHLY", None,  "16:10", "29"),
    ("Stock_Monthly_30",     f'"{python}" -X utf8 "{script}" auto',   "MONTHLY", None,  "16:10", "30"),
    ("Stock_Monthly_31",     f'"{python}" -X utf8 "{script}" auto',   "MONTHLY", None,  "16:10", "31"),
]

for name, tr, sc, day, st, md in tasks:
    cmd = ["schtasks", "/create", "/tn", name, "/tr", tr, "/sc", sc, "/st", st, "/f"]
    if day:
        cmd += ["/d", day]
    if md:
        cmd += ["/d", md]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"OK: {name}")
    else:
        print(f"ERROR: {name} -> {result.stderr.strip()}")

print("\n등록된 작업 확인:")
r = subprocess.run(["schtasks", "/query", "/fo", "TABLE"], capture_output=True, text=True, encoding="cp949")
for line in r.stdout.splitlines():
    if "Stock_" in line:
        print(" ", line)

input("\n완료. 아무 키나 누르세요...")
