# -*- coding: utf-8 -*-
"""
성승현 원본 vs 현재 시스템 월봉 MA10 전략 백테스트 비교
  - 전략A (성승현 원본): 월봉 종가 > MA10 → 매수 / < MA10 → 매도
  - 전략B (현재 시스템): 신규돌파(+15% 이내) 또는 지지권(+5% 이내) 매수 / MA10 이탈 → 매도
"""
import sys, json, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

BASE    = os.path.dirname(os.path.abspath(__file__))
MA      = 10          # 이평선
PERIOD  = '5y'        # 백테스트 기간
CAPITAL = 10_000_000  # 초기 자본 (1천만원)
ENTRY_LIMIT = 15.0    # 현재 시스템 괴리율 상한 (%)
ZONE_PCT    =  5.0    # 지지권 기준 (%)

# 테스트 유니버스: 충분한 거래 이력이 있는 대표 종목
TEST_STOCKS = {
    # 미국 대형주 (5년 이상 데이터 확실)
    'NVDA': '엔비디아',
    'AVGO': '브로드컴',
    'AMD':  'AMD',
    'TSLA': '테슬라',
    'MSFT': '마이크로소프트',
    'AAPL': '애플',
    'META': '메타',
    'GOOGL':'구글',
    'AMZN': '아마존',
    'CRWD': '크라우드스트라이크',
    'PLTR': '팔란티어',
    'LHX':  'L3해리스',
    'RTX':  'RTX',
    'VRT':  '버티브',
    'MRVL': '마벨테크',
    'IONQ': '아이온큐',
    'RGTI': '리게티',
    'OKLO': '오클로',
    'QS':   '퀀텀스케이프',
    'OII':  '오세아니어링',
}


# ─────────────────────────────────────────────
# 백테스트 엔진
# ─────────────────────────────────────────────
def get_monthly(ticker: str) -> pd.DataFrame:
    t  = yf.Ticker(ticker)
    df = t.history(period=PERIOD, interval='1mo', auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df = df[['Open', 'Close']].dropna()
    if len(df) < MA + 3:
        return pd.DataFrame()
    df['MA10'] = df['Close'].rolling(MA).mean()
    return df.dropna()


def backtest_A(df: pd.DataFrame) -> dict:
    """전략A — 성승현 원본: 월봉 종가 > MA10 돌파 시 매수, 이탈 시 매도"""
    cash, shares = float(CAPITAL), 0.0
    in_pos = False
    entry_price = 0.0
    trades = []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        close = float(curr['Close'])
        ma    = float(curr['MA10'])
        p_close = float(prev['Close'])
        p_ma    = float(prev['MA10'])

        # 매수: 전월 < MA10, 이번달 종가 > MA10 (신규 돌파)
        if not in_pos and p_close < p_ma and close > ma:
            shares = cash / close
            entry_price = close
            cash = 0.0
            in_pos = True
            trades.append({'type': 'buy', 'date': df.index[i], 'price': close})

        # 매도: 종가 < MA10 (이탈)
        elif in_pos and close < ma:
            cash = shares * close
            pnl  = (close - entry_price) / entry_price * 100
            trades.append({'type': 'sell', 'date': df.index[i], 'price': close, 'pnl': round(pnl, 2)})
            shares = 0.0
            in_pos = False

    # 미청산 포지션
    final_val = shares * float(df.iloc[-1]['Close']) if in_pos else cash
    if in_pos:
        pnl = (float(df.iloc[-1]['Close']) - entry_price) / entry_price * 100
        trades.append({'type': 'hold', 'pnl': round(pnl, 2)})

    return _calc_result(trades, final_val)


def backtest_B(df: pd.DataFrame) -> dict:
    """전략B — 현재 시스템: 신규돌파(+15% 이내) or 지지권(+5% 이내) 매수"""
    cash, shares = float(CAPITAL), 0.0
    in_pos = False
    entry_price = 0.0
    trades = []

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        close = float(curr['Close'])
        ma    = float(curr['MA10'])
        p_close = float(prev['Close'])
        p_ma    = float(prev['MA10'])

        pct   = (close - ma) / ma * 100
        fresh = p_close < p_ma and close > ma   # 이번달 신규 돌파
        dip   = close > ma and not fresh and pct <= ZONE_PCT  # 지지권

        # 매수 조건
        buy_signal = (
            (fresh and pct <= ENTRY_LIMIT) or   # ★★ 신규돌파 +15% 이내
            (dip and pct >= 0)                  # ★ 지지권 +5% 이내
        )

        if not in_pos and buy_signal:
            shares = cash / close
            entry_price = close
            cash = 0.0
            in_pos = True
            sig = '신규돌파' if fresh else '지지권'
            trades.append({'type': 'buy', 'date': df.index[i], 'price': close, 'sig': sig})

        # 매도: MA10 이탈
        elif in_pos and close < ma:
            cash = shares * close
            pnl  = (close - entry_price) / entry_price * 100
            trades.append({'type': 'sell', 'date': df.index[i], 'price': close, 'pnl': round(pnl, 2)})
            shares = 0.0
            in_pos = False

    final_val = shares * float(df.iloc[-1]['Close']) if in_pos else cash
    if in_pos:
        pnl = (float(df.iloc[-1]['Close']) - entry_price) / entry_price * 100
        trades.append({'type': 'hold', 'pnl': round(pnl, 2)})

    return _calc_result(trades, final_val)


def backtest_BH(df: pd.DataFrame) -> dict:
    """단순 매수보유 (Buy & Hold) 비교"""
    buy   = float(df.iloc[0]['Close'])
    sell  = float(df.iloc[-1]['Close'])
    final = CAPITAL * (sell / buy)
    ret   = (sell - buy) / buy * 100
    return {'total_return': round(ret, 2), 'final': round(final),
            'n_trades': 1, 'win_rate': 100 if ret > 0 else 0,
            'trades': [{'pnl': round(ret, 2)}]}


def _calc_result(trades: list, final_val: float) -> dict:
    closed = [t for t in trades if t['type'] == 'sell']
    all_pnl = [t['pnl'] for t in trades if 'pnl' in t]
    wins    = [p for p in all_pnl if p > 0]
    total_r = (final_val - CAPITAL) / CAPITAL * 100
    return {
        'total_return': round(total_r, 2),
        'final':        round(final_val),
        'n_trades':     len(closed),
        'win_rate':     round(len(wins) / len(all_pnl) * 100, 1) if all_pnl else 0,
        'avg_win':      round(np.mean([p for p in all_pnl if p > 0]), 2) if wins else 0,
        'avg_loss':     round(np.mean([p for p in all_pnl if p <= 0]), 2) if any(p <= 0 for p in all_pnl) else 0,
        'trades':       trades,
    }


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────
def main():
    print(f'\n{"="*70}')
    print(f'  월봉 MA{MA} 전략 백테스트 비교  ({PERIOD} 기간, 초기자본 {CAPITAL//10000}만원)')
    print(f'{"="*70}')
    print(f'  전략A: 성승현 원본 (신규돌파 시 매수, MA10 이탈 시 매도)')
    print(f'  전략B: 현재 시스템 (신규돌파 +{ENTRY_LIMIT}% 이내 OR 지지권 +{ZONE_PCT}% 이내)')
    print(f'  전략C: 단순 매수보유 (Buy & Hold)')
    print(f'{"="*70}\n')

    results = []
    skipped = []

    for ticker, name in TEST_STOCKS.items():
        try:
            df = get_monthly(ticker)
            if df.empty:
                skipped.append(ticker)
                continue

            rA  = backtest_A(df)
            rB  = backtest_B(df)
            rBH = backtest_BH(df)

            results.append({
                'ticker': ticker, 'name': name,
                'A_ret': rA['total_return'], 'A_trades': rA['n_trades'], 'A_win': rA['win_rate'],
                'B_ret': rB['total_return'], 'B_trades': rB['n_trades'], 'B_win': rB['win_rate'],
                'BH_ret': rBH['total_return'],
                'winner': 'A' if rA['total_return'] > rB['total_return'] else 'B' if rB['total_return'] > rA['total_return'] else '동률',
            })
            print(f'  ✅ {ticker:6} {name[:10]:<12}  A:{rA["total_return"]:>+8.1f}%  B:{rB["total_return"]:>+8.1f}%  B&H:{rBH["total_return"]:>+8.1f}%')

        except Exception as e:
            skipped.append(f'{ticker}({e})')

    if not results:
        print('결과 없음')
        return

    # ── 종합 결과 ──
    df_r = pd.DataFrame(results)

    print(f'\n{"="*70}')
    print('  종목별 비교 결과')
    print(f'{"="*70}')
    print(f'  {"티커":<8} {"종목명":<14} {"전략A":>10} {"전략B":>10} {"B&H":>10}  {"승자"}')
    print(f'  {"-"*60}')
    for _, r in df_r.sort_values('A_ret', ascending=False).iterrows():
        mark = '← A승' if r['winner'] == 'A' else ('← B승' if r['winner'] == 'B' else '동률')
        print(f'  {r["ticker"]:<8} {r["name"][:13]:<14} {r["A_ret"]:>+9.1f}% {r["B_ret"]:>+9.1f}% {r["BH_ret"]:>+9.1f}%  {mark}')

    print(f'\n{"="*70}')
    print('  전체 평균 수익률')
    print(f'{"="*70}')
    print(f'  전략A (성승현 원본):   평균 {df_r["A_ret"].mean():>+8.1f}%   중앙값 {df_r["A_ret"].median():>+8.1f}%')
    print(f'  전략B (현재 시스템):   평균 {df_r["B_ret"].mean():>+8.1f}%   중앙값 {df_r["B_ret"].median():>+8.1f}%')
    print(f'  B&H  (단순보유):       평균 {df_r["BH_ret"].mean():>+8.1f}%   중앙값 {df_r["BH_ret"].median():>+8.1f}%')

    a_wins = (df_r['winner'] == 'A').sum()
    b_wins = (df_r['winner'] == 'B').sum()
    ties   = (df_r['winner'] == '동률').sum()
    print(f'\n  A vs B 대결:  A승 {a_wins}회  B승 {b_wins}회  동률 {ties}회  (총 {len(df_r)}종목)')

    avg_trades_A = df_r['A_trades'].mean()
    avg_trades_B = df_r['B_trades'].mean()
    print(f'  평균 거래횟수: A={avg_trades_A:.1f}회  B={avg_trades_B:.1f}회')

    print(f'\n{"="*70}')
    print('  해석 가이드')
    print(f'{"="*70}')
    print('  • 전략A = 전통적 MA10 돌파 시 무조건 진입')
    print(f'  • 전략B = 진입 필터 있음 (괴리율 +{ENTRY_LIMIT}% 이내 + 지지권 추가 진입)')
    print('  • B&H  = 기간 처음부터 끝까지 보유 (비교 기준)')

    if skipped:
        print(f'\n  ⚠️ 스킵: {", ".join(skipped)}')


if __name__ == '__main__':
    main()
