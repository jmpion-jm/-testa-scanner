# -*- coding: utf-8 -*-
"""
성승현 작가 인터뷰 속 두 가지 방법 검증 (2026-09-27 사용자 요청: "1번 유튜브 내용 너의 제안 검증하고, 2번 유튜브도 돌려봐")

검증 1 — 지수 하락 패턴이면 신규 매수 쉬기 (머니인사이드 인터뷰: "코스닥 월봉이 고점 쌍봉으로 10이평을 깼으니 6월 말부터
          신규 매수를 하지 말았어야", 지식한상: "나스닥이 상승 패턴이면 사는 쪽, 하락 패턴이면 쉰다". 원서 0장 p.86 참고)
  지수(S&P500 ^GSPC, 나스닥 ^IXIC) 월봉 상태를 매달 분류:
    하락패턴 = 마지막 10이평 교차가 하향 이탈이고, 그 이탈 봉이 원서 하락 패턴(book_patterns.bearish_at·bear_composite_at) 완성
    단순이탈 = 마지막 교차가 하향 이탈인데 패턴 없음
    상승패턴 = 마지막 교차가 상향 돌파이고 그 봉이 원서 상승 패턴(bullish_at·composite_at) 완성
    단순상승 = 마지막 교차가 상향 돌파, 패턴 없음
  종목 매수 신호(book_patterns.buy_signal, 월말 종가 매수 → 10이평 이탈 매도)를 신호 달의 지수 상태별로 비교.

검증 2 — 나눠 사기 (지식한상: "처음엔 살짝 던져 놓고 추세 확인되면 더 사고 눌림목에서 추가, 나갈 때는 전량")
  같은 추세(돌파 신호 t ~ 10이평 이탈 x)를 목표 금액 1 기준으로:
    한번에   = 돌파 달 종가에 100%
    나눠서   = 돌파 때 50%, 이후 첫 '10이평 지지' 신호 달 종가에 50% 추가(지지 없이 끝나면 50%만 투자)
    3분할    = 돌파 1/3, 첫 지지 1/3, 두 번째 지지 1/3
  목표 금액 대비 수익률, 실제 투자한 돈 대비 수익률, 평균 투자 비중을 비교.
유니버스: S&P500 ∪ 나스닥100 ∪ 김학주 관심종목(현재 구성 — 생존편향, 방법 간 차이를 볼 것). 2000년 이후 신호.
실행: python backtest_author_ideas.py
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf
import book_patterns as bp
from backtest_universe_compare import universes

START = 2000


def monthly_batch(tickers):
    out = {}
    tickers = sorted(set(tickers))
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        print(f'  다운로드 {i}/{len(tickers)}', end='\r')
        raw = yf.download(chunk, period='max', interval='1mo', auto_adjust=True, group_by='ticker', progress=False, threads=True)
        for t in chunk:
            try:
                sub = raw[t][['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
                sub = sub[sub.index < pd.Timestamp.today().replace(day=1)]
                if len(sub) >= 24:
                    out[t] = bp.prepare(sub.ffill())
            except Exception:
                pass
    return out


def index_state(tk):
    df = yf.Ticker(tk).history(period='max', interval='1mo', auto_adjust=True)
    df.index = df.index.tz_localize(None)
    d = bp.prepare(df[df.index < pd.Timestamp.today().replace(day=1)][['Open', 'High', 'Low', 'Close', 'Volume']])
    st, cur = {}, None
    for k in range(1, len(d)):
        if pd.isna(d['MA'].iat[k - 1]):
            continue
        a0, a1 = d['above'].iat[k - 1], d['above'].iat[k]
        if a0 and not a1:
            pat = bp.is_breakdown(d, k) and (bp.bearish_at(d, k) or bp.bear_composite_at(d, k))
            cur = '하락패턴' if pat else '단순이탈'
        elif a1 and not a0:
            pat = bp.is_hook(d, k) and (bp.bullish_at(d, k) or bp.composite_at(d, k))
            cur = '상승패턴' if pat else '단순상승'
        if cur:
            st[d.index[k].to_period('M')] = cur
    return st


def stats(r):
    if not len(r):
        return 'n=    0'
    trim = r[r <= r.quantile(0.98)] if len(r) > 50 else r
    return (f'n={len(r):>5} 평균 {r.mean():+6.1%} 상위2%제외 {trim.mean():+6.1%} 중앙 {r.median():+6.1%} '
            f'승률 {(r > 0).mean():5.1%} -20%↓ {(r <= -.2).mean():4.1%}')


def boot(a, b, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a), np.asarray(b)
    return np.percentile([rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)], [2.5, 97.5])


if __name__ == '__main__':
    kim, mkt = universes()
    data = monthly_batch(set(kim) | set(mkt))
    print(f'\n월봉 확보 {len(data)}종목')
    idx = {'S&P500': index_state('^GSPC'), '나스닥': index_state('^IXIC')}

    sig_rows, pyr_rows = [], []
    for t, d in data.items():
        c = d['Close']
        for k in range(11, len(d)):
            if d.index[k].year < START:
                continue
            s = bp.buy_signal(d, k)
            if not s:
                continue
            x = next((j for j in range(k + 1, len(d)) if c.iat[j] < d['MA'].iat[j]), None)
            if x is None:
                continue
            m = d.index[k].to_period('M')
            sig_rows.append(dict(ticker=t, month=str(m), sig=s, ret=c.iat[x] / c.iat[k] - 1,
                                 sp=idx['S&P500'].get(m), nq=idx['나스닥'].get(m)))
            if s == '돌파':
                sup = [j for j in range(k + 1, x) if bp.buy_signal(d, j) == '지지']
                full = c.iat[x] / c.iat[k] - 1
                r1 = c.iat[x] / c.iat[k] - 1
                if sup:
                    j1 = sup[0]
                    half = 0.5 * r1 + 0.5 * (c.iat[x] / c.iat[j1] - 1)
                    inv2 = 1.0
                else:
                    half, inv2 = 0.5 * r1, 0.5
                third = r1 / 3 + sum((c.iat[x] / c.iat[j] - 1) / 3 for j in sup[:2])
                inv3 = (1 + min(len(sup), 2)) / 3
                pyr_rows.append(dict(ticker=t, month=str(m), full=full, half=half, inv2=inv2, third=third, inv3=inv3,
                                     n_sup=len(sup), months=x - k))
    ev, py = pd.DataFrame(sig_rows), pd.DataFrame(pyr_rows)
    ev.to_csv('backtest_author_index_trades.csv', index=False, encoding='utf-8-sig')
    py.to_csv('backtest_author_pyramid_trades.csv', index=False, encoding='utf-8-sig')

    print('\n' + '=' * 118)
    print('  검증 1 — 신호 달의 지수 월봉 상태별 종목 매수 신호 성과 (2000~2026, 월말 매수 → 10이평 이탈 매도)')
    print('=' * 118)
    print('  전체              ', stats(ev.ret))
    for col, name in (('sp', 'S&P500'), ('nq', '나스닥')):
        print(f'\n  [{name} 지수 기준]')
        for s in ('상승패턴', '단순상승', '단순이탈', '하락패턴'):
            print(f'  {s:<8}          ', stats(ev[ev[col] == s].ret))
        bad = ev[ev[col] == '하락패턴'].ret
        rest = ev[ev[col] != '하락패턴'].ret
        lo, hi = boot(bad, rest)
        print(f'  하락패턴 − 나머지 평균 차이 95% 구간: {lo:+.1%} ~ {hi:+.1%} | 하락패턴 구간 신호 비율 {len(bad) / len(ev):.1%}')
        ev['yr'] = ev.month.str[:4].astype(int)
        for y0, y1 in ((2000, 2009), (2010, 2019), (2020, 2026)):
            s = ev[ev.yr.between(y0, y1)]
            print(f'    {y0}~{y1}: 하락패턴 {stats(s[s[col] == "하락패턴"].ret)}')
            print(f'    {"":>9}  나머지   {stats(s[s[col] != "하락패턴"].ret)}')
    both = ev[(ev.sp == '하락패턴') | (ev.nq == '하락패턴')].ret
    print(f'\n  [둘 중 하나라도 하락패턴] {stats(both)}  vs 둘 다 아님 {stats(ev[(ev.sp != "하락패턴") & (ev.nq != "하락패턴")].ret)}')
    print(f'  하락패턴 구간 신호를 모두 건너뛰면 100만원씩 총손익: 전부 {ev.ret.sum() * 100:+,.0f}만 → '
          f'{ev[(ev.sp != "하락패턴") & (ev.nq != "하락패턴")].ret.sum() * 100:+,.0f}만')

    print('\n' + '=' * 118)
    print('  검증 2 — 나눠 사기 vs 한 번에 (돌파 신호로 시작한 추세, 목표 금액 1 기준)')
    print('=' * 118)
    print(f'  추세 {len(py)}개, 추세 중 지지 신호가 1번 이상 나온 비율 {(py.n_sup > 0).mean():.0%}')
    print(f'  한번에 (100%)        목표금액 대비 {stats(py.full)}')
    print(f'  나눠서 (50%+지지50%) 목표금액 대비 {stats(py.half)} | 평균 투자비중 {py.inv2.mean():.0%} | 투자한 돈 대비 평균 {(py.half / py.inv2).mean():+.1%}')
    print(f'  3분할 (1/3씩)        목표금액 대비 {stats(py.third)} | 평균 투자비중 {py.inv3.mean():.0%} | 투자한 돈 대비 평균 {(py.third / py.inv3).mean():+.1%}')
    lo, hi = boot(py.half, py.full)
    print(f'  나눠서 − 한번에 (목표금액 대비) 평균 차이 95% 구간: {lo:+.1%} ~ {hi:+.1%}')
    for lab, g in (('실패 추세(최종 손실)', py[py.full <= 0]), ('성공 추세(최종 수익)', py[py.full > 0])):
        print(f'  {lab:<16} n={len(g):>5} | 한번에 평균 {g.full.mean():+6.1%} | 나눠서 평균 {g.half.mean():+6.1%} | 3분할 {g.third.mean():+6.1%}')
    print(f'  100만원씩 총손익: 한번에 {py.full.sum() * 100:+,.0f}만 / 나눠서 {py.half.sum() * 100:+,.0f}만 / 3분할 {py.third.sum() * 100:+,.0f}만')
