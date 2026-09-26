# -*- coding: utf-8 -*-
"""
박스권(혼조 추세) 판정 후보 비교 (2026-09-26, 사용자 요청: "박스권 과거데이터로 비교해줘")

원서: 혼조 추세 = 작은 상승·하락을 반복하며 박스권에 갇힌 형태, 휩소 — "진입과 청산을 반복하면서 계좌 수익이
토막 난다"(p.308). "박스권이 상방 또는 하방으로 돌파되는 것을 확인한 뒤 매매"(p.309), 박스권 돌파매매(p.316),
지수가 박스권에 갇힌 불투명한 장에선 매매하지 않는다(p.401, 예: 코스피 2012~2017 월봉). 수치 기준은 없다.

대상 거래: 원서 매수 신호(book_patterns.buy_signal — 돌파·10이평 지지) 월말 종가 매수 → 월말 종가 10이평 이탈 매도.
각 신호 시점에 "그때까지 알 수 있던 정보만으로" 박스권 여부를 판정하고, 박스권으로 걸러진 거래와 남은 거래를 비교한다.
  A. 휩소 횟수   : 직전 N개월 동안 월봉 종가가 10이평을 오르내린 횟수 ≥ k
  B. 박스 폭     : 직전 12개월 (최고가/최저가 − 1) ≤ w
  C. 박스 + 돌파 : A 또는 B로 박스인데, 신호 달 종가가 직전 12개월 최고가를 넘었으면 매수 허용(박스권 돌파매매 p.316)
  D. 지수 박스권 : 나스닥 종합지수(^IXIC) 월봉의 직전 24개월 휩소 횟수 ≥ k 이면 신규 매수 전부 보류(p.401)
유니버스: 기존 백테스트와 같은 88종목. 생존편향 있음 — 걸러진 쪽과 남은 쪽의 차이를 볼 것.
실행: python backtest_box_range.py   (결과: backtest_box_range_trades.csv)
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf
import book_patterns as bp

OUT = 'backtest_box_range_trades.csv'


def monthly(tk):
    df = yf.Ticker(tk).history(period='max', interval='1mo', auto_adjust=True)
    if df.empty:
        return None
    df.index = df.index.tz_localize(None)
    return df[df.index < pd.Timestamp.today().replace(day=1)]   # 진행 중인 달 제외


def crosses(above: pd.Series, t: int, n: int) -> int:
    """t 직전 n개월(t 미포함) 동안 10이평 위/아래가 바뀐 횟수."""
    a = above.iloc[max(1, t - n):t].values
    b = above.iloc[max(0, t - n - 1):t - 1].values
    m = min(len(a), len(b))
    return int((a[-m:] != b[-m:]).sum()) if m else 0


def index_whipsaw():
    d = bp.prepare(monthly('^IXIC'))
    return {d.index[t].to_period('M'): crosses(d['above'], t, 24) for t in range(25, len(d))}


def run_ticker(tk):
    df = monthly(tk)
    if df is None or len(df) < 40:
        return []
    d = bp.prepare(df)
    c = d['Close']
    rows = []
    for t in range(24, len(d)):
        sig = bp.buy_signal(d, t)
        if not sig:
            continue
        x = next((k for k in range(t + 1, len(d)) if c.iat[k] < d['MA'].iat[k]), None)
        if x is None:
            continue
        hi12 = d['High'].iloc[t - 12:t].max()
        lo12 = d['Low'].iloc[t - 12:t].min()
        rows.append(dict(
            ticker=tk, month=str(d.index[t].to_period('M')), sig=sig,
            ret=c.iat[x] / c.iat[t] - 1, months=x - t,
            cross12=crosses(d['above'], t, 12), cross24=crosses(d['above'], t, 24),
            range12=hi12 / lo12 - 1, break12=bool(c.iat[t] > hi12),
        ))
    return rows


def stats(r):
    if not len(r):
        return 'n=    0'
    trim = r[r <= r.quantile(0.98)]
    return (f'n={len(r):>5} 평균 {r.mean():+6.1%} 상위2%제외 {trim.mean():+6.1%} 중앙 {r.median():+6.1%} '
            f'승률 {(r > 0).mean():5.1%} -20%↓ {(r <= -.2).mean():4.1%}')


def boot(a, b, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    a, b = a.values, b.values
    ds = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)]
    return np.percentile(ds, [2.5, 97.5])


ERAS = ((1974, 1999), (2000, 2009), (2010, 2019), (2020, 2026))


def compare(ev, mask, name):
    box, rest = ev[mask].ret, ev[~mask].ret
    lo, hi = boot(rest, box) if len(box) > 30 else (np.nan, np.nan)
    era = []
    for a, b in ERAS:
        seg = ev[(ev.yr >= a) & (ev.yr <= b)]
        m = mask[seg.index]
        bb, rr = seg[m].ret, seg[~m].ret
        era.append('+' if len(bb) and rr.mean() > bb.mean() else '-')
    print(f'\n[{name}]  걸러짐 {len(box)}건({len(box) / len(ev):.0%})')
    print('   박스권(보류) ', stats(box))
    print('   나머지(매수) ', stats(rest))
    print(f'   나머지−박스권 평균 차이 95% 구간 {lo:+.1%} ~ {hi:+.1%} | 구간별(74-99/00-09/10-19/20-26) 나머지가 더 좋음: {"".join(era)}'
          f' | 100만원씩 총손익: 전부 {ev.ret.sum() * 100:+,.0f}만 → 보류 후 {rest.sum() * 100:+,.0f}만')


def summarize(ev):
    ev = ev.copy()
    ev['yr'] = ev.month.str[:4].astype(int)
    print('\n' + '=' * 110)
    print('  박스권(혼조 추세) 판정 후보 비교 — 원서 매수 신호(돌파·지지) 거래 대상')
    print('=' * 110)
    print('  전체       ', stats(ev.ret))
    for n, ks in ((12, (2, 3, 4)), (24, (3, 4, 6))):
        for k in ks:
            compare(ev, ev[f'cross{n}'] >= k, f'A. 휩소: 직전 {n}개월 10이평 교차 ≥ {k}회')
    for w in (0.2, 0.3, 0.4):
        compare(ev, ev.range12 <= w, f'B. 박스 폭: 직전 12개월 고저 범위 ≤ {w:.0%}')
    for n, k in ((12, 3), (24, 4)):
        box = ev[f'cross{n}'] >= k
        compare(ev, box & ~ev.break12, f'C. 휩소({n}개월 ≥ {k}회)인데 직전 12개월 고점 미돌파')
    box = ev.range12 <= 0.3
    compare(ev, box & ~ev.break12, 'C. 박스 폭(≤30%)인데 직전 12개월 고점 미돌파')
    if 'idx_cross24' in ev:
        for k in (3, 4, 6):
            compare(ev, ev.idx_cross24 >= k, f'D. 나스닥 지수 직전 24개월 휩소 ≥ {k}회 → 전부 보류')


if __name__ == '__main__':
    tickers = sorted(pd.read_csv('archive/backtest_pattern_events_폐기_2026-09-12판.csv').ticker.unique())
    rows = []
    for i, tk in enumerate(tickers, 1):
        print(f'  {i:>3}/{len(tickers)} {tk:<8}', end='\r')
        try:
            rows += run_ticker(tk)
        except Exception as e:
            print(f'\n  {tk} 실패: {e}')
    ev = pd.DataFrame(rows)
    iw = index_whipsaw()
    ev['idx_cross24'] = ev.month.map(lambda m: iw.get(pd.Period(m, 'M'), np.nan))
    ev = ev.dropna(subset=['idx_cross24']).reset_index(drop=True)
    ev.to_csv(OUT, index=False, encoding='utf-8-sig')
    summarize(ev)
