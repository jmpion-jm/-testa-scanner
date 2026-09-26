# -*- coding: utf-8 -*-
"""
월봉MA10 매수/매도 신호 로직 검증 — nasdaq100_scan.py / sp500_scan.py의
scan_ndx100() / scan_sp500()가 원서 원칙(2026-09-26 사용자 결정)대로 분류하는지 확인한다.
  매수 = 월말 확정 돌파(후킹 캔들: 직전 종가 ≤ 10이평, 양봉, 시가 ≤ 10이평 < 종가, p.256) → priority 1
       = 10이평 지지 반등(직전 종가 > 10이평, 저가 ≤ 10이평 < 종가, p.340)                → priority 2
  10이평 위 나머지 = 추세 진행 중(보유 유지)                                            → priority 3
  10이평 아래 = 결과에서 제외(매도)
합성(가짜) 월봉 OHLC로 yf.download()를 대체해 네트워크 호출 없이 검증한다.
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


def build_cases():
    """
    ticker -> (직전달 종가, 이번달 (시가, 저가, 종가)).
    기준선(첫 period개월)은 전부 100으로 고정해 MA10을 손계산 가능하게 한다(이번 달 10이평 ≈ 100~103).
    """
    return {
        'HOOK':    (80.0, (85.0, 84.0, 110.0)),     # 직전 아래 → 양봉 몸통이 10이평 관통: 돌파(후킹)
        'GAP':     (99.0, (104.0, 103.0, 106.0)),   # 직전 아래 → 시가부터 10이평 위(몸통 관통 아님): 후킹 아님 → 추세
        'SUPPORT': (105.0, (104.0, 99.0, 103.0)),   # 직전 위 → 저가가 10이평에 닿고 종가 위: 지지
        'TREND':   (105.0, (110.0, 120.0 - 1, 130.0)),  # 직전 위, 저가도 10이평 한참 위: 추세
        'BELOW':   (105.0, (100.0, 90.0, 95.0)),    # 이번달 10이평 아래: 매도(결과에서 제외)
    }


def expected_ma10(period, prev_val, curr_val, which):
    if which == 'prev':
        return ((period - 1) * 100.0 + prev_val) / period
    return ((period - 2) * 100.0 + prev_val + curr_val) / period


def make_raw(period, cases, short_ticker='SHORT'):
    dates = pd.date_range('2020-01-01', periods=period + 2, freq='MS')
    frames = {}
    for ticker, (prev_val, (o, lo, c)) in cases.items():
        closes = [100.0] * period + [prev_val, c]
        opens = [100.0] * period + [prev_val, o]
        lows = [100.0] * period + [prev_val, lo]
        highs = [max(a, b) for a, b in zip(opens, closes)]
        frames[ticker] = pd.DataFrame({'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
                                       'Volume': [1000.0] * len(closes)}, index=dates)

    # 데이터 부족 종목 — MA10 계산 최소 개월 수(period+2) 미달 시 반드시 제외돼야 함
    short_len = max(period - 2, 1)
    frames[short_ticker] = pd.DataFrame(
        {k: [100.0] * short_len for k in ('Open', 'High', 'Low', 'Close', 'Volume')}, index=dates[:short_len]
    ).reindex(dates)

    return pd.concat(frames, axis=1)


def _run_scan(module_name, scan_fn_name):
    import importlib
    mod = importlib.import_module(module_name)
    P = mod.MA_PERIOD
    cases = build_cases()
    raw = make_raw(P, cases)

    original_download = mod.yf.download
    mod.yf.download = lambda *a, **k: raw
    try:
        scan_fn = getattr(mod, scan_fn_name)
        results = scan_fn(list(cases.keys()) + ['SHORT'])
    finally:
        mod.yf.download = original_download

    by_ticker = {r['ticker']: r for r in results}

    print(f'\n[{module_name}] MA{P} 신호 분류 검증')

    check('BELOW(월봉MA10 이탈) 종목은 결과에서 제외됨', 'BELOW' not in by_ticker,
          f'실제 포함 여부={"BELOW" in by_ticker}')
    check('SHORT(데이터 부족) 종목은 결과에서 제외됨', 'SHORT' not in by_ticker,
          f'실제 포함 여부={"SHORT" in by_ticker}')

    expects = {
        'HOOK':    dict(priority=1, fresh=True,  signal_kw='돌파(후킹'),
        'SUPPORT': dict(priority=2, fresh=False, signal_kw='10이평 지지'),
        'GAP':     dict(priority=3, fresh=False, signal_kw='추세 진행'),
        'TREND':   dict(priority=3, fresh=False, signal_kw='추세 진행'),
    }
    for ticker, exp in expects.items():
        r = by_ticker.get(ticker)
        if r is None:
            check(f'{ticker} 결과에 존재', False, '결과에서 누락됨')
            continue
        prev_val, (_, _, curr_val) = cases[ticker]
        exp_ma10 = expected_ma10(P, prev_val, curr_val, 'curr')
        exp_pct  = round((curr_val - exp_ma10) / exp_ma10 * 100, 1)

        check(f'{ticker} priority={exp["priority"]}', r['priority'] == exp['priority'],
              f'실제={r["priority"]}')
        check(f'{ticker} fresh={exp["fresh"]}', r['fresh'] == exp['fresh'],
              f'실제={r["fresh"]}')
        check(f'{ticker} signal에 "{exp["signal_kw"]}" 포함', exp['signal_kw'] in r['signal'],
              f'실제={r["signal"]}')
        check(f'{ticker} MA10 계산값 일치', abs(r['ma10'] - round(exp_ma10, 2)) < 0.01,
              f'기대={round(exp_ma10, 2)}, 실제={r["ma10"]}')
        check(f'{ticker} 괴리율(pct) 계산값 일치', abs(r['pct'] - exp_pct) < 0.15,
              f'기대={exp_pct}, 실제={r["pct"]}')


def test_nasdaq100_monthly_signal():
    _run_scan('nasdaq100_scan', 'scan_ndx100')


def test_sp500_monthly_signal():
    _run_scan('sp500_scan', 'scan_sp500')


if __name__ == '__main__':
    print('=' * 60)
    print('  월봉MA10 신호 로직 회귀 테스트')
    print('=' * 60)

    test_nasdaq100_monthly_signal()
    test_sp500_monthly_signal()

    print('\n' + '=' * 60)
    if failures:
        print(f'  실패 {len(failures)}건: {", ".join(failures)}')
        print('=' * 60)
        sys.exit(1)
    else:
        print('  전체 통과')
        print('=' * 60)
