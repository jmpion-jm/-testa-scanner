# -*- coding: utf-8 -*-
"""
원서 패턴 구현(book_patterns.py) 회귀 테스트 — 해석이 모델·세션마다 바뀌지 않도록 잠근다.

2026-09-26 사용자 요청: "모델 변경 때마다 바꾸는 것도 스트레스". 규칙은
`캔들차트(성승현작가)/패턴_구현명세.md`에 쪽수 근거와 함께 고정돼 있고, 이 테스트가 그 명세를 강제한다.
이 테스트가 깨지면 코드를 고치기 전에 명세서와 사용자 승인을 먼저 확인할 것.

1) 원서 예시 차트 10개 재현 (tests/fixtures/book_examples에 고정한 시세 — 네트워크 불필요)
2) 핵심 규칙 경계 조건 (합성 데이터)
3) 조건값 잠금 (명세서 §4와 같은 값인지)
"""
import os, sys
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)
import book_patterns as bp

failures = []


def check(name, cond, detail=''):
    print(f"  {'✅' if cond else '❌'} {name}" + ('' if cond else f' — {detail}'))
    if not cond:
        failures.append(name)


def bars(closes, start='2000-01-01'):
    """종가 목록 → OHLCV. 시가=직전 종가, 고·저가=몸통 ±0.5%, 거래량 일정."""
    c = np.array(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * 1.005
    lo = np.minimum(o, c) * 0.995
    idx = pd.date_range(start, periods=len(c), freq='MS')
    return pd.DataFrame({'Open': o, 'High': hi, 'Low': lo, 'Close': c, 'Volume': 1000.0}, index=idx)


def test_constants_locked():
    print('\n[조건값 잠금 — 명세서 §4]')
    expect = {'MA_PERIOD': 10, 'AVG_WIN': 10, 'VOL_BURST_MULT': 3.0, 'VOL_2ND': (0.7, 2.0),
              'BIG_BODY_PCT': 0.05, 'PULLBACK_VOL_IDEAL': 1 / 7,
              'BREAK_SEARCH': 2, 'LOOKBACK': 24, 'MAX_OPPOSITE': 2, 'EQ_TOL': 0.01}
    for k, v in expect.items():
        check(f'{k} = {v}', getattr(bp, k) == v, f'실제={getattr(bp, k)}')


def test_core_rules():
    print('\n[핵심 규칙 경계 조건]')
    base = [100.0] * 12                     # MA10 ≈ 100
    down = [96, 92, 88, 85, 82, 80]          # 하락 → 첫 바닥 80
    bounce = [84, 87, 85]
    # 쌍바닥: 두 번째 바닥 80.5(첫 바닥 이상) → 10이평 아래에 머물다 마지막 봉에서 후킹
    dbl = base + down + bounce + [82, 80.5, 82] + [95]
    d = bp.prepare(bars(dbl))
    t = len(d) - 1
    check('합성 쌍바닥: 마지막 봉이 후킹', bp.is_hook(d, t), f'close={d.Close.iat[t]:.1f} MA={d.MA.iat[t]:.1f}')
    r = bp.bullish_at(d, t)
    check('합성 쌍바닥 판정 (p.253)', r is not None and r['pattern'] == '쌍바닥', f'실제={r and r["pattern"]}')

    # 두 번째 바닥이 첫 바닥보다 3% 낮음 → 쌍바닥 아님(전저점 붕괴, p.254~255)
    broken = base + down + bounce + [82, 77.0, 80] + [95]
    d2 = bp.prepare(bars(broken))
    r2 = bp.bullish_at(d2, len(d2) - 1)
    check('오른쪽 바닥이 1% 넘게 낮으면 쌍바닥 아님', r2 is None or r2['pattern'] != '쌍바닥', f'실제={r2 and r2["pattern"]}')

    # V자: 바닥 한 번 뒤 곧바로 후킹 → 패턴 없음(p.254 "V자 반등은 거의 발생하지 않는다")
    v = base + down + [95]
    d3 = bp.prepare(bars(v))
    check('V자 반등은 패턴 아님', bp.bullish_at(d3, len(d3) - 1) is None)

    # 후킹 정의: 음봉이면 10이평을 넘어도 후킹 아님
    neg = bars(dbl)
    neg.iloc[-1, neg.columns.get_loc('Open')] = 97.0
    neg.iloc[-1, neg.columns.get_loc('Close')] = 96.0   # 음봉(시가>종가)
    d4 = bp.prepare(neg)
    check('음봉은 후킹 아님 (p.256 "장대양봉")', not bp.is_hook(d4, len(d4) - 1))

    # 하락 완성 정의: 거울 — 쌍봉 후 10이평 뚫는 음봉
    up = [104, 108, 112, 115, 118, 120]
    pull = [116, 113, 115]
    top = [100.0] * 12 + up + pull + [118, 119.5, 117] + [104]
    d5 = bp.prepare(bars(top))
    j = len(d5) - 1
    check('합성 쌍봉: 마지막 봉이 하락 완성 음봉', bp.is_breakdown(d5, j), f'close={d5.Close.iat[j]:.1f} MA={d5.MA.iat[j]:.1f}')
    r5 = bp.bearish_at(d5, j)
    check('합성 쌍봉 판정 (p.260)', r5 is not None and r5['pattern'] == '쌍봉', f'실제={r5 and r5["pattern"]}')


def test_chapter_rules():
    print('\n[1·4·5·6장 규칙 — 매매법_전체_구현명세.md]')
    # 장대양봉 = 몸통 ≥ 전일 대비 5% (p.216)
    d = bp.prepare(bars([100.0] * 12 + [106]))
    check('몸통 +6% 양봉 = 장대양봉 (p.216)', bp.is_big_bull(d, len(d) - 1))
    d = bp.prepare(bars([100.0] * 12 + [103]))
    check('몸통 +3% 양봉은 장대양봉 아님', not bp.is_big_bull(d, len(d) - 1))
    # 은둔형 장대양봉: 작은 양봉 연속 합산 (p.217, p.249)
    d = bp.prepare(bars([100.0] * 12 + [102, 104, 106]))
    check('+2%×3 연속 양봉 = 은둔형 장대양봉 (p.249)', bp.is_big_bull(d, len(d) - 1))

    # 4등분선 (p.219~223): 장대양봉 100→110 뒤 종가 108 = 80% 안전지대 / 102 = 20% 절대자리 훼손
    d = bp.prepare(bars([100.0] * 12 + [110, 108]))
    r = bp.four_division(d)
    check('4등분선 80% = 안전지대 (p.220)', r is not None and r['zone'] == '안전지대', f'실제={r}')
    d = bp.prepare(bars([100.0] * 12 + [110, 102]))
    r = bp.four_division(d)
    check('4등분선 20% = 절대자리 훼손 (p.221)', r is not None and r['zone'] == '절대자리 훼손', f'실제={r}')

    # 눌림목 거래량 (p.364): 상승구간 최대 7000 대비 현재 500 = 1/14 → 금상첨화
    df = bars([100.0] * 12 + [105, 110, 115, 120, 117])
    df['Volume'] = [1000.0] * 12 + [2000, 3000, 7000, 5000, 500]
    r = bp.pullback_volume(bp.prepare(df))
    check('눌림목 거래량 1/14 = 금상첨화 (p.364)', r is not None and r['ideal'], f'실제={r}')
    df.iloc[-1, df.columns.get_loc('Volume')] = 3000.0
    r = bp.pullback_volume(bp.prepare(df))
    check('눌림목 거래량 3/7은 금상첨화 아님', r is not None and not r['ideal'], f'실제={r}')

    # 월봉 포킹 (p.384~387): 종가가 5·10·20 이평을 한 봉에 동시 돌파
    d = bp.add_long_mas(bp.prepare(bars([100.0] * 20 + [90.0] * 6 + [105])))
    check('5·10·20 동시 돌파 = 포킹 (p.384)', bp.is_forking(d, len(d) - 1))
    d = bp.add_long_mas(bp.prepare(bars([100.0] * 20 + [90.0] * 6 + [93])))
    check('5이평만 넘으면 포킹 아님', not bp.is_forking(d, len(d) - 1))


def test_book_examples():
    print('\n[원서 예시 차트 재현 — 고정 시세]')
    import verify_book_examples as vbe
    ok = vbe.run(offline=True)
    check('원서 예시 10개 전부 재현', ok)


if __name__ == '__main__':
    print('=' * 60)
    print('  원서 패턴 구현 회귀 테스트')
    print('=' * 60)
    test_constants_locked()
    test_core_rules()
    test_chapter_rules()
    test_book_examples()
    print('\n' + '=' * 60)
    if failures:
        print(f'  실패 {len(failures)}건: {", ".join(failures)}')
        sys.exit(1)
    print('  전체 통과')
