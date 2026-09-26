# -*- coding: utf-8 -*-
"""원서 예시 차트 재현 검증 — book_patterns.py가 원서에 실린 차트를 원서와 같은 패턴으로 판정하는가.

원서 2장 예시(쪽수)마다 실제 시세를 받아 그 구간의 후킹/이탈 판정을 출력하고, 기대 판정과 비교한다.
네트워크(yfinance)가 필요해서 CI 회귀테스트(tests/)와 분리한 수동 검증 스크립트.
실행: python verify_book_examples.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
import pandas as pd
import yfinance as yf
import book_patterns as bp

# (설명, 티커, 봉, 구간시작, 구간끝, 기대: [('bull'|'bear'|'comp', 패턴명)])
EXAMPLES = [
    ('p.258 나스닥 월봉 쌍바닥 (후킹 A=2023-01)',       '^IXIC',     '1mo', '2022-10', '2023-02', [('bull', '쌍바닥')]),
    ('p.271 S&P500 월봉 역H&S (2023-01 후킹)',          '^GSPC',     '1mo', '2022-12', '2023-02', [('bull', '역H&S')]),
    ('p.271 S&P500 월봉 H&S (2022 고점)',               '^GSPC',     '1mo', '2022-01', '2022-05', [('bear', 'H&S')]),
    ('p.264 카카오 월봉 쌍봉 (B=2021-09 이탈)',          '035720.KS', '1mo', '2021-08', '2021-10', [('bear', '쌍봉')]),
    ('p.277 SAMG엔터 주봉 삼중바닥',                     '419530.KQ', '1wk', '2024-11', '2025-02', [('bull', '삼중바닥')]),
    ('p.298 나스닥 주봉 되돌림3 (B 장대양봉)',           '^IXIC',     '1wk', '2022-12', '2023-01-20', [('comp', '되돌림3')]),
    ('p.290 다우 주봉 대쌍바닥 (2015~2016)',             '^DJI',      '1wk', '2016-01', '2016-07', [('comp', '대쌍바닥')]),
    # 원서는 '겹쌍봉'이라 부르지만 두 쌍봉이 각각 붙은 월봉 두 개(2015-10/11, 2016-04/05)라 코드상 한 천장씩 — A(저승사자) 시점과 쌍봉만 재현
    ('p.285 중앙첨단소재 월봉 겹쌍봉 (A 저승사자=2016-06)', '051980.KQ', '1mo', '2016-01', '2016-07', [('bear', '쌍봉')]),
    ('p.289 에프알텍 월봉 대쌍봉',                       '073540.KQ', '1mo', '2022-06', '2022-10', [('bcomp', '대쌍봉')]),
    ('p.295 HLB글로벌 주봉 되돌림1(쌍바닥→쌍봉)',        '003580.KS', '1wk', '2021-05', '2021-05-31', [('bcomp', '되돌림1(하락)')]),
]


FIXTURE_DIR = 'tests/fixtures/book_examples'
PAD_BEFORE = 150   # 구간 앞 여유 봉수 — MA10(10) + 탐색구간(24) + 복합패턴 앞선 패턴 탐색(24×2)보다 넉넉히


def fixture_name(tk, iv, a):
    return f"{tk.replace('^', '').replace('.', '_')}_{iv}_{a}.csv"


def load(tk, iv, a, b, offline=False):
    """offline=True면 tests/fixtures에 고정해 둔 시세를 쓴다(CI용, 네트워크 불필요·재현성 고정)."""
    import os
    path = os.path.join(FIXTURE_DIR, fixture_name(tk, iv, a))
    if offline:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return df
    df = yf.Ticker(tk).history(period='max', interval=iv, auto_adjust=True)
    df.index = df.index.tz_localize(None)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']]


def save_fixtures():
    import os
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    for desc, tk, iv, a, b, expect in EXAMPLES:
        df = load(tk, iv, a, b)
        end = pd.Timestamp(b) + pd.offsets.MonthEnd(0)
        pos = df.index.searchsorted(pd.Timestamp(a))
        cut = df.iloc[max(0, pos - PAD_BEFORE):][lambda x: x.index <= end]
        cut.to_csv(os.path.join(FIXTURE_DIR, fixture_name(tk, iv, a)))
        print(f'saved {fixture_name(tk, iv, a)} ({len(cut)}봉)')


def run(offline=False):
    passed = 0
    for desc, tk, iv, a, b, expect in EXAMPLES:
        df = load(tk, iv, a, b, offline=offline)
        d = bp.prepare(df)
        idx = [i for i, ts in enumerate(d.index) if pd.Timestamp(a) <= ts <= pd.Timestamp(b) + pd.offsets.MonthEnd(0)]
        found = []
        print(f'\n■ {desc}  [{tk} {iv}]')
        for t in idx:
            ds = d.index[t].strftime('%Y-%m-%d')
            if bp.is_hook(d, t):
                r = bp.bullish_at(d, t)
                comps = bp.composite_at(d, t)
                lab = r['pattern'] if r else '패턴없음'
                cl = ', '.join(c['pattern'] + (f"({c.get('undone')}→{c.get('by', '캔들')})" if 'undone' in c else '') for c in comps)
                print(f'   {ds} 후킹 → {lab}' + (f' | 복합: {cl}' if cl else ''))
                if r:
                    found.append(('bull', r['pattern']))
                    print('       바닥:', [d.index[x].strftime('%Y-%m-%d') for x in r['bottoms']],
                          '저가:', [round(x, 2) for x in r['bottom_lows']])
                    print('       참고조건:', {k: ('O' if v else 'X') for k, v in r['quality'].items()})
                found += [('comp', c['pattern']) for c in comps]
            if bp.is_breakdown(d, t):
                r = bp.bearish_at(d, t)
                lab = r['pattern'] if r else '패턴없음'
                print(f'   {ds} 이탈 → {lab}')
                bc = bp.bear_composite_at(d, t)
                if bc:
                    print('       하락복합:', [c['pattern'] for c in bc])
                found += [('bcomp', c['pattern']) for c in bc]
                if r:
                    found.append(('bear', r['pattern']))
                    print('       천장:', [d.index[x].strftime('%Y-%m-%d') for x in r['tops']],
                          '고가:', [round(x, 2) for x in r['top_highs']], '참고:', {k: ('O' if v else 'X') for k, v in r['quality'].items()})
        ok = all(e in found for e in expect)
        passed += ok
        print(f'   => 기대 {expect} : {"✅ 재현" if ok else "❌ 불일치"}')
    print(f'\n원서 예시 재현: {passed}/{len(EXAMPLES)}')
    return passed == len(EXAMPLES)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'save':
        save_fixtures()          # 원서 예시 시세를 tests/fixtures에 고정
    else:
        sys.exit(0 if run(offline=len(sys.argv) > 1 and sys.argv[1] == 'offline') else 1)
