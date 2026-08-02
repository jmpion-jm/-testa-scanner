# 주식정보 자동매매 시스템 — CLAUDE.md

## 이 프로젝트란
월봉MA10 매매법 기반 미국/한국 주식 스캔 + 슬랙 알림 자동화. 실행 환경은
**로컬 PC가 아니라 GitHub Actions**(`.github/workflows/*.yml`)이 메인이다.
로컬 Windows 작업 스케줄러(`register_tasks.py`)는 별도의 중복 백업 경로일 뿐이다.

## 절대 하지 말 것 (NEVER DO)
- **월봉MA10 매매 규칙을 임의로 변경하지 말 것** (노후자산 시스템, 사용자 승인 없이 손절/익절 기준 수정 금지)
- 알림/스케줄 문제가 생기면 로컬 스케줄러부터 고치지 말 것 — `.github/workflows/`가 실제 실행 경로인지 먼저 확인
- GitHub Actions 잡이 "success"라고 해서 실제로 원하는 결과(슬랙 전송 등)가 나갔다고 가정하지 말 것 — 내부 예외가 조용히 삼켜질 수 있음
- 관리자 권한 없이 `register_tasks.py` 실행하지 말 것 — 기존 태스크 갱신이 조용히 실패함(Access denied가 나와도 스크립트가 계속 진행됨)

## 자주 하는 실수 (2026-08-01 세션에서 확인)
- PowerShell을 서브프로세스로 호출할 때 `encoding='utf-8'`을 쓰면 한글 시스템 메시지에서 깨짐 → **`encoding='cp949', errors='replace'`** 사용
- 멀티라인 PowerShell 스크립트를 `-Command`에 통째로 문자열로 넘기지 말 것 → **`.ps1` 파일로 저장 후 `-File`로 실행**
- `New-ScheduledTaskTrigger`엔 `-Monthly` 파라미터가 없음 → 월간 트리거는 **schtasks.exe**로 생성
- `New-ScheduledTaskSettingsSet`의 실제 파라미터명은 `-AllowStartIfOnBatteries`/`-DontStopIfGoingOnBatteries` (Disallow/Stop 아님, 반대 이름)
- `Set-ScheduledTask -InputObject`는 이 환경에서 "매개 변수가 틀립니다" 오류 발생 → `-TaskName` + `-Settings`/`-Principal` 객체 방식 사용
- Slack Block Kit은 메시지당 **50블록 하드 리밋** — `send_slack()`은 초과 시 자동 분할 전송하도록 이미 수정됨, 새 알림 블록 추가 시 유의
- 외부 스크래핑(`pd.read_html` 등)은 로컬에 우연히 깔린 패키지(lxml 등)에 의존하지 말고 `requirements.txt`에 명시할 것

## 문제 생기면 여기부터
1. `.github/workflows/`에서 관련 워크플로우 스케줄/최근 실행 확인 (`Invoke-RestMethod`로 GitHub API 조회 가능, 공개 저장소라 인증 불필요·로그 원문만 403)
2. `스케줄태스크_실행오류_수정_2026-08-01.md` — 이미 확인된 원인/조치 대조
3. 프로젝트 메모리(`project_stock_system.md`) — 계좌 구성, 슬랙 채널, 매매 전략 상세

## 핵심 규칙 요약
- 매수 조건: 월봉MA10 위 + 주봉MA10 눌림목(≤5%) 근처
- 매도 조건: 월봉MA10 하향 이탈 (예외 없음)
- DC/IRP 퇴직연금 계좌는 개별종목 트레이딩 규칙(즉시손절) 적용 대상 아님 — 장기 분산자산으로 별도 취급
