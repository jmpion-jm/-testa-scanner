# -*- coding: utf-8 -*-
"""
월봉매매법 — 한국(코스피200) vs 미국(기존 88종목) 비교 (2026-09-26 사용자 요청: "미국은 양도세가 있어 불리할 수
있으니 한국은 코스피200에서 발굴하는 게 맞는가 → 비교해줘")

같은 규칙: 원서 매수 신호(book_patterns.buy_signal — 돌파·10이평 지지) 월말 종가 매수 → 월말 종가 10이평 이탈 매도.
같은 기간: 2000-01 이후 신호만(한국 월봉 데이터가 2000년 전후부터라 미국도 맞춤).
세금(단순 추정): 미국 = 이익 거래에 22%(연 250만원 공제·같은 해 손익 상계 무시 → 세금을 크게 잡은 보수적 추정),
                한국 = 소액주주 양도세 없음, 매도 거래세 0.2%.
탑다운(원서 p.394·396): 신호 달에 지수(코스피 ^KS11 / S&P500 ^GSPC)가 월말 10이평 위였는지로 나눠 본다.
유니버스: 코스피200 = 위키백과 현재 구성 종목, 미국 = 기존 88종목. 둘 다 "지금 살아남은 종목"이라 생존편향이 있다 —
         절대 수치보다 두 시장의 차이를 볼 것. 환율 효과는 넣지 않음(미국은 달러 수익률).
실행: python backtest_korea_vs_us.py   (결과: backtest_korea_vs_us_trades.csv)
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
from io import StringIO
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import book_patterns as bp

OUT = 'backtest_korea_vs_us_trades.csv'
START = 2000
US_TAX, KR_TAX = 0.22, 0.002


def kospi200():
    r = requests.get('https://en.wikipedia.org/wiki/KOSPI_200', headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
    t = [x for x in pd.read_html(StringIO(r.text)) if 'Symbol' in x.columns and len(x) > 50][0]
    return [str(s).split('.')[0].zfill(6) for s in t['Symbol']]


def monthly(t):
    df = yf.Ticker(t).history(period='max', interval='1mo', auto_adjust=True)
    if df.empty:
        return None
    df.index = df.index.tz_localize(None)
    df = df[df.index < pd.Timestamp.today().replace(day=1)]
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def index_above(t):
    d = bp.prepare(monthly(t))
    return {ix.to_period('M'): bool(a) for ix, a in zip(d.index, d['above'])}


def trades(t, market, idx_up):
    df = monthly(t)
    if df is None or len(df) < 30:
        return []
    d = bp.prepare(df)
    c = d['Close']
    out = []
    for k in range(12, len(d)):
        if d.index[k].year < START:
            continue
        sig = bp.buy_signal(d, k)
        if not sig:
            continue
        x = next((j for j in range(k + 1, len(d)) if c.iat[j] < d['MA'].iat[j]), None)
        if x is None:
            continue
        r = c.iat[x] / c.iat[k] - 1
        after = r * (1 - US_TAX) if (market == 'US' and r > 0) else (r - KR_TAX if market == 'KR' else r)
        out.append(dict(market=market, ticker=t, month=str(d.index[k].to_period('M')), sig=sig, ret=r, ret_after=after,
                        months=x - k, idx_up=idx_up.get(d.index[k].to_period('M'))))
    return out


def stats(g, col='ret'):
    r = g[col]
    if not len(r):
        return 'n=0'
    trim = r[r <= r.quantile(0.98)]
    return (f'n={len(r):>5} 평균 {r.mean():+6.1%} 상위2%제외 {trim.mean():+6.1%} 중앙 {r.median():+6.1%} '
            f'승률 {(r > 0).mean():5.1%} +50%↑ {(r >= .5).mean():5.1%} -20%↓ {(r <= -.2).mean():4.1%} '
            f'보유 {g.months.mean():4.1f}개월 월당 {(r / g.months).mean():+5.2%}')


def boot(a, b, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    a, b = a.values, b.values
    return np.percentile([rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)], [2.5, 97.5])


def summarize(ev):
    ev = ev.copy()
    ev['yr'] = ev.month.str[:4].astype(int)
    print('\n' + '=' * 120)
    print(f'  월봉매매법 한국(코스피200) vs 미국(88종목) — {START}년 이후 원서 매수 신호, 월말 10이평 이탈 매도')
    print('=' * 120)
    for m, name in (('US', '미국'), ('KR', '한국')):
        g = ev[ev.market == m]
        print(f'\n[{name}] 종목 {g.ticker.nunique()}개')
        print('  세전           ', stats(g))
        print('  세후(단순추정)  ', stats(g, 'ret_after'))
        for up, lab in ((True, '지수 10이평 위'), (False, '지수 10이평 아래')):
            print(f'  {lab:<12}   ', stats(g[g.idx_up == up]))
    us, kr = ev[ev.market == 'US'], ev[ev.market == 'KR']
    for col, lab in (('ret', '세전'), ('ret_after', '세후')):
        lo, hi = boot(us[col], kr[col])
        print(f'\n  미국−한국 거래당 평균 차이({lab}) 95% 구간: {lo:+.1%} ~ {hi:+.1%}')
    print('\n[구간별 거래당 평균 세후 (미국 | 한국)]')
    for a, b in ((2000, 2009), (2010, 2019), (2020, 2026)):
        s = ev[(ev.yr >= a) & (ev.yr <= b)]
        u, k = s[s.market == 'US'], s[s.market == 'KR']
        print(f'  {a}~{b}: 미국 n={len(u):>4} {u.ret_after.mean():+6.1%} 승률 {(u.ret > 0).mean():5.1%} | '
              f'한국 n={len(k):>4} {k.ret_after.mean():+6.1%} 승률 {(k.ret > 0).mean():5.1%}')


if __name__ == '__main__':
    us_up, kr_up = index_above('^GSPC'), index_above('^KS11')
    us = sorted(pd.read_csv('archive/backtest_pattern_events_폐기_2026-09-12판.csv').ticker.unique())
    kr = kospi200()
    rows = []
    for market, lst, up, suf in (('US', us, us_up, ''), ('KR', kr, kr_up, '.KS')):
        for i, t in enumerate(lst, 1):
            print(f'  [{market}] {i:>3}/{len(lst)} {t:<8}', end='\r')
            try:
                rows += trades(t + suf, market, up)
            except Exception as e:
                print(f'\n  {t} 실패: {e}')
    ev = pd.DataFrame(rows)
    ev.to_csv(OUT, index=False, encoding='utf-8-sig')
    summarize(ev)
