# -*- coding: utf-8 -*-
"""
bullish_pattern_scan.py의 3개 검증된 패턴(쌍바닥/삼중바닥/역H&S)을 과거 월봉
데이터로 백테스트한다. (2026-09-12 최초 검증 완료 — 결과는
캔들차트(성승현작가)/성승현_매매법_핵심정리.md의 "6개 패턴 백테스트 최종
결론" 절 및 프로젝트 메모리 feedback_strategy_rules.md 참고)

⚠️ 애초에 이 스크립트로 6개 패턴(쌍봉/H&S/삼고점/컵위드핸들 포함)을 백테스트해서
3개만 살아남았다. 실패/보류 판정된 4개 패턴의 코드와 그때의 백테스트 결과는
`archive/캔들차트_성승현/pattern_scan_6패턴버전_deprecated.py`에 보존돼 있으니
필요하면 그걸 참고해 재검증할 것 — 이 파일은 이제 "검증된 3개가 시간이 지나도
여전히 유효한지" 정기 점검하는 회귀 테스트 용도다.

방법: 각 종목의 전체 월봉 히스토리를 월 단위로 걸어가며(walk-forward), 매 시점
"그 시점까지의 데이터만 보고" bullish_pattern_scan.py의 탐지 함수를 그대로
호출한다. 전월엔 미완성이었다가 이번 달에 처음 완성 조건(broke_up)을 만족하는
순간만 "이벤트"로 기록하고(같은 패턴이 계속 유지되는 동안 중복 카운트 방지),
그 시점 종가 대비 +3/+6/+12개월 후 수익률을 실제로 계산한다.

⚠️ 한계 (반드시 감안할 것):
- 생존편향: 현재 nasdaq100 구성종목 기준이라, 과거에 지수에서 빠졌거나 상장폐지된
  종목은 표본에서 빠져있다. 실제 승률은 이보다 낮을 가능성이 있다.
- 패턴 탐지 로직 자체가 근사치(peak/trough 알고리즘, 넥라인 허용폭 등)라 원서가
  말하는 "육안 판단"과 100% 같지 않다.
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd

from bullish_pattern_scan import (
    fetch_monthly, LOOKBACK_MONTHS, find_ssangbadak, find_samjungbadak, find_inverse_hns,
)

HORIZONS = [3, 6, 12]  # 개월
MIN_START = LOOKBACK_MONTHS // 2
FORWARD_BUFFER = max(HORIZONS)

FINDERS = (find_ssangbadak, find_samjungbadak, find_inverse_hns)


def backtest_ticker(ticker: str, df: pd.DataFrame, events: list):
    n = len(df)
    if n < MIN_START + FORWARD_BUFFER + 1:
        return
    prev_state = {fn.__name__: False for fn in FINDERS}
    for i in range(MIN_START, n - FORWARD_BUFFER):
        sl = df.iloc[:i + 1]
        for fn in FINDERS:
            try:
                r = fn(sl)
            except Exception:
                r = None
            cur = bool(r and r.get('broke_up'))
            if cur and not prev_state[fn.__name__]:
                close0 = float(df['Close'].iloc[i])
                fwd = {h: float(df['Close'].iloc[i + h]) / close0 - 1 for h in HORIZONS}
                events.append({
                    'ticker': ticker, 'pattern': r['pattern'],
                    'date': df.index[i].strftime('%Y-%m'), **{f'fwd{h}m': fwd[h] for h in HORIZONS},
                })
            prev_state[fn.__name__] = cur


def run_backtest(tickers: list):
    events = []
    total = len(tickers)
    for n, t in enumerate(tickers, 1):
        print(f'  {n:>3}/{total} {t:<8}', end='\r')
        try:
            df = fetch_monthly(t)
        except Exception:
            continue
        backtest_ticker(t, df, events)
    print(' ' * 30, end='\r')
    return pd.DataFrame(events)


def print_report(df_events: pd.DataFrame, universe_label: str = 'nasdaq100'):
    print(f"\n{'=' * 100}\n  패턴 백테스트 결과 (월봉 기준, {universe_label}, 15년치)\n{'=' * 100}")
    if df_events.empty:
        print("  이벤트가 하나도 발견되지 않았습니다.")
        return
    for pattern, g in df_events.groupby('pattern'):
        print(f"\n  [{pattern}]  이벤트 {len(g)}건  (완성 후 상승을 기대)")
        for h in HORIZONS:
            col = f'fwd{h}m'
            vals = g[col].dropna()
            if vals.empty:
                continue
            avg = vals.mean() * 100
            med = vals.median() * 100
            hit = (vals > 0).mean() * 100
            print(f"    +{h:>2}개월: 평균 {avg:+6.1f}%  중앙값 {med:+6.1f}%  "
                  f"기대방향 적중률 {hit:5.1f}%  (n={len(vals)})")
    print(f"\n{'=' * 100}")
    print("  ⚠️ 생존편향 있음(현재 nasdaq100 구성종목 기준).")
    print('=' * 100 + '\n')


if __name__ == '__main__':
    from nasdaq100_scan import get_ndx100_tickers
    tickers = get_ndx100_tickers()
    print(f'\n  NASDAQ100 {len(tickers)}종목, 15년치 월봉으로 패턴 백테스트 시작...')
    df_events = run_backtest(tickers)
    df_events.to_csv('backtest_pattern_events.csv', index=False, encoding='utf-8-sig')
    print_report(df_events, universe_label='nasdaq100')
    print(f'  이벤트 상세 로그 저장: backtest_pattern_events.csv ({len(df_events)}건)')
