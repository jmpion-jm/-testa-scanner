# -*- coding: utf-8 -*-
"""박스권 판정: 꼬리(고가·저가) 기준 vs 몸통(시가·종가) 기준 비교 (2026-09-27)
계기: 성승현 작가 인터뷰 "전고점은 위쪽 꼬리가 아니라 몸통의 전고점", 원서 p.255 "꼬리가 아니라 시가 기준".
현재 book_patterns.in_box = 직전 12개월 (최고가/최저가−1) ≤ 30% 이고 종가 ≤ 그 최고가(꼬리 기준).
몸통 기준 = 직전 12개월 (max(시가,종가)/min(시가,종가)−1) ≤ w 이고 종가 ≤ 몸통 최고.
대상: S&P500 ∪ 나스닥100 ∪ 김학주 관심종목, 2000년 이후 원서 매수 신호 → 월말 10이평 이탈 매도.
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8'); warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import book_patterns as bp
from backtest_universe_compare import universes
from backtest_author_ideas import monthly_batch, stats, boot

kim, mkt = universes()
data = monthly_batch(set(kim) | set(mkt))
rows = []
for t, d in data.items():
    c = d['Close']; bh = d[['Open', 'Close']].max(axis=1); bl = d[['Open', 'Close']].min(axis=1)
    for k in range(12, len(d)):
        if d.index[k].year < 2000 or not bp.buy_signal(d, k):
            continue
        x = next((j for j in range(k + 1, len(d)) if c.iat[j] < d['MA'].iat[j]), None)
        if x is None:
            continue
        hi, lo = d['High'].iloc[k - 12:k].max(), d['Low'].iloc[k - 12:k].min()
        bhi, blo = bh.iloc[k - 12:k].max(), bl.iloc[k - 12:k].min()
        rows.append(dict(ret=c.iat[x] / c.iat[k] - 1, wick=bp.in_box(d, k),
                         brange=bhi / blo - 1, bbreak=c.iat[k] > bhi, wrange=hi / lo - 1, wbreak=c.iat[k] > hi))
ev = pd.DataFrame(rows)
print(f'\n신호 {len(ev)}건, 전체 {stats(ev.ret)}')
def show(name, m):
    a, b = ev[m].ret, ev[~m].ret
    lo, hi = boot(b, a) if len(a) > 30 else (np.nan, np.nan)
    print(f'[{name}] 박스 {m.mean():.1%}')
    print(f'   박스 안  {stats(a)}')
    print(f'   나머지   {stats(b)}')
    print(f'   나머지−박스 95% 구간 {lo:+.1%} ~ {hi:+.1%} | 박스 빼면 총손익 {b.sum() / ev.ret.sum():.1%} 유지')
show('현재: 꼬리 30%', ev.wick)
for w in (0.20, 0.25, 0.30):
    show(f'몸통 {w:.0%}', (ev.brange <= w) & ~ev.bbreak)
both = ev.wick & ((ev.brange <= 0.25) & ~ev.bbreak)
print(f'\n꼬리30% 박스 중 몸통 기준으론 이미 돌파(표시 해제될 신호): {(ev.wick & ev.bbreak).sum()}건 → 그 성과 {stats(ev[ev.wick & ev.bbreak].ret)}')
