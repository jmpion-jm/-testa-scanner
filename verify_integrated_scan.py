# -*- coding: utf-8 -*-
"""
integrated_scan.py 회귀 검증 하네스 — integrated_scan.py·book_patterns.py의 함수를 재사용하지 않고
완전히 독립적으로 다시 계산해서, 이번 실행 결과(매수 신호 + 관찰 목록)가 일치하는지 대조한다.
대조 항목: 확정 월(진행 중인 달 제외), 확정 월말 10이평 대비 %, 원서 매수 신호(돌파=후킹 / 지지),
그리고 전체 종목 중 매수 신호를 스크립트가 빠뜨린 종목이 없는지.
(2026-09-26 원서 원칙 전환에 맞춰 재작성 — 이전 판은 월봉%/주봉%만 대조)

실행: python -X utf8 verify_integrated_scan.py
"""
import warnings
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf
import integrated_scan as S
import market_time as mt
# 주의: integrated_scan.py 임포트 시점에 sys.stdout을 utf-8 TextIOWrapper로 재설정한다 —
# 여기서 또 감싸면 버퍼가 이중으로 닫히므로 `python -X utf8`로 실행한다.

TOL = 0.15   # 허용 오차(퍼센트포인트) — 재조회 시점 차이로 인한 미세한 시세 변동 감안


def independent(ticker):
    """독립 계산: (확정 월 'YYYY-MM', 월말 10이평 대비 %, 신호 '돌파'/'지지'/None)."""
    df = yf.Ticker(ticker).history(period='3y', interval='1mo', auto_adjust=True)
    df = df[['Open', 'Low', 'Close']].dropna()
    df.index = df.index.tz_localize(None)
    ref = pd.Period(mt.us_ref_date(), 'M')
    if df.index[-1].to_period('M') == ref and not mt.is_monthend_after_close():
        df = df.iloc[:-1]                      # 진행 중인 달 제외
    ma = df['Close'].rolling(10).mean()
    o, lo, c = float(df['Open'].iloc[-1]), float(df['Low'].iloc[-1]), float(df['Close'].iloc[-1])
    m, pc, pm = float(ma.iloc[-1]), float(df['Close'].iloc[-2]), float(ma.iloc[-2])
    if pc <= pm and c > o and o <= m < c:
        sig = '돌파'
    elif pc > pm and lo <= m < c:
        sig = '지지'
    else:
        sig = None
    return df.index[-1].strftime('%Y-%m'), (c - m) / m * 100, sig


def main():
    buy, watch, skipped = S.scan()
    got = {c['ticker']: c for c in buy + watch}
    print(f'\n검증: 매수신호 {len(buy)} + 관찰 {len(watch)} (전체 {len(S.STOCKS)}종목 중 스킵 {len(skipped)})')
    skip_t = {t for t, _, _ in skipped}
    fail = checked = 0
    for t in S.STOCKS:
        if t in skip_t:
            continue
        try:
            month, pct, sig = independent(t)
        except Exception as e:
            print(f'  [검증실패] {t}: 독립계산 안 됨 ({e})')
            fail += 1
            continue
        checked += 1
        c = got.get(t)
        script_sig = c.get('signal') if c else None
        if sig != script_sig:
            fail += 1
            print(f'  [신호 불일치] {t}: 스크립트 {script_sig} vs 독립계산 {sig}')
            continue
        if c and (c['sig_month'] != month or abs(c['m_pct'] - pct) > TOL):
            fail += 1
            print(f"  [수치 불일치] {t}: 스크립트({c['sig_month']} {c['m_pct']:+.1f}%) vs 독립계산({month} {pct:+.1f}%)")
    print(f'\n결과: {checked}종목 대조, 불일치/실패 {fail}건')
    print('→ 전체 일치. 확정 월·10이평 %·원서 매수 신호 계산은 신뢰 가능.' if fail == 0 else '→ 불일치 발견 — 위 종목 확인 필요.')


if __name__ == '__main__':
    main()
