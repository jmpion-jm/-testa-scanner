# Stock 작업 5개 배터리 제한 제거 + StartWhenAvailable 설정
# 관리자 권한으로 실행 필요 (우클릭 → 관리자 권한으로 실행)

$taskNames = @("Stock_Weekly_Friday","Stock_Monthly_28","Stock_Monthly_29","Stock_Monthly_30","Stock_Monthly_31")
$python = "C:\Users\user\AppData\Local\Programs\Python\Python313\python.exe"
$alert  = "c:\Users\user\Desktop\주식정보\slack_alert.py"

$fixed = 0
foreach ($n in $taskNames) {
    $xml = (schtasks /query /xml /tn $n 2>&1) -join "`n"
    if ($LASTEXITCODE -ne 0) { Write-Host "없음: $n"; continue }

    $xml = $xml -replace '<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>',
                         '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
    $xml = $xml -replace '<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>',
                         '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
    if ($xml -notmatch 'StartWhenAvailable') {
        $xml = $xml -replace '</Settings>', "  <StartWhenAvailable>true</StartWhenAvailable>`n  </Settings>"
    }

    $tmp = "$env:TEMP\fix_$n.xml"
    [System.IO.File]::WriteAllText($tmp, $xml, [System.Text.Encoding]::Unicode)
    $r = schtasks /create /xml $tmp /tn $n /f 2>&1
    if ($LASTEXITCODE -eq 0) { Write-Host "OK: $n"; $fixed++ }
    else { Write-Host "FAIL: $n → $r" }
}

Write-Host "`n완료: $fixed / $($taskNames.Count) 수정"
Read-Host "`n엔터를 누르면 종료합니다"
