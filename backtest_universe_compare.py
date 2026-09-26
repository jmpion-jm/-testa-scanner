# -*- coding: utf-8 -*-
"""
후보군 비교: 김학주 관심종목(config stocks) vs 시장 전체(S&P500 ∪ 나스닥100) — 같은 월봉매매법 규칙
(2026-09-26 사용자 요청: "김학주 종목과 S&P, 나스닥100 종목을 가지고 시뮬레이션 돌리면 어떻게 되는가")

규칙: 원서 매수 신호(book_patterns.buy_signal — 돌파·10이평 지지) 월말 종가 매수 → 월말 종가 10이평 이탈 매도.
구간:
  A. 2000~2026 전체 — 청산된 거래만. ⚠️ 김학주 목록은 2026년에 고른 종목이라 "지금 유망해 보이는 종목"을 과거에 산 셈
     (사후 선택 편향) + 두 후보군 모두 현재 구성 종목(생존편향). 김학주 쪽이 좋게 나오는 게 당연 — 참고용.
  B. 2026-05 이후 신호 — 김학주 교수 자료(가장 오래된 문서 2026-05-03)가 나온 뒤라 공정한 구간. 보유 중인 거래는
     현재가로 평가(미확정). 기간이 몇 달뿐이라 초기 결과.
  비교군 C = 시장 전체에서 김학주 종목을 뺀 나머지(겹치는 종목 효과 제거).
실행: python backtest_universe_compare.py   (결과: backtest_universe_compare_trades.csv)
"""
import sys, json, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf
import book_patterns as bp

OUT = 'backtest_universe_compare_trades.csv'
START, OOS = 2000, '2026-05'


def universes():
    kim = [t for t in json.load(open('config.json', encoding='utf-8'))['stocks'] if t != 'DJT']
    import sp500_scan, nasdaq100_scan
    mkt = sorted(set(sp500_scan.get_sp500_tickers()) | set(nasdaq100_scan.get_ndx100_tickers()))
    return kim, mkt


def monthly(t):
    df = yf.Ticker(t).history(period='max', interval='1mo', auto_adjust=True)
    if df.empty:
        return None
    df.index = df.index.tz_localize(None)
    df = df[df.index < pd.Timestamp.today().replace(day=1)]   # 진행 중인 달 제외
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def trades(t):
    df = monthly(t)
    if df is None or len(df) < 14:
        return []
    d = bp.prepare(df)
    c = d['Close']
    out = []
    for k in range(11, len(d)):
        if d.index[k].year < START:
            continue
        sig = bp.buy_signal(d, k)
        if not sig:
            continue
        x = next((j for j in range(k + 1, len(d)) if c.iat[j] < d['MA'].iat[j]), None)
        closed = x is not None
        px = c.iat[x] if closed else c.iat[-1]
        out.append(dict(ticker=t, month=str(d.index[k].to_period('M')), sig=sig, ret=px / c.iat[k] - 1,
                        closed=closed, months=(x - k) if closed else len(d) - 1 - k))
    return out


def stats(r):
    if not len(r):
        return 'n=    0'
    trim = r[r <= r.quantile(0.98)] if len(r) > 50 else r
    return (f'n={len(r):>5} 평균 {r.mean():+6.1%} 상위2%제외 {trim.mean():+6.1%} 중앙 {r.median():+6.1%} '
            f'승률 {(r > 0).mean():5.1%} +50%↑ {(r >= .5).mean():5.1%} -20%↓ {(r <= -.2).mean():4.1%}')


def boot(a, b, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    a, b = a.values, b.values
    return np.percentile([rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)], [2.5, 97.5])


if __name__ == '__main__':
    kim, mkt = universes()
    allt = sorted(set(kim) | set(mkt))
    print(f'김학주 {len(kim)}종목 / 시장 전체 {len(mkt)}종목 / 합계 {len(allt)}종목 (겹침 {len(set(kim) & set(mkt))})')
    rows = []
    for i, t in enumerate(allt, 1):
        print(f'  {i:>3}/{len(allt)} {t:<8}', end='\r')
        try:
            for r in trades(t):
                r['kim'], r['mkt'] = t in kim, t in mkt
                rows.append(r)
        except Exception as e:
            print(f'\n  {t} 실패: {e}')
    ev = pd.DataFrame(rows)
    ev.to_csv(OUT, index=False, encoding='utf-8-sig')

    groups = (('김학주 관심종목', ev.kim), ('시장 전체(S&P500∪나스닥100)', ev.mkt), ('시장 전체 − 김학주 종목', ev.mkt & ~ev.kim))
    print('\n' + '=' * 120)
    print(f'  A. {START}~2026 청산 거래 (⚠️ 김학주 쪽은 사후 선택 편향 — 참고용)')
    print('=' * 120)
    for name, m in groups:
        print(f'  {name:<24}', stats(ev[m & ev.closed].ret))
    a, b = ev[ev.kim & ev.closed].ret, ev[ev.mkt & ~ev.kim & ev.closed].ret
    lo, hi = boot(a, b)
    print(f'  김학주 − (시장−김학주) 평균 차이 95% 구간: {lo:+.1%} ~ {hi:+.1%}')
    ev['yr'] = ev.month.str[:4].astype(int)
    for y0, y1 in ((2000, 2009), (2010, 2019), (2020, 2026)):
        s = ev[ev.closed & ev.yr.between(y0, y1)]
        print(f'  {y0}~{y1}: 김학주 {stats(s[s.kim].ret)}')
        print(f'  {"":>9}  시장−김학주 {stats(s[s.mkt & ~s.kim].ret)}')

    o = ev[ev.month >= OOS]
    print('\n' + '=' * 120)
    print(f'  B. {OOS} 이후 신호 (김학주 자료가 나온 뒤 — 공정 구간, 보유 중은 현재가 평가)')
    print('=' * 120)
    for name, m in groups:
        g = o[m]
        print(f'  {name:<24}', stats(g.ret), f'| 청산 {int(g.closed.sum())}건 / 보유중 {int((~g.closed).sum())}건')
