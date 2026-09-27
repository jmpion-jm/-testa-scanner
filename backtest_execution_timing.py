# -*- coding: utf-8 -*-
"""실행 시점 영향 검증(2026-09-27): 말일 종가 매수·매도(검증 가정) vs 다음 날 첫 거래가(월봉 다음 달 시가) 매수·매도.
작가 인터뷰 "말일 새벽 미국 마감 전에 확인하고 판다" 필요성 확인용. 결과는 캔들차트(성승현작가)/유튜브_검토기록.md."""
import sys, warnings; warnings.filterwarnings('ignore')
sys.path.insert(0, '.')
import numpy as np, pandas as pd
import book_patterns as bp
from backtest_universe_compare import universes
from backtest_author_ideas import monthly_batch
kim, mkt = universes()
data = monthly_batch(set(kim) | set(mkt))
rows = []
for t, d in data.items():
    c, o = d['Close'], d['Open']
    for k in range(11, len(d) - 1):
        if d.index[k].year < 2000 or not bp.buy_signal(d, k):
            continue
        x = next((j for j in range(k + 1, len(d) - 1) if c.iat[j] < d['MA'].iat[j]), None)
        if x is None:
            continue
        ideal = c.iat[x] / c.iat[k] - 1                    # 월말 종가 매수·월말 종가 매도(검증 가정)
        real = o.iat[x + 1] / o.iat[k + 1] - 1             # 다음 달 첫날 시가 매수·다음 달 첫날 시가 매도(지금 실행 방식 근사)
        sell_close = o.iat[k + 1] and c.iat[x] / o.iat[k + 1] - 1   # 매수는 다음날, 매도는 말일 종가(새벽 확인)
        rows.append((ideal, real, sell_close, o.iat[k + 1] / c.iat[k] - 1, o.iat[x + 1] / c.iat[x] - 1))
r = pd.DataFrame(rows, columns=['ideal', 'real', 'sell_close', 'buy_gap', 'sell_gap'])
print(f'거래 {len(r)}건')
print(f'검증 가정(말일 종가 매수·매도)      평균 {r.ideal.mean():+.2%}  승률 {(r.ideal>0).mean():.1%}')
print(f'지금 방식(다음날 시가 매수·매도)    평균 {r.real.mean():+.2%}  승률 {(r.real>0).mean():.1%}')
print(f'다음날 매수 + 말일 종가 매도(새벽) 평균 {r.sell_close.mean():+.2%}  승률 {(r.sell_close>0).mean():.1%}')
print(f'매수 하루 늦을 때 가격 차이: 평균 {r.buy_gap.mean():+.2%} (중앙 {r.buy_gap.median():+.2%}) | 매도 하루 늦을 때: 평균 {r.sell_gap.mean():+.2%} (중앙 {r.sell_gap.median():+.2%})')
