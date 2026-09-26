# -*- coding: utf-8 -*-
"""
월봉 MA10 전략 파라미터 최적화
  - ENTRY_LIMIT (신규돌파 괴리율 상한): 다양한 값 테스트
  - ZONE_PCT    (지지권 기준):          다양한 값 테스트
  - 최적 조합 탐색
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import numpy as np
from itertools import product

MA      = 10
PERIOD  = '5y'
CAPITAL = 10_000_000

TEST_STOCKS = {
    'NVDA':'엔비디아','AVGO':'브로드컴','AMD':'AMD','TSLA':'테슬라',
    'MSFT':'마이크로소프트','AAPL':'애플','META':'메타','GOOGL':'구글',
    'AMZN':'아마존','CRWD':'크라우드스트라이크','PLTR':'팔란티어',
    'LHX':'L3해리스','RTX':'RTX','VRT':'버티브','MRVL':'마벨테크',
    'IONQ':'아이온큐','RGTI':'리게티','OKLO':'오클로','QS':'퀀텀스케이프',
    'OII':'오세아니어링',
}

# ── 데이터 사전 로드 ──────────────────────────────────────────
def load_all() -> dict:
    data = {}
    print('데이터 다운로드 중...')
    for ticker, name in TEST_STOCKS.items():
        try:
            t  = yf.Ticker(ticker)
            df = t.history(period=PERIOD, interval='1mo', auto_adjust=True)
            df.index = df.index.tz_localize(None) if df.index.tz else df.index
            df = df[['Open', 'Close']].dropna()
            if len(df) < MA + 3:
                continue
            df['MA10'] = df['Close'].rolling(MA).mean()
            df = df.dropna()
            data[ticker] = (name, df)
            print(f'  ✅ {ticker}', end='  ')
        except:
            pass
    print(f'\n로드 완료: {len(data)}종목\n')
    return data


# ── 백테스트 ─────────────────────────────────────────────────
def backtest(df: pd.DataFrame, entry_limit: float, zone_pct: float) -> float:
    """지정 파라미터로 백테스트 → 총 수익률(%) 반환"""
    cash, shares = float(CAPITAL), 0.0
    in_pos = False
    entry_price = 0.0

    for i in range(1, len(df)):
        curr    = df.iloc[i]
        prev    = df.iloc[i - 1]
        close   = float(curr['Close'])
        ma      = float(curr['MA10'])
        p_close = float(prev['Close'])
        p_ma    = float(prev['MA10'])
        pct     = (close - ma) / ma * 100
        fresh   = p_close < p_ma and close > ma
        dip     = close > ma and not fresh and 0 <= pct <= zone_pct

        buy = (fresh and pct <= entry_limit) or (zone_pct > 0 and dip)

        if not in_pos and buy:
            shares = cash / close
            entry_price = close
            cash = 0.0
            in_pos = True
        elif in_pos and close < ma:
            cash = shares * close
            shares = 0.0
            in_pos = False

    final = shares * float(df.iloc[-1]['Close']) if in_pos else cash
    return (final - CAPITAL) / CAPITAL * 100


# ── 최적화 ───────────────────────────────────────────────────
def optimize(data: dict):
    # 탐색 범위
    entry_limits = [5, 10, 15, 20, 25, 30, 50, 999]   # 999 = 무제한 (성승현 원본)
    zone_pcts    = [0, 3, 5, 7, 10]                     # 0 = 지지권 사용 안 함

    results = []
    total = len(entry_limits) * len(zone_pcts)
    done  = 0

    print(f'파라미터 조합 {total}개 탐색 중...\n')

    for el, zp in product(entry_limits, zone_pcts):
        rets = []
        for ticker, (name, df) in data.items():
            r = backtest(df, el, zp)
            rets.append(r)
        avg    = np.mean(rets)
        median = np.median(rets)
        wins   = sum(1 for r in rets if r > 0)
        results.append({
            'entry_limit': el if el < 999 else '무제한',
            'zone_pct':    zp,
            'avg':         round(avg, 1),
            'median':      round(median, 1),
            'win_count':   wins,
            'n':           len(rets),
        })
        done += 1
        print(f'  [{done:2d}/{total}] 진입상한={str(el)+"%" if el<999 else "무제한":>8}  지지권={zp:>2}%  '
              f'평균={avg:>+8.1f}%  중앙값={median:>+8.1f}%  승률={wins}/{len(rets)}')

    df_res = pd.DataFrame(results).sort_values('avg', ascending=False)

    print(f'\n{"="*75}')
    print('  상위 10개 파라미터 조합 (평균 수익률 기준)')
    print(f'{"="*75}')
    print(f'  {"진입상한":>8}  {"지지권":>6}  {"평균수익":>10}  {"중앙값":>10}  {"양수종목"}')
    print(f'  {"-"*65}')
    for _, r in df_res.head(10).iterrows():
        el_str = f'+{r["entry_limit"]}%' if r["entry_limit"] != '무제한' else '무제한'
        mark = ' ★ 최적' if _ == df_res.index[0] else ''
        print(f'  {el_str:>8}  +{r["zone_pct"]:>4}%  {r["avg"]:>+9.1f}%  '
              f'{r["median"]:>+9.1f}%  {r["win_count"]}/{r["n"]}{mark}')

    # 최적 파라미터
    best = df_res.iloc[0]
    print(f'\n{"="*75}')
    print('  최적 파라미터')
    print(f'{"="*75}')
    el_best = best['entry_limit']
    zp_best = int(best['zone_pct'])
    print(f'  ENTRY_LIMIT (신규돌파 괴리율 상한): {el_best}%' if el_best != '무제한' else f'  ENTRY_LIMIT: 무제한 (성승현 원본)')
    print(f'  ZONE_PCT    (지지권 기준):          +{zp_best}%' if zp_best > 0 else '  ZONE_PCT: 미사용')
    print(f'  기대 평균 수익률: {best["avg"]:+.1f}%')
    print(f'  기대 중앙값 수익률: {best["median"]:+.1f}%')

    # 현재 시스템(15%+5%)과 최적의 차이
    cur = df_res[(df_res['entry_limit'] == 15) & (df_res['zone_pct'] == 5)]
    if not cur.empty:
        cur_avg = float(cur.iloc[0]['avg'])
        print(f'\n  현재 시스템 (15%+5%) 평균:  {cur_avg:+.1f}%')
        print(f'  최적 파라미터 대비 개선:    {best["avg"] - cur_avg:+.1f}%p')

    return best


def main():
    print(f'\n{"="*75}')
    print(f'  월봉 MA{MA} 전략 파라미터 최적화  ({PERIOD}, 초기자본 {CAPITAL//10000}만원)')
    print(f'{"="*75}\n')

    data = load_all()
    if not data:
        print('데이터 없음')
        return

    best = optimize(data)
    print(f'\n{"="*75}')
    print('  slack_alert.py 적용 권장값')
    print(f'{"="*75}')
    el = best['entry_limit']
    zp = int(best['zone_pct'])
    if el == '무제한':
        print('  ENTRY_LIMIT = 999  # 사실상 무제한 (성승현 원본)')
    else:
        print(f'  ENTRY_LIMIT = {el}.0')
    print(f'  ZONE_PCT    = {zp}.0')


if __name__ == '__main__':
    main()
