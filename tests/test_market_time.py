# -*- coding: utf-8 -*-
"""market_time.py 회귀 테스트 — 월말 알림이 말일 미국장 마감 후에만 나가는지.

2026-09-26: 월말 매도 알림이 KST 16:10(미국 개장 전)에 돌아 말일 전날 종가로 결정을
내리던 문제를 고치면서 추가. 워크플로우 실행 시각(UTC)별로 판정을 고정해 둔다.
"""
import os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import market_time as mt


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def decide(now):
    """slack_alert.run('auto')와 같은 판정."""
    after = mt.is_after_us_close(now)
    friday = mt.us_ref_date(now).weekday() == 4
    monthly = mt.is_monthend_after_close(now)
    weekly = friday and not after and not monthly
    return monthly, weekly


CASES = [
    # (설명, 시각, 기대 월말, 기대 주간)
    ('9/30(수) 말일 KST 16:10 옛 실행시각 — 미국장 개장 전', utc(2026, 9, 30, 7, 10), False, False),
    ('9/30(수) 말일 미국 마감 후 UTC 22:00',                 utc(2026, 9, 30, 22, 0), True,  False),
    ('9/29(화) 말일 아님 UTC 22:00',                         utc(2026, 9, 29, 22, 0), False, False),
    ('9/30 실행이 밀려 UTC 10/1 01:30에 돈 경우',              utc(2026, 10, 1, 1, 30), True,  False),
    ('9/25(금) 주간 아침 UTC 07:00',                          utc(2026, 9, 25, 7, 0),  False, True),
    ('10/30(금)=말일 아침 주간 실행',                         utc(2026, 10, 30, 7, 0), False, True),
    ('10/30(금)=말일 저녁 월말 실행 (주간 중복 없음)',          utc(2026, 10, 30, 22, 0), True, False),
    ('10/31(토) 저녁 — 말일 평일 아님',                        utc(2026, 10, 31, 22, 0), False, False),
    ('2027/5/31(월, 메모리얼데이) 저녁 — 말일 평일로 판정',     utc(2027, 5, 31, 22, 0), True,  False),
]


def main():
    fail = 0
    for desc, now, exp_m, exp_w in CASES:
        got = decide(now)
        ok = got == (exp_m, exp_w)
        fail += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {desc}: 월말={got[0]} 주간={got[1]}"
              + ('' if ok else f'  (기대 월말={exp_m} 주간={exp_w})'))

    # 스케줄 실행이 아닐 때(수동/로컬)는 월말 스캔이 항상 돈다
    os.environ.pop('GITHUB_EVENT_NAME', None)
    if not mt.should_run_monthly_scan():
        print('FAIL  수동/로컬 실행은 항상 진행돼야 함'); fail += 1
    else:
        print('PASS  수동/로컬 실행은 항상 진행')

    print(f'\n{len(CASES) + 1 - fail}/{len(CASES) + 1} 통과')
    sys.exit(1 if fail else 0)


if __name__ == '__main__':
    main()
