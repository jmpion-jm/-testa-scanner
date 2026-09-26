# -*- coding: utf-8 -*-
"""
진입 시점 3방식 비교 (2026-09-26, 사용자 요청: "월말 매매법 vs 주봉 조기진입, 비교해줘")

매도 규칙은 셋 다 같다: 월말 종가가 월봉 10이평 아래로 마감한 달 종가에 매도 (매매법 그대로).

  ① 원서   : 월말 종가가 10이평을 아래→위로 돌파한 달(직전 달 종가 ≤ 10이평) 종가에 매수
  ② 확정후 : ①의 돌파 달이 확정된 뒤, 다음 달부터 추세가 살아있는 동안 첫 '주봉 눌림목'
             (주봉 종가가 주봉 10이평 대비 0~+5%) 주 종가에 매수 — 사용자 주봉 규칙을 월봉 확정 뒤에만 사용
  ③ 조기진입: 직전 달 종가가 10이평 아래인 달에, 월말 전에 주 종가가 "이번 달 잠정 10이평"
             (지난 9개월 종가 + 이번 주 종가 평균)을 넘으면 그 주 종가에 매수.
             월말 종가가 10이평 아래면 그 달 종가에 즉시 매도(= 규칙 위반 손절), 위면 ①과 같이 보유.

유니버스: backtest_book_patterns.py와 같은 88종목. 생존편향 있음 — 세 방식 간 차이를 볼 것.
청산된 거래만 집계(아직 보유 중인 추세는 제외, 개수만 표시).
실행: python backtest_entry_timing.py  (결과: backtest_entry_timing_trades.csv)
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf

OUT = 'backtest_entry_timing_trades.csv'
PULL_MAX = 0.05


def load(tk):
    df = yf.Ticker(tk).history(period='max', interval='1d', auto_adjust=True)
    if df.empty:
        return None
    df.index = df.index.tz_localize(None)
    return df['Close'].dropna()


def run_ticker(tk):
    px = load(tk)
    if px is None or len(px) < 400:
        return []
    this_month = pd.Timestamp.today().to_period('M')
    px = px[px.index.to_period('M') < this_month]          # 진행 중인 달 제외
    mo = px.groupby(px.index.to_period('M'))
    mclose = mo.last()
    ma = mclose.rolling(10).mean()
    months = list(mclose.index)
    # 월 내부의 주 종가(월 경계에서 자름) — ③용
    key = px.index.to_period('M').astype(str) + '|' + px.index.to_period('W-FRI').astype(str)
    intra = px.groupby(key).tail(1)                         # 날짜 인덱스 유지: 각 (달, 주)의 마지막 거래일 종가
    intra_m = intra.index.to_period('M')
    # 진짜 주봉(W-FRI) + 주봉 10이평 — ②용
    wk = px.resample('W-FRI').last().dropna()
    wma = wk.rolling(10).mean()

    def exit_from(i):
        """i번째 달부터 보면서 처음 '종가 < 10이평'인 달 인덱스. 없으면 None(보유 중)."""
        for k in range(i, len(months)):
            if mclose.iat[k] < ma.iat[k]:
                return k
        return None

    rows = []
    for t in range(10, len(months)):
        if pd.isna(ma.iat[t - 1]):
            continue
        prev_below = mclose.iat[t - 1] <= ma.iat[t - 1]
        if not prev_below:
            continue
        m = months[t]
        cross = mclose.iat[t] > ma.iat[t]

        # ③ 조기진입: 이번 달 월말 전 주 종가가 잠정 10이평 돌파
        base9 = mclose.iloc[t - 9:t].sum()
        weeks = intra[intra_m == m]
        for wkey, wc in list(weeks.items())[:-1]:            # 마지막 조각 = 월말 종가(①의 자리)
            if wc > (base9 + wc) / 10:
                if cross:
                    x = exit_from(t + 1)
                    if x is not None:
                        rows.append(dict(ticker=tk, strat='③조기진입', signal=str(m), entry=str(wkey.date()),
                                         ret=mclose.iat[x] / wc - 1, months=x - t, outcome='돌파 성공'))
                    else:
                        rows.append(dict(ticker=tk, strat='③조기진입', signal=str(m), outcome='보유중'))
                else:
                    rows.append(dict(ticker=tk, strat='③조기진입', signal=str(m), entry=str(wkey.date()),
                                     ret=mclose.iat[t] / wc - 1, months=0, outcome='월말 실패 손절'))
                break
        else:
            if cross:   # 월중 신호가 없었으면 ③도 ①처럼 월말 종가에 매수
                x = exit_from(t + 1)
                rows.append(dict(ticker=tk, strat='③조기진입', signal=str(m), entry=str(m.end_time.date()),
                                 ret=(mclose.iat[x] / mclose.iat[t] - 1) if x is not None else np.nan,
                                 months=(x - t) if x is not None else np.nan,
                                 outcome='월말 진입' if x is not None else '보유중'))

        if not cross:
            continue
        x = exit_from(t + 1)
        # ① 원서
        if x is None:
            rows.append(dict(ticker=tk, strat='①원서', signal=str(m), outcome='보유중'))
            rows.append(dict(ticker=tk, strat='②확정후', signal=str(m), outcome='보유중'))
            continue
        rows.append(dict(ticker=tk, strat='①원서', signal=str(m), entry=str(m.end_time.date()),
                         ret=mclose.iat[x] / mclose.iat[t] - 1, months=x - t, outcome='진입'))
        # ② 확정 후 주봉 눌림목: t+1 ~ x 달 안의 첫 눌림 주
        lo, hi = months[t + 1].start_time, months[x].end_time
        cand = wk[(wk.index >= lo) & (wk.index <= hi)]
        entry = None
        for d, c in cand.items():
            w = wma.get(d)
            if w and not pd.isna(w) and 0 <= c / w - 1 <= PULL_MAX:
                entry = (d, c)
                break
        if entry is None:
            rows.append(dict(ticker=tk, strat='②확정후', signal=str(m), outcome='눌림 없음(놓침)',
                             missed_ret=mclose.iat[x] / mclose.iat[t] - 1))
        else:
            rows.append(dict(ticker=tk, strat='②확정후', signal=str(m), entry=str(entry[0].date()),
                             ret=mclose.iat[x] / entry[1] - 1,
                             months=sum(1 for k in range(t + 1, x + 1) if months[k] >= entry[0].to_period('M')),
                             outcome='진입'))
    return rows


def stats(r):
    r = r.dropna()
    if not len(r):
        return 'n=0'
    trim = r[r <= r.quantile(0.98)]
    return (f'n={len(r):>5} | 평균 {r.mean():+7.1%}  상위2%제외 {trim.mean():+6.1%}  중앙 {r.median():+6.1%}  '
            f'승률 {(r > 0).mean():5.1%}  +50%↑ {(r >= .5).mean():5.1%}  -20%↓ {(r <= -.2).mean():5.1%}  '
            f'100만원씩 총손익 {r.sum() * 100:+,.0f}만')


def boot_diff(a, b, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    a, b = a.values, b.values
    ds = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)]
    return np.percentile(ds, [2.5, 97.5])


def summarize(ev):
    print('\n' + '=' * 110)
    print('  진입 시점 비교 — 매도는 셋 다 "월말 종가 < 월봉 10이평"')
    print('=' * 110)
    for s in ('①원서', '②확정후', '③조기진입'):
        e = ev[ev.strat == s]
        print(f'\n[{s}]  보유중 {int((e.outcome == "보유중").sum())}건 제외')
        print('  전체       ', stats(e.ret))
        if s == '②확정후':
            miss = e[e.outcome == '눌림 없음(놓침)']
            print(f'  놓친 추세   {len(miss)}건 ({len(miss) / max(1, len(e[e.outcome != "보유중"])):.0%}) — 그 추세의 ①수익 평균 {miss.missed_ret.mean():+.1%}, '
                  f'+50%↑ {(miss.missed_ret >= .5).mean():.0%}')
        if s == '③조기진입':
            for o in ('돌파 성공', '월말 실패 손절'):
                print(f'  {o:<10} ', stats(e[e.outcome == o].ret))

    # 같은 추세 짝 비교: ①과 ③이 같은 돌파 달을 잡은 경우 진입가 차이
    one = ev[(ev.strat == '①원서') & ev.ret.notna()].set_index(['ticker', 'signal']).ret
    three = ev[(ev.strat == '③조기진입') & (ev.outcome == '돌파 성공')].set_index(['ticker', 'signal']).ret
    both = one.to_frame('one').join(three.rename('three'), how='inner')
    print(f'\n[같은 돌파를 ①·③이 둘 다 잡은 {len(both)}건] ③이 ①보다 수익 높음 {(both.three > both.one).mean():.0%}, '
          f'평균 차이 {(both.three - both.one).mean():+.1%}p (조기 진입으로 얻는 몫)')
    two = ev[(ev.strat == '②확정후') & ev.ret.notna()].set_index(['ticker', 'signal']).ret
    b2 = one.to_frame('one').join(two.rename('two'), how='inner')
    print(f'[같은 추세를 ①·②가 둘 다 잡은 {len(b2)}건] ②가 ①보다 수익 높음 {(b2.two > b2.one).mean():.0%}, '
          f'평균 차이 {(b2.two - b2.one).mean():+.1%}p')

    r1 = ev[(ev.strat == '①원서')].ret.dropna()
    for s in ('②확정후', '③조기진입'):
        rs = ev[(ev.strat == s)].ret.dropna()
        lo, hi = boot_diff(rs, r1)
        print(f'  {s} − ①원서 거래당 평균 차이 95% 구간: {lo:+.1%} ~ {hi:+.1%}')

    print('\n[구간별 평균 / 100만원씩 총손익]')
    ev = ev.copy()
    ev['yr'] = ev.signal.str[:4].astype(int)
    for a, b in ((1974, 1999), (2000, 2009), (2010, 2019), (2020, 2026)):
        seg = ev[(ev.yr >= a) & (ev.yr <= b)]
        parts = []
        for s in ('①원서', '②확정후', '③조기진입'):
            r = seg[seg.strat == s].ret.dropna()
            parts.append(f'{s} n={len(r):>4} {r.mean():+6.1%} {r.sum() * 100:+8,.0f}만')
        print(f'  {a}~{b}: ' + ' | '.join(parts))


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
    ev.to_csv(OUT, index=False, encoding='utf-8-sig')
    summarize(ev)
