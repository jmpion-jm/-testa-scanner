# -*- coding: utf-8 -*-
import yfinance as yf
import pandas as pd
import numpy as np
import json, os
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    _CFG = json.load(f)

US_STOCKS = {ticker: name for ticker, (name, sector) in _CFG['stocks'].items()}

MA_SHORT, MA_MID, MA_LONG = 5, 25, 75
VOLUME_MULT = 1.5

def get_slope(series, window=5):
    y = series.dropna().tail(window).values
    if len(y) < window:
        return 0.0
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    return slope / y.mean() if y.mean() != 0 else 0.0


# ── 1. 현재 신호 스캔 ──────────────────────────────────────────
print('=== 미국종목 테스타 조건 현재 스캔 ===')
print(f'  기준일: {datetime.today().strftime("%Y-%m-%d")}')
print()

current_signals = []
status_rows = []

for ticker, name in US_STOCKS.items():
    try:
        t  = yf.Ticker(ticker)
        df = t.history(period='1y', interval='1d', auto_adjust=True)
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        df = df[['Open','High','Low','Close','Volume']].dropna()
        if len(df) < MA_LONG + 5:
            continue

        df['MA5']  = df['Close'].rolling(MA_SHORT).mean()
        df['MA25'] = df['Close'].rolling(MA_MID).mean()
        df['MA75'] = df['Close'].rolling(MA_LONG).mean()
        df = df.dropna()

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        bull     = bool(curr['MA5'] > curr['MA25'] > curr['MA75'])
        s5       = get_slope(df['MA5'])
        s25      = get_slope(df['MA25'])
        s75      = get_slope(df['MA75'])
        slope_ok = s5 > 0 and s25 > 0 and s75 > 0

        avg_vol  = df['Volume'].iloc[-21:-1].mean()
        vol_r    = float(curr['Volume']) / avg_vol if avg_vol > 0 else 0

        dipped   = bool(prev['Close'] < prev['MA5'])
        breakout = bool(curr['Close'] > curr['MA5'])
        signal   = bull and slope_ok and (vol_r >= VOLUME_MULT) and dipped and breakout

        if signal:
            stop   = float(df['Low'].iloc[-6:-1].min())
            risk   = (float(curr['Close']) - stop) / float(curr['Close']) * 100
            target = float(curr['Close']) * (1 + risk / 100 * 3)
            current_signals.append({
                'ticker': ticker, 'name': name,
                'entry': int(curr['Close']), 'stop': int(stop),
                'target': int(target), 'risk': round(risk, 1),
                'vol_r': round(vol_r, 1),
            })

        status_rows.append({
            'ticker': ticker, 'name': name,
            'bull': bull, 'slope': slope_ok,
            'vol_r': round(vol_r, 1),
            'signal': signal,
        })
    except Exception as e:
        pass

# 트래커 연동 — 신호 자동 기록
try:
    import signal_tracker as tracker
    for s in current_signals:
        tracker.record_signal(s['ticker'], s['name'], '테스타일봉',
                              s['entry'], s['entry'],
                              stop=s['stop'], target=s['target'])
except Exception as e:
    print(f'[트래커] {e}')

if current_signals:
    print('★ 현재 신호 종목:')
    for s in current_signals:
        print(f'  {s["name"]}({s["ticker"]})  진입 {s["entry"]:,}'
              f'  손절 {s["stop"]:,}  목표 {s["target"]:,}'
              f'  리스크 -{s["risk"]}%  거래량 {s["vol_r"]}x')
else:
    print('  현재 신호 없음')

print()
print('  [전체 상태 — 정배열 충족 여부]')
print(f'  {"종목":<14} {"정배열":>5} {"기울기":>6} {"거래량":>7}')
print('  ' + '-'*36)
for r in status_rows:
    bull_s = 'OK' if r['bull'] else '--'
    slp_s  = 'OK' if r['slope'] else '--'
    vol_s  = f'{r["vol_r"]}x'
    print(f'  {r["name"]:<14} {bull_s:>5}  {slp_s:>5}  {vol_s:>6}')


# ── 2. 과거 6개월 백테스트 ─────────────────────────────────────
print()
print('=== 미국종목 테스타 과거 6개월 백테스트 ===')
all_sig = []

for ticker, name in US_STOCKS.items():
    try:
        t  = yf.Ticker(ticker)
        df = t.history(period='2y', interval='1d', auto_adjust=True)
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        df = df[['High','Low','Close','Volume']].dropna()
        if len(df) < MA_LONG + 10:
            continue

        df['MA5']  = df['Close'].rolling(MA_SHORT).mean()
        df['MA25'] = df['Close'].rolling(MA_MID).mean()
        df['MA75'] = df['Close'].rolling(MA_LONG).mean()
        df = df.dropna()

        cutoff = pd.Timestamp.today() - pd.DateOffset(months=6)

        for i in range(MA_LONG + 5, len(df) - 1):
            if df.index[i] < cutoff:
                continue

            sub  = df.iloc[:i+1]
            curr = sub.iloc[-1]
            prev = sub.iloc[-2]

            if not (curr['MA5'] > curr['MA25'] > curr['MA75']):
                continue
            if curr['Close'] < curr['MA75']:
                continue

            s5  = get_slope(sub['MA5'])
            s25 = get_slope(sub['MA25'])
            s75 = get_slope(sub['MA75'])
            if not (s5 > 0 and s25 > 0 and s75 > 0):
                continue

            if not (prev['Close'] < prev['MA5'] and curr['Close'] > curr['MA5']):
                continue

            avg_vol = sub['Volume'].iloc[-21:-1].mean()
            if curr['Volume'] < avg_vol * VOLUME_MULT:
                continue

            entry  = float(curr['Close'])
            stop   = float(sub['Low'].iloc[-6:-1].min())
            risk   = (entry - stop) / entry * 100
            target = entry * (1 + risk / 100 * 3)

            future = df.iloc[i+1:i+21]
            if future.empty:
                continue

            result = 'hold'
            for j in range(len(future)):
                row = future.iloc[j]
                if row['High'] >= target:
                    result = 'target'
                    break
                elif row['Low'] <= stop:
                    result = 'stop'
                    break

            ret20 = (float(future['Close'].iloc[-1]) - entry) / entry * 100

            all_sig.append({
                'date': df.index[i].strftime('%Y-%m-%d'),
                'ticker': ticker, 'name': name,
                'risk': round(risk, 1), 'result': result,
                'ret20': round(ret20, 1),
            })
    except Exception as e:
        pass

if all_sig:
    ds     = pd.DataFrame(all_sig)
    closed = ds[ds['result'] != 'hold']
    tgt    = closed[closed['result'] == 'target']
    stp    = closed[closed['result'] == 'stop']
    wr     = len(tgt) / len(closed) * 100 if len(closed) else 0
    avg_r  = ds['risk'].mean()
    ev     = (wr / 100 * avg_r * 3) - ((100 - wr) / 100 * avg_r)

    print(f'  총 신호: {len(ds)}건  |  완료: {len(closed)}건  |  진행중: {len(ds)-len(closed)}건')
    print(f'  목표달성: {len(tgt)}건 ({wr:.1f}%)  |  손절: {len(stp)}건 ({100-wr:.1f}%)')
    print(f'  평균 리스크: -{avg_r:.1f}%  |  기대수익(EV): {ev:+.2f}%/거래')
    print()
    print(f'  {"날짜":<12} {"종목":<14} {"리스크":>7} {"결과":<10} {"20일수익":>8}')
    print('  ' + '-'*55)
    for _, r in ds.sort_values('date').iterrows():
        icon = '✅' if r['result'] == 'target' else ('❌' if r['result'] == 'stop' else '⏳')
        print(f'  {r["date"]:<12} {r["name"]:<14} -{r["risk"]:>5.1f}%  {icon}{r["result"]:<8} {r["ret20"]:>+7.1f}%')

    print()
    print('=== 한국 vs 미국 테스타 비교 ===')
    print(f'  {"구분":<12} {"신호수":>6} {"승률":>6} {"평균리스크":>10} {"EV/거래":>9}')
    print('  ' + '-'*45)
    print(f'  {"미국종목":<12} {len(ds):>6}건  {wr:>5.1f}%  -{avg_r:>8.1f}%  {ev:>+8.2f}%')
    print(f'  {"한국KOSPI":<12} {"38":>6}건  {"46.2":>5}%  -{"10.4":>8}%  {8.7:>+8.2f}%')
else:
    print('  신호 없음')
