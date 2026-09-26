# -*- coding: utf-8 -*-
"""
3대 전략 백테스트 비교
  전략1. B&H        — 처음 사서 끝까지 보유
  전략2. 월봉MA10   — 성승현 원본 (신규돌파 무제한)
  전략3. 테스타     — 일봉 MA5/25/75 정배열 + 눌림목 돌파
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import numpy as np

MA_MONTHLY  = 10
PERIOD      = '5y'
CAPITAL     = 10_000_000

# 테스타 파라미터
MA_S, MA_M, MA_L = 5, 25, 75
VOL_MULT  = 1.5
MAX_RISK  = 15.0   # 손절폭 상한 (%)
RR_RATIO  = 3.0    # 손익비
MAX_HOLD  = 20     # 최대 보유일

TEST_STOCKS = {
    'NVDA':'엔비디아',   'AVGO':'브로드컴',   'AMD':'AMD',
    'TSLA':'테슬라',     'MSFT':'마이크로소프트','AAPL':'애플',
    'META':'메타',       'GOOGL':'구글',       'AMZN':'아마존',
    'CRWD':'크라우드스트라이크','PLTR':'팔란티어','LHX':'L3해리스',
    'RTX':'RTX',         'VRT':'버티브',       'MRVL':'마벨테크',
    'IONQ':'아이온큐',   'RGTI':'리게티',      'OKLO':'오클로',
    'QS':'퀀텀스케이프', 'OII':'오세아니어링',
}


# ─────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────
def load_monthly(ticker: str) -> pd.DataFrame:
    t  = yf.Ticker(ticker)
    df = t.history(period=PERIOD, interval='1mo', auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df = df[['Open', 'Close']].dropna()
    if len(df) < MA_MONTHLY + 3:
        return pd.DataFrame()
    df['MA10'] = df['Close'].rolling(MA_MONTHLY).mean()
    return df.dropna()


def load_daily(ticker: str) -> pd.DataFrame:
    t  = yf.Ticker(ticker)
    df = t.history(period=PERIOD, interval='1d', auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    if len(df) < MA_L + 10:
        return pd.DataFrame()
    df['MA5']  = df['Close'].rolling(MA_S).mean()
    df['MA25'] = df['Close'].rolling(MA_M).mean()
    df['MA75'] = df['Close'].rolling(MA_L).mean()
    return df.dropna()


# ─────────────────────────────────────────────
# 전략 1: B&H
# ─────────────────────────────────────────────
def bt_bh(df_m: pd.DataFrame) -> dict:
    buy   = float(df_m.iloc[0]['Close'])
    sell  = float(df_m.iloc[-1]['Close'])
    ret   = (sell - buy) / buy * 100
    final = CAPITAL * (sell / buy)
    return {'ret': round(ret, 2), 'final': round(final),
            'trades': 1, 'win_rate': 100.0 if ret > 0 else 0.0,
            'avg_win': round(ret, 2) if ret > 0 else 0,
            'avg_loss': round(ret, 2) if ret <= 0 else 0}


# ─────────────────────────────────────────────
# 전략 2: 월봉 MA10 (성승현 원본)
# ─────────────────────────────────────────────
def bt_monthly(df_m: pd.DataFrame) -> dict:
    cash, shares = float(CAPITAL), 0.0
    in_pos = False
    entry_price = 0.0
    closed_pnl = []

    for i in range(1, len(df_m)):
        curr  = df_m.iloc[i]
        prev  = df_m.iloc[i - 1]
        close = float(curr['Close'])
        ma    = float(curr['MA10'])
        fresh = float(prev['Close']) < float(prev['MA10']) and close > ma

        if not in_pos and fresh:
            shares = cash / close
            entry_price = close
            cash = 0.0
            in_pos = True

        elif in_pos and close < ma:
            cash = shares * close
            closed_pnl.append((close - entry_price) / entry_price * 100)
            shares = 0.0
            in_pos = False

    if in_pos:
        final_price = float(df_m.iloc[-1]['Close'])
        cash = shares * final_price
        closed_pnl.append((final_price - entry_price) / entry_price * 100)

    pnl_list = closed_pnl
    wins  = [p for p in pnl_list if p > 0]
    loss_ = [p for p in pnl_list if p <= 0]
    total_ret = (cash - CAPITAL) / CAPITAL * 100
    return {
        'ret': round(total_ret, 2), 'final': round(cash),
        'trades': len(pnl_list),
        'win_rate': round(len(wins) / len(pnl_list) * 100, 1) if pnl_list else 0,
        'avg_win':  round(np.mean(wins), 2)  if wins  else 0,
        'avg_loss': round(np.mean(loss_), 2) if loss_ else 0,
    }


# ─────────────────────────────────────────────
# 전략 3: 테스타 (일봉 MA5/25/75 정배열 + 눌림목)
# ─────────────────────────────────────────────
def _slope(series: pd.Series, i: int, w: int = 5) -> float:
    seg = series.iloc[max(0, i - w + 1): i + 1].values
    if len(seg) < w or seg.mean() == 0:
        return 0.0
    x = np.arange(len(seg))
    return float(np.polyfit(x, seg, 1)[0]) / seg.mean()


def bt_testa(df_d: pd.DataFrame) -> dict:
    cash, shares = float(CAPITAL), 0.0
    in_pos = False
    entry_price = stop_loss = target_price = 0.0
    hold_days = 0
    closed_pnl = []

    for i in range(1, len(df_d)):
        curr  = df_d.iloc[i]
        prev  = df_d.iloc[i - 1]
        close = float(curr['Close'])

        if in_pos:
            hold_days += 1
            low = float(curr['Low'])
            high = float(curr['High'])

            if low <= stop_loss:
                exit_price = min(close, stop_loss)
                cash = shares * exit_price
                closed_pnl.append((exit_price - entry_price) / entry_price * 100)
                shares = 0.0; in_pos = False
            elif high >= target_price:
                cash = shares * target_price
                closed_pnl.append((target_price - entry_price) / entry_price * 100)
                shares = 0.0; in_pos = False
            elif hold_days >= MAX_HOLD:
                cash = shares * close
                closed_pnl.append((close - entry_price) / entry_price * 100)
                shares = 0.0; in_pos = False
        else:
            ma5  = float(curr['MA5'])
            ma25 = float(curr['MA25'])
            ma75 = float(curr['MA75'])

            bull      = ma5 > ma25 > ma75
            s5        = _slope(df_d['MA5'],  i)
            s25       = _slope(df_d['MA25'], i)
            s75       = _slope(df_d['MA75'], i)
            slope_ok  = s5 > 0 and s25 > 0 and s75 > 0

            avg_vol   = float(df_d['Volume'].iloc[max(0, i-21):i].mean()) or 1
            vol_ok    = float(curr['Volume']) / avg_vol >= VOL_MULT

            dipped    = float(prev['Close']) < float(prev['MA5'])
            breakout  = close > ma5

            if bull and slope_ok and vol_ok and dipped and breakout:
                stop  = float(df_d['Low'].iloc[max(0, i-5):i].min())
                risk  = (close - stop) / close * 100

                if 0 < risk <= MAX_RISK:
                    tgt  = close * (1 + risk / 100 * RR_RATIO)
                    shares = cash / close
                    entry_price = close
                    stop_loss   = stop
                    target_price = tgt
                    cash = 0.0
                    in_pos = True
                    hold_days = 0

    if in_pos:
        final_price = float(df_d.iloc[-1]['Close'])
        cash = shares * final_price
        closed_pnl.append((final_price - entry_price) / entry_price * 100)

    wins  = [p for p in closed_pnl if p > 0]
    loss_ = [p for p in closed_pnl if p <= 0]
    total_ret = (cash - CAPITAL) / CAPITAL * 100
    return {
        'ret': round(total_ret, 2), 'final': round(cash),
        'trades': len(closed_pnl),
        'win_rate': round(len(wins) / len(closed_pnl) * 100, 1) if closed_pnl else 0,
        'avg_win':  round(np.mean(wins), 2)  if wins  else 0,
        'avg_loss': round(np.mean(loss_), 2) if loss_ else 0,
    }


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    print(f'\n{"="*80}')
    print(f'  3대 전략 백테스트 비교  ({PERIOD} / 초기자본 {CAPITAL//10000}만원 / {len(TEST_STOCKS)}종목)')
    print(f'{"="*80}')
    print(f'  전략1. B&H       — 처음 매수 후 끝까지 보유')
    print(f'  전략2. 월봉MA10  — 성승현 원본 (신규돌파 무제한 + MA10 이탈 매도)')
    print(f'  전략3. 테스타    — 일봉 MA5/25/75 정배열 + 눌림목 돌파 (손익비 1:{RR_RATIO:.0f})')
    print(f'{"="*80}\n')

    results = []
    for ticker, name in TEST_STOCKS.items():
        try:
            df_m = load_monthly(ticker)
            df_d = load_daily(ticker)
            if df_m.empty or df_d.empty:
                print(f'  ⚠️  {ticker} 데이터 부족')
                continue

            r1 = bt_bh(df_m)
            r2 = bt_monthly(df_m)
            r3 = bt_testa(df_d)

            winner = max([('B&H', r1['ret']), ('월봉', r2['ret']), ('테스타', r3['ret'])],
                         key=lambda x: x[1])[0]

            results.append({
                'ticker': ticker, 'name': name,
                'bh': r1['ret'],  'bh_t': r1['trades'],
                'ma': r2['ret'],  'ma_t': r2['trades'],  'ma_w': r2['win_rate'],
                'ts': r3['ret'],  'ts_t': r3['trades'],  'ts_w': r3['win_rate'],
                'winner': winner,
            })
            print(f'  ✅ {ticker:6} {name[:12]:<13} '
                  f'B&H:{r1["ret"]:>+8.1f}%  '
                  f'월봉:{r2["ret"]:>+8.1f}% ({r2["trades"]}회)  '
                  f'테스타:{r3["ret"]:>+8.1f}% ({r3["trades"]}회)  '
                  f'→ {winner}')
        except Exception as e:
            print(f'  ❌ {ticker}: {e}')

    if not results:
        return

    df_r = pd.DataFrame(results)

    print(f'\n{"="*80}')
    print('  종목별 결과 (수익률 기준 정렬)')
    print(f'{"="*80}')
    print(f'  {"티커":<7} {"종목명":<13} {"B&H":>9} {"월봉MA10":>10} {"테스타":>9}  승자')
    print(f'  {"-"*65}')
    for _, r in df_r.sort_values('bh', ascending=False).iterrows():
        print(f'  {r["ticker"]:<7} {r["name"][:12]:<13} '
              f'{r["bh"]:>+8.1f}%  {r["ma"]:>+9.1f}%  {r["ts"]:>+8.1f}%  '
              f'{"★ " if r["winner"]=="B&H" else "  "}{r["winner"]}')

    # ── 통계 ──
    print(f'\n{"="*80}')
    print('  전체 평균 / 중앙값 / 승률')
    print(f'{"="*80}')
    for col, label in [('bh','B&H      '), ('ma','월봉MA10 '), ('ts','테스타   ')]:
        avg    = df_r[col].mean()
        med    = df_r[col].median()
        wins   = (df_r[col] > 0).sum()
        n      = len(df_r)
        print(f'  {label}: 평균 {avg:>+8.1f}%  중앙값 {med:>+8.1f}%  양수 {wins}/{n}')

    # ── 전략별 승리 횟수 ──
    w_bh = (df_r['winner'] == 'B&H').sum()
    w_ma = (df_r['winner'] == '월봉').sum()
    w_ts = (df_r['winner'] == '테스타').sum()
    print(f'\n  종목별 최고 수익 전략:  B&H {w_bh}회  /  월봉MA10 {w_ma}회  /  테스타 {w_ts}회')

    # ── 거래 횟수 비교 ──
    avg_ma_t = df_r['ma_t'].mean()
    avg_ts_t = df_r['ts_t'].mean()
    print(f'  평균 거래 횟수:          월봉MA10 {avg_ma_t:.1f}회  /  테스타 {avg_ts_t:.1f}회')
    print(f'  평균 승률:               월봉MA10 {df_r["ma_w"].mean():.1f}%  /  테스타 {df_r["ts_w"].mean():.1f}%')

    # ── 손익 곡선 (가상 1천만원, 20종목 균등) ──
    share = CAPITAL / len(df_r)
    tot_bh = sum(share * (1 + r['bh']/100) for _, r in df_r.iterrows())
    tot_ma = sum(share * (1 + r['ma']/100) for _, r in df_r.iterrows())
    tot_ts = sum(share * (1 + r['ts']/100) for _, r in df_r.iterrows())

    print(f'\n{"="*80}')
    print(f'  1천만원 균등 분산 시 최종 자산 (20종목 × 50만원)')
    print(f'{"="*80}')
    print(f'  B&H      : {tot_bh:>13,.0f}원  ({(tot_bh-CAPITAL)/CAPITAL*100:>+.1f}%)')
    print(f'  월봉MA10 : {tot_ma:>13,.0f}원  ({(tot_ma-CAPITAL)/CAPITAL*100:>+.1f}%)')
    print(f'  테스타   : {tot_ts:>13,.0f}원  ({(tot_ts-CAPITAL)/CAPITAL*100:>+.1f}%)')

    print(f'\n{"="*80}')
    print('  해석 가이드')
    print(f'{"="*80}')
    print('  • B&H    : 신경 안 써도 되는 최강 전략 (강세장 한정)')
    print('  • 월봉MA10: 하락장 방어 + 추세 추종, 연간 2~3회 확인')
    print(f'  • 테스타 : 단기 모멘텀, 연간 {avg_ts_t:.0f}회 매매, 높은 참여도 필요')


if __name__ == '__main__':
    main()
