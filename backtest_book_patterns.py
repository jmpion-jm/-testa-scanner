# -*- coding: utf-8 -*-
"""
원서 기준 패턴(book_patterns.py) 백테스트 — 반드시 기준선과 비교한다.

2026-09-26: 이전 백테스트(backtest_patterns.py)는 "패턴 뒤 올랐나"만 세고 기준선과 비교하지 않아
"검증됨(59~83%)"이라는 잘못된 결론을 냈다(같은 종목을 아무 달에나 사도 70% 올랐음). 이 스크립트는
같은 종목·같은 기간에서 다음을 나란히 비교한다.

  A. 아무 달에나 산 경우
  B. 10이평 상향 돌파한 달 전부(몸통·양봉 무관)
  C. 후킹 캔들(양봉 몸통이 10이평 관통)인 달 전부 — 원서 상승 패턴의 "완성" 조건 자체
  D. 후킹 + 원서 패턴(쌍바닥/역H&S/삼중바닥, 되돌림·겹/대쌍바닥)

측정: ① 3/6/12개월 뒤 수익(보유 고정) ② 실제 매매법 거래 — 그 달 종가 매수, 이후 처음으로 월봉 종가가
10이평 아래로 마감한 달 종가에 매도(사용자 시스템의 매도 규칙). 거래 수익·보유기간·큰손실(-20%↓) 비율.

유니버스: archive/backtest_pattern_events_폐기_2026-09-12판.csv의 88종목(나스닥100 현재 구성 + 김학주 관심종목). 지금 살아남은
종목이라 생존편향이 있다 — 절대 수치보다 A 대비 차이(같은 편향을 공유)를 볼 것.
실행: python backtest_book_patterns.py   (결과: backtest_book_patterns_events.csv)
"""
import sys, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import yfinance as yf
import book_patterns as bp

OUT = 'backtest_book_patterns_events.csv'


def trade_exit(d, t):
    """t 종가 매수 → 이후 첫 '종가 < 10이평' 달 종가 매도. 아직 보유 중이면 None."""
    for k in range(t + 1, len(d)):
        if d['Close'].iat[k] < d['MA'].iat[k]:
            return k
    return None


def run_ticker(tk):
    df = yf.Ticker(tk).history(period='max', interval='1mo', auto_adjust=True)
    if df.empty or len(df) < 40:
        return []
    df.index = df.index.tz_localize(None)
    df = df[df.index < pd.Timestamp.today().replace(day=1)]  # 진행 중인 이번 달(미완성 봉) 제외
    d = bp.prepare(df)
    c = d['Close']
    rows = []
    for t in range(bp.MA_PERIOD + 1, len(d)):
        if pd.isna(d['MA'].iat[t]):
            continue
        r = {'ticker': tk, 'date': d.index[t].strftime('%Y-%m')}
        for h in (3, 6, 12):
            r[f'fwd{h}'] = c.iat[t + h] / c.iat[t] - 1 if t + h < len(d) else np.nan
        cross = c.iat[t] > d['MA'].iat[t] and c.iat[t - 1] <= d['MA'].iat[t - 1]
        # bullish_pattern_scan.is_uptrend()와 같은 조건(당시 시점 기준): MA10이 3개월 전보다 높고 가격이 6개월 전보다 높음
        r['uptrend'] = bool(t >= 7 and d['MA'].iat[t] > d['MA'].iat[t - 3] and c.iat[t] > c.iat[t - 6])
        hook = bp.is_hook(d, t)
        r['cross'] = cross
        r['hook'] = hook
        r['pattern'] = ''
        r['composite'] = ''
        if hook:
            b = bp.bullish_at(d, t)
            comps = bp.composite_at(d, t)
            r['pattern'] = b['pattern'] if b else ''
            r['composite'] = '+'.join(x['pattern'] for x in comps)
            if b:
                for k, v in b['quality'].items():
                    r['q_' + k] = v
        if cross:
            x = trade_exit(d, t)
            if x is not None:
                r['trade_ret'] = c.iat[x] / c.iat[t] - 1
                r['trade_months'] = x - t
        rows.append(r)
    return rows


def summarize(ev):
    def line(name, s):
        f12 = s['fwd12'].dropna()
        tr = s['trade_ret'].dropna() if 'trade_ret' in s else pd.Series(dtype=float)
        out = f'  {name:<34} n={len(s):>5}'
        if len(f12):
            out += f' | 12개월 뒤 상승 {(f12 > 0).mean()*100:5.1f}% 평균 {f12.mean()*100:+6.1f}% -20%↓ {(f12 < -0.2).mean()*100:5.1f}%'
        if len(tr):
            out += (f' | 매매법 거래 n={len(tr):>4} 승률 {(tr > 0).mean()*100:5.1f}% 평균 {tr.mean()*100:+6.1f}%'
                    f' 중앙 {tr.median()*100:+6.1f}% -20%↓ {(tr < -0.2).mean()*100:4.1f}%'
                    f' 보유 {s["trade_months"].dropna().mean():4.1f}개월')
        print(out)

    print('\n' + '=' * 150)
    print('  원서 기준 패턴 백테스트 (월봉) — 기준선 대비')
    print('=' * 150)
    line('A. 아무 달', ev.drop(columns=['trade_ret', 'trade_months']))
    line('B. 10이평 상향돌파 달 전부', ev[ev.cross])
    line('C. 후킹 캔들(원서 완성조건) 전부', ev[ev.hook])
    line('C-1. 후킹인데 패턴 없음', ev[ev.hook & (ev.pattern == '') & (ev.composite == '')])
    for p in ['쌍바닥', '역H&S', '삼중바닥']:
        line(f'D. {p}', ev[ev.pattern == p])
    line('D. 상승패턴 전체(3종)', ev[ev.pattern != ''])
    for p in ['되돌림1', '되돌림2', '되돌림3', '겹쌍바닥', '대쌍바닥']:
        line(f'E. {p}', ev[ev.composite.str.contains(p)])
    pat = ev[ev.pattern != '']
    line('D. 상승패턴 + 우상향필터 통과', pat[pat.uptrend])
    line('D. 상승패턴 + 우상향필터 탈락', pat[~pat.uptrend])
    qcols = [c for c in ev.columns if c.startswith('q_')]
    if qcols:
        print('\n  [참고조건별 — 상승패턴 3종 중 조건 충족 vs 미충족]')
        pat = ev[ev.pattern != '']
        for qc in qcols:
            sub = pat[pat[qc].notna()]
            if len(sub) < 20:
                continue
            line(f'{qc[2:]} O', sub[sub[qc] == True])
            line(f'{qc[2:]} X', sub[sub[qc] == False])


if __name__ == '__main__':
    # 88종목 유니버스 목록만 재사용 — 이 CSV의 이벤트 값은 폐기판(2026-09-12)이라 쓰지 않음
    tickers = sorted(pd.read_csv('archive/backtest_pattern_events_폐기_2026-09-12판.csv').ticker.unique())
    allrows = []
    for n, tk in enumerate(tickers, 1):
        print(f'  {n:>3}/{len(tickers)} {tk:<8}', end='\r')
        try:
            allrows += run_ticker(tk)
        except Exception as e:
            print(f'\n  [건너뜀] {tk}: {type(e).__name__}: {e}')
    ev = pd.DataFrame(allrows)
    ev.to_csv(OUT, index=False, encoding='utf-8-sig')
    print(f'\n저장: {OUT} ({len(ev)}개월, {ev.ticker.nunique()}종목, {ev.date.min()}~{ev.date.max()})')
    summarize(ev)
