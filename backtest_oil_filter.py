# -*- coding: utf-8 -*-
"""
유가 연동 필터 검증 (2026-11-14 사용자 요청: "월봉매매법에 선정되는 종목을 유가와 연동해서 한번 더 걸러내는 방법" —
"지금 규칙까지 바꿀 필요는 없고 테스트만")

질문: 유가가 오르는 국면에서, 유가에 약한 종목(유가가 오를 때 주가가 내리던 종목)의 매수 신호를 빼면 성과가 나아지는가?

  유가 국면 = 신호 달 WTI(CL=F) 월말 종가가 자기 10개월 이평 위면 '유가 상승기', 아래면 '유가 하락기'
              (보조: 직전 3개월 유가 상승률 상위 1/3 = '유가 급등기')
  유가 민감도 = 신호 달까지의 직전 36개월 종목 월수익률을 유가 월수익률에 회귀한 기울기(베타, 미래 정보 없음)
              → 전체 신호의 하위 1/3 = '유가에 약함', 상위 1/3 = '유가에 강함'(임의 기준값 대신 분위)
  성과 = 월말 종가 매수 → 10이평 이탈 월말 종가 매도(backtest_author_ideas와 같음)
유니버스: S&P500 ∪ 나스닥100 ∪ 김학주 관심종목(현재 구성 — 생존편향, 그룹 간 차이를 볼 것). 2000년 이후 신호.
실행: python backtest_oil_filter.py
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf
import book_patterns as bp
from backtest_universe_compare import universes
from backtest_author_ideas import monthly_batch, stats, boot

START = 2000
BETA_WIN = 36


def oil_monthly():
    df = yf.Ticker('CL=F').history(period='max', interval='1mo', auto_adjust=True)
    df.index = df.index.tz_localize(None).to_period('M')
    c = df['Close'].groupby(level=0).last()
    c = c[c.index < pd.Timestamp.today().to_period('M')]
    return pd.DataFrame({'close': c, 'ma': c.rolling(10).mean(), 'ret': c.pct_change(), 'chg3': c.pct_change(3)})


if __name__ == '__main__':
    oil = oil_monthly()
    kim, mkt = universes()
    data = monthly_batch(set(kim) | set(mkt))
    print(f'\n월봉 확보 {len(data)}종목, 유가 {oil.index[0]}~{oil.index[-1]}')

    rows = []
    for t, d in data.items():
        c = d['Close']
        per = d.index.to_period('M')
        sr = pd.Series(c.values, index=per).pct_change()
        both = pd.concat({'s': sr, 'o': oil['ret']}, axis=1)
        for k in range(11, len(d)):
            if d.index[k].year < START:
                continue
            s = bp.buy_signal(d, k)
            if not s:
                continue
            x = next((j for j in range(k + 1, len(d)) if c.iat[j] < d['MA'].iat[j]), None)
            if x is None:
                continue
            m = per[k]
            if m not in oil.index or pd.isna(oil.at[m, 'ma']):
                continue
            w = both.loc[:m].tail(BETA_WIN).dropna()
            if len(w) < 24:
                continue
            beta = np.cov(w.s, w.o)[0, 1] / np.var(w.o, ddof=1)
            rows.append(dict(ticker=t, month=str(m), yr=m.year, sig=s, ret=c.iat[x] / c.iat[k] - 1, beta=beta,
                             oil_up=bool(oil.at[m, 'close'] > oil.at[m, 'ma']), chg3=oil.at[m, 'chg3']))
    ev = pd.DataFrame(rows)
    lo_b, hi_b = ev.beta.quantile([1 / 3, 2 / 3])
    ev['sens'] = np.where(ev.beta <= lo_b, '약함', np.where(ev.beta >= hi_b, '강함', '중간'))
    spike = ev.chg3.quantile(2 / 3)
    ev['oil_spike'] = ev.chg3 >= spike
    ev.to_csv('backtest_oil_filter_trades.csv', index=False, encoding='utf-8-sig')

    print('\n' + '=' * 118)
    print(f'  유가 연동 필터 검증 — 신호 {len(ev):,}건 (2000~, 월말 매수 → 10이평 이탈 매도)')
    print(f'  유가 민감도 베타 3분위 경계: 약함 ≤ {lo_b:+.2f} < 중간 < {hi_b:+.2f} ≤ 강함 | 유가 급등기 = 3개월 상승률 ≥ {spike:+.0%}')
    print('=' * 118)
    print('  전체                     ', stats(ev.ret))
    for cond, name in ((ev.oil_up, '유가 상승기(WTI 10이평 위)'), (~ev.oil_up, '유가 하락기'), (ev.oil_spike, '유가 급등기(3개월 상위1/3)')):
        print(f'\n  [{name}] 신호 비율 {cond.mean():.0%}')
        for sname in ('약함', '중간', '강함'):
            print(f'    유가에 {sname}            ', stats(ev[cond & (ev.sens == sname)].ret))
        a = ev[cond & (ev.sens == '약함')].ret
        b = ev[cond & (ev.sens != '약함')].ret
        lo, hi = boot(a, b)
        print(f'    약함 − 나머지 평균 차이 95% 구간: {lo:+.1%} ~ {hi:+.1%}')

    print('\n  [기간별 — 유가 상승기 안에서 약함 vs 나머지 평균]')
    for y0, y1 in ((2000, 2009), (2010, 2019), (2020, 2026)):
        s = ev[ev.yr.between(y0, y1) & ev.oil_up]
        a, b = s[s.sens == '약함'].ret, s[s.sens != '약함'].ret
        print(f'    {y0}~{y1}: 약함 n={len(a):>5} 평균 {a.mean():+6.1%} 중앙 {a.median():+6.1%} | '
              f'나머지 n={len(b):>5} 평균 {b.mean():+6.1%} 중앙 {b.median():+6.1%}')

    skip = ev.oil_up & (ev.sens == '약함')
    skip2 = ev.oil_spike & (ev.sens == '약함')
    print(f'\n  100만원씩 총손익: 전부 {ev.ret.sum() * 100:+,.0f}만 '
          f'→ 유가 상승기 약함 제외 {ev[~skip].ret.sum() * 100:+,.0f}만 (신호 {skip.mean():.0%} 제외) '
          f'→ 유가 급등기 약함 제외 {ev[~skip2].ret.sum() * 100:+,.0f}만 (신호 {skip2.mean():.0%} 제외)')
    print(f'  건당 평균: 전부 {ev.ret.mean():+.1%} / 상승기 약함 제외 {ev[~skip].ret.mean():+.1%} / 급등기 약함 제외 {ev[~skip2].ret.mean():+.1%}')
