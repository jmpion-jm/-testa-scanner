# 주식 알림 자동 스케줄러 설정
# 관리자 권한으로 실행하세요

$pythonPath = (Get-Command python).Source
$scriptPath = "c:\Users\user\Desktop\주식정보\slack_alert.py"

# ── 1. 매주 금요일 오후 4시 — 주간 모니터링 알림 ──────────────
$weeklyAction  = New-ScheduledTaskAction -Execute $pythonPath `
    -Argument "-X utf8 `"$scriptPath`" weekly"

$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "16:00"

Register-ScheduledTask `
    -TaskName   "주식알림_주간_금요일" `
    -Action     $weeklyAction `
    -Trigger    $weeklyTrigger `
    -RunLevel   Highest `
    -Description "매주 금요일 오후 4시 — 주간 모니터링 알림" `
    -Force

Write-Host "주간 알림 스케줄 등록 완료 (매주 금요일 16:00)" -ForegroundColor Green


# ── 2. 매달 말일 오후 4시 — 월말 매매 결정 알림 ──────────────
# Windows 작업 스케줄러는 '말일' 직접 설정 불가
# → 매달 28/29/30/31일 실행 후 코드에서 말일 여부 자동 판단

$monthlyAction  = New-ScheduledTaskAction -Execute $pythonPath `
    -Argument "-X utf8 `"$scriptPath`" auto"

# 매달 28일부터 31일까지 실행 (코드 내부에서 말일 여부 판단)
foreach ($day in @(28, 29, 30, 31)) {
    $trigger = New-ScheduledTaskTrigger -Monthly -DaysOfMonth $day -At "16:10"
    Register-ScheduledTask `
        -TaskName   "주식알림_월말_${day}일" `
        -Action     $monthlyAction `
        -Trigger    $trigger `
        -RunLevel   Highest `
        -Description "매달 ${day}일 오후 4시 — 월말 매매 결정 알림 (말일 자동 판단)" `
        -Force
}

Write-Host "월말 알림 스케줄 등록 완료 (매달 28~31일 16:10)" -ForegroundColor Green
Write-Host ""
Write-Host "등록된 작업 목록:" -ForegroundColor Cyan
Get-ScheduledTask | Where-Object { $_.TaskName -like "주식알림*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
