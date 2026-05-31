import subprocess, sys, os

python  = sys.executable
base    = os.path.dirname(os.path.abspath(__file__))
alert   = os.path.join(base, "slack_alert.py")
testa   = os.path.join(base, "testa_scan.py")
morning = os.path.join(base, "testa_morning.py")
sp500   = os.path.join(base, "sp500_scan.py")
ndx100  = os.path.join(base, "nasdaq100_scan.py")

tasks = [
    # 월봉 MA10 알림 — 매주 금요일 / 월말
    ("Stock_Weekly_Friday",  f'"{python}" -X utf8 "{alert}" weekly', "WEEKLY",  "FRI", "16:00", None),
    ("Stock_Monthly_28",     f'"{python}" -X utf8 "{alert}" monthly',"MONTHLY", None,  "16:10", "28"),
    ("Stock_Monthly_29",     f'"{python}" -X utf8 "{alert}" monthly',"MONTHLY", None,  "16:10", "29"),
    ("Stock_Monthly_30",     f'"{python}" -X utf8 "{alert}" monthly',"MONTHLY", None,  "16:10", "30"),
    ("Stock_Monthly_31",     f'"{python}" -X utf8 "{alert}" monthly',"MONTHLY", None,  "16:10", "31"),
    # S&P 500 스캔 — 월말 (28~31일) 17:30 (슬랙 알림 완료 후)
    ("SP500_Monthly_28",     f'"{python}" -X utf8 "{sp500}"',        "MONTHLY", None,  "17:30", "28"),
    ("SP500_Monthly_29",     f'"{python}" -X utf8 "{sp500}"',        "MONTHLY", None,  "17:30", "29"),
    ("SP500_Monthly_30",     f'"{python}" -X utf8 "{sp500}"',        "MONTHLY", None,  "17:30", "30"),
    ("SP500_Monthly_31",     f'"{python}" -X utf8 "{sp500}"',        "MONTHLY", None,  "17:30", "31"),
    # NASDAQ 100 스캔 — 월말 (28~31일) 18:00 (S&P500 완료 후)
    ("NDX100_Monthly_28",    f'"{python}" -X utf8 "{ndx100}"',       "MONTHLY", None,  "18:00", "28"),
    ("NDX100_Monthly_29",    f'"{python}" -X utf8 "{ndx100}"',       "MONTHLY", None,  "18:00", "29"),
    ("NDX100_Monthly_30",    f'"{python}" -X utf8 "{ndx100}"',       "MONTHLY", None,  "18:00", "30"),
    ("NDX100_Monthly_31",    f'"{python}" -X utf8 "{ndx100}"',       "MONTHLY", None,  "18:00", "31"),
    # 테스타 스캔 — 평일 매일 장 마감 후 (16:00)
    ("Testa_Daily_Scan",     f'"{python}" -X utf8 "{testa}"',        "WEEKLY",  "MON,TUE,WED,THU,FRI", "16:00", None),
    # 테스타 아침 진입 확인 — 평일 09:10 (전날 신호 종목 시가 확인)
    ("Testa_Morning_Check",  f'"{python}" -X utf8 "{morning}"',      "WEEKLY",  "MON,TUE,WED,THU,FRI", "09:10", None),
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
