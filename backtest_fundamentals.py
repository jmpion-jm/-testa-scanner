# -*- coding: utf-8 -*-
"""
매출·영업이익 성장이 월봉매매법 성과를 높이는가 (2026-09-26 사용자 요청: "매출, 이익성장률을 고려한 후 종목을
선택했을 때 수익률을 시뮬레이션해 검증해봐")

거래: backtest_universe_compare.py 결과(S&P500 ∪ 나스닥100 ∪ 김학주 관심종목, 원서 매수 신호 월말 종가 매수 →
      월말 10이평 이탈 매도) 중 2010년 이후 신호.
실적: 미국 SEC EDGAR XBRL(companyfacts) — 분기(약 3개월) 매출·영업이익과 **공시일(filed)**.
      신호 달 말일 기준 **이미 공시된** 가장 최근 분기를 1년 전 같은 분기와 비교(미래 정보 사용 안 함, point-in-time).
      매출 성장 = 전년 동기 대비, 영업이익 = 전년 동기보다 증가(적자 축소 포함)했는가.
      해외 기업(20-F·IFRS 공시)·4분기만 있는 경우 등 실적을 못 구한 신호는 "실적 없음"으로 따로 센다.
사용자 규칙과 같은 기준: 매출성장 10%↑(bullish_pattern_scan.MIN_REVENUE_GROWTH), 영업이익 증가(integrated_scan 우선순위).
생존편향: 현재 구성 종목이라 절대 수치는 부풀려짐 — 같은 거래 집합 안에서 실적 조건 충족/미충족 **차이**를 볼 것.
실행: python backtest_fundamentals.py   (SEC 응답은 임시 폴더에 캐시, 결과: backtest_fundamentals_trades.csv)
"""
import sys, os, json, time, tempfile, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')
import numpy as np
import pandas as pd
import requests

UA = {'User-Agent': 'PersonalResearch research@example.com'}
CACHE = os.path.join(tempfile.gettempdir(), 'sec_companyfacts')
IN, OUT = 'backtest_universe_compare_trades.csv', 'backtest_fundamentals_trades.csv'
REV_TAGS = ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet',
            'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueGoodsNet']
OP_TAGS = ['OperatingIncomeLoss']
REV_MIN = 0.10


def cik_map():
    j = requests.get('https://www.sec.gov/files/company_tickers.json', headers=UA, timeout=30).json()
    return {v['ticker'].upper().replace('.', '-'): int(v['cik_str']) for v in j.values()}


def facts(cik):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f'{cik}.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    r = requests.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json', headers=UA, timeout=60)
    time.sleep(0.12)   # SEC 초당 10회 제한
    if r.status_code != 200:
        return None
    j = r.json()
    json.dump(j, open(p, 'w', encoding='utf-8'))
    return j


def quarterly(j, tags):
    """약 3개월짜리 값만 → DataFrame(end, filed, val). 같은 분기는 처음 공시된 값(그때 알 수 있던 값)."""
    rows = []
    gaap = (j or {}).get('facts', {}).get('us-gaap', {})
    for tag in tags:
        for unit, lst in gaap.get(tag, {}).get('units', {}).items():
            if unit != 'USD':
                continue
            for f in lst:
                if 'start' not in f:
                    continue
                s, e = pd.Timestamp(f['start']), pd.Timestamp(f['end'])
                if 80 <= (e - s).days <= 100:
                    rows.append((e, pd.Timestamp(f['filed']), float(f['val'])))
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=['end', 'filed', 'val']).sort_values(['end', 'filed'])
    return df.drop_duplicates('end', keep='first').reset_index(drop=True)


def yoy(q, asof):
    """asof 시점에 공시돼 있던 최근 분기 vs 1년 전 같은 분기. (현재값, 1년전값) 또는 None."""
    if q is None:
        return None
    known = q[q.filed <= asof]
    if known.empty:
        return None
    cur = known.iloc[-1]
    if (asof - cur.end).days > 200:   # 너무 오래된 분기면 신뢰 불가
        return None
    prev = known[(known.end - (cur.end - pd.Timedelta(days=365))).abs() <= pd.Timedelta(days=20)]
    if prev.empty:
        return None
    return cur.val, prev.iloc[-1].val


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
    ev = pd.read_csv(IN)
    ev = ev[(ev.month >= '2010-01') & ev.closed].copy()
    cmap = cik_map()
    tickers = sorted(ev.ticker.unique())
    rev_q, op_q = {}, {}
    for i, t in enumerate(tickers, 1):
        print(f'  SEC {i:>3}/{len(tickers)} {t:<8}', end='\r')
        cik = cmap.get(t.upper())
        if not cik:
            continue
        try:
            j = facts(cik)
        except Exception:
            continue
        rev_q[t], op_q[t] = quarterly(j, REV_TAGS), quarterly(j, OP_TAGS)
    print(' ' * 40)

    rg, og = [], []
    for _, r in ev.iterrows():
        asof = pd.Period(r.month, 'M').end_time.normalize()
        rv = yoy(rev_q.get(r.ticker), asof)
        op = yoy(op_q.get(r.ticker), asof)
        rg.append(rv[0] / rv[1] - 1 if rv and rv[1] > 0 else np.nan)
        og.append((op[0] > op[1]) if op else np.nan)
    ev['rev_g'], ev['op_up'] = rg, og
    ev.to_csv(OUT, index=False, encoding='utf-8-sig')

    has = ev.rev_g.notna() & ev.op_up.notna()
    print('=' * 120)
    print('  매출·영업이익 성장 조건별 월봉매매법 성과 (2010~2026 청산 거래, 신호 시점에 공시된 실적만 사용)')
    print('=' * 120)
    print(f'  전체 신호 {len(ev)}건 중 실적 확인 {int(has.sum())}건 ({has.mean():.0%}), 실적 없음 {int((~has).sum())}건')
    e = ev[has]
    rev_ok = e.rev_g >= REV_MIN
    op_ok = e.op_up.astype(bool)
    groups = [
        ('매출 +10%↑ & 영업이익 증가 (사용자 규칙 최우선)', rev_ok & op_ok),
        ('매출 +10%↑ 만', rev_ok & ~op_ok),
        ('영업이익 증가 만', ~rev_ok & op_ok),
        ('둘 다 아님', ~rev_ok & ~op_ok),
    ]
    print(f'\n  {"실적 확인된 전체":<34}', stats(e.ret))
    for name, m in groups:
        print(f'  {name:<34}', stats(e[m].ret))
    both, rest = e[rev_ok & op_ok].ret, e[~(rev_ok & op_ok)].ret
    lo, hi = boot(both, rest)
    print(f'\n  (매출+10%↑ & 이익증가) − 나머지 평균 차이 95% 구간: {lo:+.1%} ~ {hi:+.1%}')
    lo, hi = boot(e[rev_ok].ret, e[~rev_ok].ret)
    print(f'  (매출+10%↑) − (매출+10% 미만)   평균 차이 95% 구간: {lo:+.1%} ~ {hi:+.1%}')
    e = e.assign(yr=e.month.str[:4].astype(int))
    print('\n  [구간별: 매출+10%↑&이익증가 | 나머지]')
    for y0, y1 in ((2010, 2014), (2015, 2019), (2020, 2026)):
        s = e[e.yr.between(y0, y1)]
        m = (s.rev_g >= REV_MIN) & s.op_up.astype(bool)
        print(f'  {y0}~{y1}: {stats(s[m].ret)}')
        print(f'  {"":>9}  {stats(s[~m].ret)}')
    print('\n  [매출 성장률 구간별]')
    for lo_, hi_, lab in ((-9, 0, '역성장'), (0, .1, '0~10%'), (.1, .25, '10~25%'), (.25, .5, '25~50%'), (.5, 99, '50%↑')):
        m = (e.rev_g >= lo_) & (e.rev_g < hi_)
        print(f'  매출 {lab:<6} {stats(e[m].ret)}')
