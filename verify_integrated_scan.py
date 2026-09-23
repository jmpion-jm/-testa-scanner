# -*- coding: utf-8 -*-
"""
integrated_scan.py 회귀 검증 하네스 — integrated_scan.py의 내부 함수를 재사용하지 않고
완전히 독립적으로 다시 계산해서, 이번 실행 결과(buy_candidates+watch_list)의 월봉%/주봉%가
일치하는지 65종목 전체를 대조한다. 코드가 나중에 또 바뀌어도 이 스크립트로 재검증 가능.

실행: python verify_integrated_scan.py
"""
import sys, warnings
warnings.filterwarnings('ignore')

import yfinance as yf
import integrated_scan as S
# 주의: integrated_scan.py 임포트 시점에 자체적으로 sys.stdout을 utf-8 TextIOWrapper로
# 재설정한다 — 이 스크립트에서 먼저 같은 방식으로 감싸면 버퍼가 이중으로 닫히는
# 문제가 있어(2026-09-23 확인), 여기서는 별도로 감싸지 않고 `python -X utf8`로 실행한다.

TOL = 0.15   # 허용 오차(퍼센트포인트) — 재조회 시점 차이로 인한 미세한 시세 변동 감안


def independent_pct(ticker, interval, period):
    """integrated_scan.fetch()와 완전히 별도로 짠 계산 (같은 버그 공유 방지)."""
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    closes = df['Close'].dropna()
    ma = closes.rolling(10).mean()
    c, m = float(closes.iloc[-1]), float(ma.iloc[-1])
    return (c - m) / m * 100


def main():
    buy_candidates, watch_list, skipped = S.scan()
    print(f'\n검증 대상: 매수후보 {len(buy_candidates)} + 관찰대상 {len(watch_list)} = '
          f'{len(buy_candidates) + len(watch_list)}종목 (전체 {len(S.STOCKS)}종목 중 스킵 {len(skipped)})')

    fail = 0
    checked = 0
    for c in buy_candidates + watch_list:
        t = c['ticker']
        try:
            m_pct_indep = independent_pct(t, '1mo', '3y')
            w_pct_indep = independent_pct(t, '1wk', '2y')
        except Exception as e:
            print(f'  [검증실패] {t}: 독립계산 자체가 안 됨 ({e})')
            fail += 1
            continue
        checked += 1
        m_diff = abs(m_pct_indep - c['m_pct'])
        w_diff = abs(w_pct_indep - c['w_pct'])
        if m_diff > TOL or w_diff > TOL:
            fail += 1
            print(f"  [불일치] {t}: 스크립트(월{c['m_pct']:+.1f}/주{c['w_pct']:+.1f}) "
                  f"vs 독립계산(월{m_pct_indep:+.1f}/주{w_pct_indep:+.1f})")

    print(f'\n결과: {checked}종목 대조, 불일치/실패 {fail}건')
    if fail == 0:
        print('→ 전체 일치. integrated_scan.py의 월봉%/주봉% 계산은 신뢰 가능.')
    else:
        print('→ 불일치 발견 — 위 종목들 확인 필요.')


if __name__ == '__main__':
    main()
