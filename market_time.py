# -*- coding: utf-8 -*-
"""
미국장 월말 종가 확정 시점 판정 — 월말 매도/스캔 알림이 "월말 종가"를 보고 나가게 하기 위한 공통 함수.

배경(2026-09-26 전체 검토에서 발견): 월말 알림 워크플로우들이 KST 16:10~16:40(UTC 07:10~07:40)에
돌고 있었는데, 이 시각은 그날 미국장이 열리기도 전이다(미국 마감 = UTC 20:00 서머타임 / 21:00 표준시).
그래서 "월말 매도 결정" 알림이 말일 종가가 아니라 그 전날 종가로 나가고 있었다 — 원서 원칙
"월말 종가 확정 후 판단"과 어긋남. 월말 워크플로우는 이제 UTC 22시대(= KST 다음날 아침 7시대,
미국 마감 1~2시간 뒤)에 돌고, 여기 함수로 "오늘(미국 기준)이 말일이고 장이 이미 끝났는가"를 확인한다.

미국 공휴일은 따로 처리하지 않는다: 말일 평일이 휴장일(예: 5월 마지막 월요일 메모리얼데이)이면
그날 저녁엔 데이터의 마지막 봉이 실제 마지막 거래일 종가라 결과가 그대로 맞다.
"""
import os
import calendar
from datetime import datetime, timedelta, timezone, date

# UTC 20시 이후 ~ 다음날 06시 전 = 그날 미국장 마감 이후로 본다.
# 기준 날짜는 now-6h로 잡아서, GitHub 스케줄이 몇 시간 밀려 UTC 자정을 넘겨도
# 여전히 "그날(미국 기준)"로 판정되게 한다.
_REF_SHIFT = timedelta(hours=6)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def us_ref_date(now: datetime = None) -> date:
    """판정 기준이 되는 미국 거래일 날짜."""
    now = now or utc_now()
    return (now - _REF_SHIFT).date()


def is_after_us_close(now: datetime = None) -> bool:
    now = now or utc_now()
    return now.hour >= 20 or now.hour < 6


def is_last_weekday_of_month(d: date) -> bool:
    last_day = calendar.monthrange(d.year, d.month)[1]
    for day in range(last_day, 0, -1):
        if date(d.year, d.month, day).weekday() < 5:
            return d.day == day
    return False


def is_monthend_after_close(now: datetime = None) -> bool:
    """미국 기준 이번 달 마지막 평일이고, 그날 장이 이미 마감됐는가."""
    now = now or utc_now()
    return is_after_us_close(now) and is_last_weekday_of_month(us_ref_date(now))


def should_run_monthly_scan() -> bool:
    """월말 전용 스캔(나스닥100/S&P500/패턴)의 실행 여부.
    GitHub 스케줄 실행일 때만 말일·마감후 조건을 따지고, 수동 실행(workflow_dispatch)이나
    로컬 실행은 언제든 그대로 돈다."""
    if os.environ.get('GITHUB_EVENT_NAME') != 'schedule':
        return True
    return is_monthend_after_close()
