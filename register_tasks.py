# -*- coding: utf-8 -*-
"""
주식정보 자동화 스케줄 태스크 일괄 등록/점검 스크립트

반드시 관리자 권한으로 실행할 것.
- 관리자 권한이 아니면 즉시 중단합니다 (schtasks 갱신이 조용히 실패하는 걸 막기 위함).
- 모든 태스크는 LogonType=S4U로 등록됩니다 → 화면 잠금/로그오프 상태에서도 실행됩니다.
  (기존 방식은 "로그온 중일 때만 실행"이라 화면이 잠겨있으면 지연·실패했음)
- 등록 후 각 태스크를 재조회해서 LogonType·Enabled·다음 실행시각을 검증하고
  통과/실패를 표로 출력합니다. "OK" 출력만 믿지 말고 이 표를 확인할 것.
"""
import ctypes
import subprocess
import sys
import os
import io

base = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(base, "register_tasks_last_run.log")

# 실행 즉시(관리자 권한 확인 이전부터) 모든 출력을 로그 파일에도 기록.
# 관리자 권한 UAC 창이 별도 콘솔에서 뜨기 때문에 원격에서 화면을 볼 수
# 없으므로, 무슨 일이 있었는지 항상 파일로 확인 가능하게 함.
_log_f = open(log_path, "w", encoding="utf-8")
class _Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()
    def flush(self):
        for st in self.streams:
            st.flush()
sys.stdout = _Tee(sys.stdout, _log_f)
sys.stderr = _Tee(sys.stderr, _log_f)

print(f"=== register_tasks.py 시작 {__import__('datetime').datetime.now()} ===")

# ── 관리자 권한 확인 ─────────────────────────────────────────
try:
    is_admin = ctypes.windll.shell32.IsUserAnAdmin()
except Exception:
    is_admin = False

print(f"관리자 권한 여부: {is_admin}")

if not is_admin:
    print("=" * 60)
    print("  오류: 관리자 권한이 아닙니다.")
    print("  관리자 권한 없이 실행하면 기존 태스크 갱신이 조용히")
    print("  실패합니다 (Access denied가 나와도 스크립트는 계속 진행됨).")
    print("  PowerShell을 관리자 권한으로 열고 다시 실행하세요.")
    print("=" * 60)
    sys.exit(1)

python  = sys.executable
alert   = os.path.join(base, "slack_alert.py")
testa   = os.path.join(base, "testa_scan.py")
morning = os.path.join(base, "testa_morning.py")
sp500   = os.path.join(base, "sp500_scan.py")
ndx100  = os.path.join(base, "nasdaq100_scan.py")
bnf     = os.path.join(base, "BNF 매매법", "bnf_scan.py")
weekly  = os.path.join(base, "us_weekly_scan.py")
tracker = os.path.join(base, "signal_tracker.py")
discov  = os.path.join(base, "discovery_scan.py")

WEEKDAYS_MF = "MON,TUE,WED,THU,FRI"

# (태스크명, 실행할 python 인자문자열, schtasks /sc 값, /d 값, 시각)
#   /d 값: 주간=요일(MON..SUN 콤마목록), 월간=단일 일자(28~31 중 하나)
#   월말이 며칠인지 달마다 달라서, 28/29/30/31 각각 별도 태스크로 등록하고
#   실제 발송 여부는 스크립트 내부(is_last_trading_day)에서 판단한다.
MONTH_END_DAYS = [28, 29, 30, 31]
tasks_base = [
    ("Stock_Weekly_Friday",  f'-X utf8 "{alert}" weekly',   "WEEKLY",  "FRI",       "16:00"),
    ("US_Weekly_Scan",       f'-X utf8 "{weekly}" slack',   "WEEKLY",  "FRI",       "16:05"),
    ("Stock_Monthly",        f'-X utf8 "{alert}" monthly',  "MONTHLY", None,        "16:10"),
    ("SP500_Monthly",        f'-X utf8 "{sp500}"',          "MONTHLY", None,        "17:30"),
    ("NDX100_Monthly",       f'-X utf8 "{ndx100}"',         "MONTHLY", None,        "18:00"),
    ("Testa_Daily_Scan",     f'-X utf8 "{testa}"',          "WEEKLY",  WEEKDAYS_MF, "16:00"),
    ("BNF_Daily_Scan",       f'-X utf8 "{bnf}"',            "WEEKLY",  WEEKDAYS_MF, "16:05"),
    ("Testa_Morning_Check",  f'-X utf8 "{morning}"',        "WEEKLY",  WEEKDAYS_MF, "09:10"),
    ("Tracker_Daily_Update", f'-X utf8 "{tracker}" update', "WEEKLY",  WEEKDAYS_MF, "16:30"),
    ("Tracker_Monthly",      f'-X utf8 "{tracker}" report', "MONTHLY", None,        "19:00"),
    ("Discovery_Monthly",    f'-X utf8 "{discov}"',         "MONTHLY", None,        "18:30"),
]

# 월간 항목은 28/29/30/31 네 개 태스크로 펼친다 (schtasks가 /d에 콤마로
# 여러 날짜를 받지 않아서, 예전부터 검증된 방식으로 되돌림)
tasks = []
for name, arg, sc, d, st in tasks_base:
    if sc == "MONTHLY":
        for day in MONTH_END_DAYS:
            tasks.append((f"{name}_{day}", arg, sc, str(day), st))
    else:
        tasks.append((name, arg, sc, d, st))

# ── 1단계: schtasks로 생성/갱신 ──────────────────────────────
# PowerShell의 New-ScheduledTaskTrigger는 월간(Monthly) 트리거를 지원하지
# 않아서(-Monthly 파라미터 자체가 없음) schtasks.exe로 생성한다.
print("작업 등록 중 (schtasks)...")
for name, arg, sc, d, st in tasks:
    tr = f'"{python}" {arg}'
    cmd = ["schtasks", "/create", "/tn", name, "/tr", tr, "/sc", sc,
           "/st", st, "/d", d, "/f"]
    result = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="cp949", errors="replace")
    if result.returncode == 0:
        print(f"OK: {name}")
    else:
        print(f"ERROR: {name} -> {(result.stderr or result.stdout).strip()}")

# ── 2단계: 로그온 방식(S4U)·배터리 설정 덧씌우기 ────────────
# 주의: 기본 생성 방식은 LogonType이 "Interactive only"라 화면 잠금 중엔
# 실행이 지연·실패했다(0x800710E0). S4U로 바꾸면 비밀번호 저장 없이도
# 로그오프/잠금 상태에서 정상 실행된다.
# New-ScheduledTaskSettingsSet의 실제 생성자 파라미터는
# -AllowStartIfOnBatteries / -DontStopIfGoingOnBatteries 이지만(반대 이름),
# 이미 생성된 태스크의 .Settings 객체 "속성" 이름은 Disallow.../Stop...
# 이므로 아래처럼 속성 대입 방식을 쓴다.
task_names = [t[0] for t in tasks]
names_ps = ", ".join(f'"{n}"' for n in task_names)
ps_fixup = f'''
$names = @({names_ps})
foreach ($n in $names) {{
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if ($t) {{
        try {{
            $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
            $principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType S4U -RunLevel Highest
            Set-ScheduledTask -TaskName $n -Settings $settings -Principal $principal -ErrorAction Stop | Out-Null
            Write-Host "설정완료: $n"
        }} catch {{
            Write-Host "설정실패: $n -> $($_.Exception.Message)"
        }}
    }} else {{
        Write-Host "없음: $n"
    }}
}}
'''
ps1_path = os.path.join(base, "_register_tasks_tmp.ps1")
with open(ps1_path, "w", encoding="utf-8-sig") as f:
    f.write(ps_fixup)

print("\n로그온 방식(S4U)·배터리 설정 적용 중...")
r1 = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1_path],
                     capture_output=True, text=True, encoding="cp949", errors="replace")
print(r1.stdout.strip())
if r1.stderr.strip():
    print("[stderr]", r1.stderr.strip())

# ── 검증: 등록이 실제로 의도대로 됐는지 재조회 ────────────────
task_names = [t[0] for t in tasks]
names_ps = ", ".join(f'"{n}"' for n in task_names)
ps_verify = f"""
$names = @({names_ps})
$rows = foreach ($n in $names) {{
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if (-not $t) {{
        [PSCustomObject]@{{ Name=$n; Found="NO"; LogonType="-"; Enabled="-"; NextRun="-" }}
    }} else {{
        $info = Get-ScheduledTaskInfo -TaskName $n
        [PSCustomObject]@{{
            Name=$n
            Found="YES"
            LogonType=$t.Principal.LogonType
            Enabled=$t.Settings.Enabled
            NextRun=$info.NextRunTime
        }}
    }}
}}
$rows | Format-Table -AutoSize | Out-String -Width 200
"""
ps1_verify_path = os.path.join(base, "_verify_tasks_tmp.ps1")
with open(ps1_verify_path, "w", encoding="utf-8-sig") as f:
    f.write(ps_verify)

r2 = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1_verify_path],
                     capture_output=True, text=True, encoding="cp949", errors="replace")
print("\n검증 결과 (LogonType이 S4U가 아니거나 Found=NO면 문제):")
print(r2.stdout)
if r2.stderr.strip():
    print("[stderr]", r2.stderr.strip())

try:
    os.remove(ps1_path)
    os.remove(ps1_verify_path)
except OSError:
    pass

print(f"=== register_tasks.py 종료 {__import__('datetime').datetime.now()} ===")
print(f"(이 창은 10초 후 자동으로 닫힙니다. 로그: {log_path})")
import time
time.sleep(10)
