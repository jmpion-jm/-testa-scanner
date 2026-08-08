# -*- coding: utf-8 -*-
"""
월봉MA10 + 주봉MA10 눌림목 이중 필터 로직 검증 (us_weekly_scan.py).
CLAUDE.md에 명시된 매수 조건 "월봉MA10 위 + 주봉MA10 눌림목(≤5%) 근처"이
정확히 구현돼 있는지, 월봉MA10 아래 종목이 제대로 제외되는지 확인한다.

매매법 규칙 자체는 바꾸지 않고 기존 코드가 그 규칙대로 동작하는지만 확인한다 —
실패하면 코드를 임의로 고치지 말고 사용자에게 먼저 보고할 것.
"""
import sys, os
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name} — {detail}")
        failures.append(name)


def _ohlcv(closes, dates):
    return pd.DataFrame({
        'Open': closes, 'High': closes, 'Low': closes,
        'Close': closes, 'Volume': [1000.0] * len(closes),
    }, index=dates[-len(closes):])


def _monthly_df(period, prev_val, curr_val, dates):
    return _ohlcv([100.0] * period + [prev_val, curr_val], dates)


def _weekly_df(prev_val, curr_val, dates):
    # MA_WEEK=10은 us_weekly_scan.py에 하드코딩된 상수 (아래 기대값들의 전제)
    return _ohlcv([100.0] * 10 + [prev_val, curr_val], dates)


def _daily_df(dates, lows):
    return _ohlcv(lows, dates)


def test_weekly_dual_filter():
    import importlib
    mod = importlib.import_module('us_weekly_scan')

    check('MA_WEEK 상수가 10으로 고정돼 있음 (아래 기대값들의 전제)',
          mod.MA_WEEK == 10, f'실제={mod.MA_WEEK}')

    P = mod.MA_MONTH
    month_dates = pd.date_range('2020-01-01', periods=P + 2, freq='MS')
    week_dates  = pd.date_range('2024-01-01', periods=12, freq='W-MON')
    day_dates   = pd.date_range('2026-01-01', periods=5, freq='D')
    daily_lows  = [95.0, 96.0, 94.0, 97.0, 98.0]

    # (월봉 prev/curr, 주봉 prev/curr, 기대 grade, 설명) — grade=None이면 결과에서 완전 제외돼야 함
    cases = {
        'MBELOW':   dict(m=(105.0, 90.0),  w=(105.0, 103.0), grade=None,
                          desc='월봉MA10 이탈 → 완전 제외'),
        'WFRESH':   dict(m=(105.0, 200.0), w=(80.0, 110.0),  grade=1,
                          desc='주봉 신규돌파'),
        'WPULL':    dict(m=(105.0, 200.0), w=(105.0, 103.0), grade=2,
                          desc='주봉 눌림목 ≤5%'),
        'WSUPPORT': dict(m=(105.0, 200.0), w=(105.0, 108.0), grade=3,
                          desc='주봉 지지권 5~10%'),
        'WTREND':   dict(m=(105.0, 200.0), w=(105.0, 130.0), grade=4,
                          desc='주봉 추세중 >10%'),
        'WBELOWW':  dict(m=(105.0, 200.0), w=(105.0, 95.0),  grade=5,
                          desc='주봉MA10 이탈'),
    }

    fetch_map = {}
    for ticker, spec in cases.items():
        fetch_map[(ticker, '1mo')] = _monthly_df(P, *spec['m'], month_dates)
        fetch_map[(ticker, '1wk')] = _weekly_df(*spec['w'], week_dates)
        fetch_map[(ticker, '1d')]  = _daily_df(day_dates, daily_lows)

    def fake_fetch(ticker, interval, period):
        return fetch_map[(ticker, interval)]

    original_fetch  = mod.fetch
    original_stocks = mod.STOCKS
    mod.fetch  = fake_fetch
    mod.STOCKS = {t: (t, '테스트') for t in cases}

    try:
        rows = mod.scan()
    finally:
        mod.fetch  = original_fetch
        mod.STOCKS = original_stocks

    by_ticker = {r['ticker']: r for r in rows}

    print('\n[us_weekly_scan] 월봉×주봉 이중필터 등급 검증')

    check('MBELOW(월봉MA10 이탈) 종목은 완전히 제외됨', 'MBELOW' not in by_ticker,
          f'실제 포함 여부={"MBELOW" in by_ticker}')

    for ticker, spec in cases.items():
        if spec['grade'] is None:
            continue
        r = by_ticker.get(ticker)
        if r is None:
            check(f'{ticker} 결과에 존재 ({spec["desc"]})', False, '결과에서 누락됨')
            continue
        check(f'{ticker} grade={spec["grade"]} ({spec["desc"]})',
              r['grade'] == spec['grade'], f'실제={r["grade"]}')

        # 적정매수단가 = 주봉MA10, 손절가 = min(최근5일 저가, 매수단가*0.97)
        w_prev, w_curr = spec['w']
        exp_w_ma = (8 * 100.0 + w_prev + w_curr) / 10
        check(f'{ticker} 적정매수단가(limit_buy)=주봉MA10',
              abs(r['limit_buy'] - round(exp_w_ma, 2)) < 0.01,
              f'기대={round(exp_w_ma, 2)}, 실제={r["limit_buy"]}')

        exp_stop = round(min(min(daily_lows), r['limit_buy'] * 0.97), 2)
        check(f'{ticker} 손절가(stop5) = min(5일저가, 매수단가*0.97)',
              abs(r['stop5'] - exp_stop) < 0.01, f'기대={exp_stop}, 실제={r["stop5"]}')


if __name__ == '__main__':
    print('=' * 60)
    print('  월봉×주봉 이중필터 로직 회귀 테스트')
    print('=' * 60)

    test_weekly_dual_filter()

    print('\n' + '=' * 60)
    if failures:
        print(f'  실패 {len(failures)}건: {", ".join(failures)}')
        print('=' * 60)
        sys.exit(1)
    else:
        print('  전체 통과')
        print('=' * 60)
