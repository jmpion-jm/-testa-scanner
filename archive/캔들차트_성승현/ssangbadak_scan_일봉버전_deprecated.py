# -*- coding: utf-8 -*-
"""
쌍바닥(더블보텀) 정량 스크리너 — 성승현 매매법 2장 원문 확인 조건 기반
(원서 p.256~257 직접 대조 완료, 2026-09-10)

⚠️ 이건 "패턴을 완벽히 알아보는" 도구가 아니라, 원서가 제시한 정량 조건
(전저점 사수, 후킹/펌핑/랠리 구조, 거래량 조건)을 걸러내는 1차 스크리너다.
오탐/누락이 있을 수 있으므로 결과는 반드시 차트로 육안 재확인할 것.

원서 조건 (직접 인용, p.256~257):
- "쌍바닥 패턴이 완성되면 들어가는 자리는 세 군데다. 우선 후킹 캔들의
   종가나 10이평선의 지지를 확인한 펌핑 캔들 종가."
- "금일 9시~9시3분의 첫 캔들 거래량이 전일 9시~9시3분의 첫 캔들 거래량보다
   200% 이상 터졌다면 들어간다." → 3분봉 데이터는 야후파이낸스 무료
   API로 못 구하므로, 이 스크립트에서는 **일봉 거래량이 20일 평균 대비
   얼마나 터졌는지**로 근사 대체한다 (원서와 다른 근사치임을 명시).
- "오른쪽 엉덩이가 더 높은 소위 '짝궁둥이 쌍바닥'이 훨씬 더 세다." →
   2차 저점이 1차 저점보다 높으면 'strong' 태그.
"""
import sys, json, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)
MA_PERIOD = CFG.get('ma_period', 10)

TROUGH_ORDER   = 8     # 저점 판정 시 좌우 몇 거래일 이내 최저값이어야 하는지
LOOKBACK_DAYS  = 130   # 약 6개월 — 두 저점 + 최근 돌파 구간을 담을 기간
NECKLINE_TOL   = 0.03  # 넥라인(중간 고점) 돌파 판정 여유폭 3%
BREAKOUT_VOL_X = 1.5   # 돌파일 거래량이 20일 평균 대비 이 배수 이상이면 신뢰도↑


def fetch(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def find_ssangbadak(df: pd.DataFrame) -> dict | None:
    """최근 LOOKBACK_DAYS 구간에서 쌍바닥 후보를 찾는다."""
    if len(df) < LOOKBACK_DAYS:
        return None
    win = df.iloc[-LOOKBACK_DAYS:].copy()
    win['MA10'] = df['Close'].rolling(MA_PERIOD).mean().iloc[-LOOKBACK_DAYS:]
    lows = win['Low'].values

    trough_idx = argrelextrema(lows, np.less_equal, order=TROUGH_ORDER)[0]
    # 인접한 중복 저점 제거 (같은 바닥이 여러 인덱스로 잡히는 것 방지)
    trough_idx = [i for n, i in enumerate(trough_idx)
                  if n == 0 or i - trough_idx[n - 1] > TROUGH_ORDER]
    if len(trough_idx) < 2:
        return None

    # 최근 두 개의 저점만 사용 (1차 저점, 2차 저점)
    i1, i2 = trough_idx[-2], trough_idx[-1]
    if i2 - i1 < TROUGH_ORDER or i2 - i1 > 90:
        return None  # 두 저점 간격이 너무 좁거나(노이즈) 너무 넓음(다른 사이클)

    low1, low2 = float(win['Low'].iloc[i1]), float(win['Low'].iloc[i2])
    if low2 < low1 * 0.97:
        return None  # 전저점을 유의미하게 깼음 → 쌍바닥 아님(원서: 전저점 사수 필수)

    # 두 저점 사이의 넥라인(중간 고점)
    mid = win.iloc[i1:i2 + 1]
    neckline = float(mid['High'].max())

    # 2차 저점 이후 현재까지 돌파 여부 확인
    after = win.iloc[i2:]
    if after.empty:
        return None
    cur_close = float(win['Close'].iloc[-1])
    cur_ma10  = float(win['MA10'].iloc[-1]) if not pd.isna(win['MA10'].iloc[-1]) else None
    breakout  = cur_close >= neckline * (1 - NECKLINE_TOL)

    avg_vol20 = float(win['Volume'].iloc[-21:-1].mean()) or 1
    cur_vol_r = float(win['Volume'].iloc[-1]) / avg_vol20

    strong = low2 > low1 * 1.01  # 짝궁둥이형 (오른쪽 저점이 더 높음)

    return {
        'low1': low1, 'low2': low2, 'neckline': neckline,
        'cur_close': cur_close, 'cur_ma10': cur_ma10,
        'breakout': breakout, 'strong': strong,
        'cur_vol_r': round(cur_vol_r, 2),
        'days_since_low2': len(win) - 1 - i2,
        'above_ma10': (cur_close > cur_ma10) if cur_ma10 else None,
    }


def is_uptrend(ticker: str) -> bool:
    """진짜 우상향인지 '월봉' 기준으로 확인 (일봉 교차 횟수는 노이즈라 부적합).
    원서 3장(p.308~310) 경고: 혼조추세(박스권)가 가장 위험한 유형.
    판정: 최근 6개월 월봉MA10이 대체로 우상향(3개월 전보다 지금이 높음)
    이고, 현재가가 6개월 전 대비 상승했는지로 매크로 방향성만 본다.
    """
    try:
        dm = yf.Ticker(ticker).history(period="2y", interval="1mo", auto_adjust=True)
        if len(dm) < 8:
            return False
        dm['MA10'] = dm['Close'].rolling(MA_PERIOD).mean()
        ma_now    = float(dm['MA10'].iloc[-1])
        ma_3m_ago = float(dm['MA10'].iloc[-4])
        price_now   = float(dm['Close'].iloc[-1])
        price_6m_ago = float(dm['Close'].iloc[-7])
        return ma_now > ma_3m_ago and price_now > price_6m_ago
    except Exception:
        return False


MIN_REVENUE_GROWTH = 0.10   # 성장성 없는 종목은 쳐다보지도 말 것 — 최소 매출성장률 10%


def get_revenue_growth(ticker: str) -> float | None:
    """전년동기대비 매출성장률. 데이터 없으면 None (필터에서 제외 대상)."""
    try:
        info = yf.Ticker(ticker).info
        g = info.get('revenueGrowth')
        return float(g) if g is not None else None
    except Exception:
        return None


def scan_universe(universe: list[tuple[str, str]], label: str) -> list[dict]:
    results = []
    total = len(universe)
    for n, (ticker, name) in enumerate(universe, 1):
        print(f'  [{label}] {n:>3}/{total} {ticker:<8}', end='\r')
        try:
            df = fetch(ticker)
            r = find_ssangbadak(df)
            if r and r['breakout'] and is_uptrend(ticker):
                r.update(ticker=ticker, name=name)
                results.append(r)
        except Exception:
            continue
    print(' ' * 40, end='\r')

    # 기술적 조건 통과한 종목만 매출성장률 조회 (API 호출 최소화)
    print(f'  기술적 필터 통과 {len(results)}종목 — 성장성 확인 중...')
    survivors = []
    for r in results:
        g = get_revenue_growth(r['ticker'])
        r['revenue_growth'] = g
        if g is not None and g >= MIN_REVENUE_GROWTH:
            survivors.append(r)

    survivors.sort(key=lambda x: (-x['strong'], -x['cur_vol_r']))
    return survivors


def print_report(results: list[dict], label: str):
    print(f"\n{'='*98}\n  쌍바닥 정량 스크리너 — {label}   [{datetime.today().strftime('%Y-%m-%d')}]\n{'='*98}")
    if not results:
        print("  조건 만족 종목 없음 (기술적 조건 + 혼조추세 제외 + 매출성장률"
              f" {MIN_REVENUE_GROWTH*100:.0f}%+ 전부 통과한 종목 없음)")
        return
    print(f"  {'종목':<8} {'이름':<20} {'1차저점':>10} {'2차저점':>10} {'넥라인':>10}"
          f" {'현재가':>10} {'거래량배수':>8} {'매출성장률':>8} {'짝궁둥이':>6} {'MA10위':>6}")
    print('  ' + '-' * 94)
    for r in results:
        g = r['revenue_growth']
        g_str = f"{g*100:+.0f}%" if g is not None else "N/A"
        print(f"  {r['ticker']:<8} {r['name']:<20} {r['low1']:>10,.2f} {r['low2']:>10,.2f}"
              f" {r['neckline']:>10,.2f} {r['cur_close']:>10,.2f} {r['cur_vol_r']:>7.1f}x"
              f" {g_str:>9} {'✅' if r['strong'] else '  ':>6} {'✅' if r['above_ma10'] else '  ':>6}")
    print(f"\n  총 {len(results)}종목 — 넥라인 돌파 + 방향성 있는 추세(혼조추세 제외)"
          f" + 매출성장률 {MIN_REVENUE_GROWTH*100:.0f}%+ 전부 통과")
    print("  ⚠️ 1차 스크리너 결과입니다. 반드시 차트로 육안 재확인 후 매매 판단하세요.")
    print("  ⚠️ 매출성장률은 매출 규모가 작은 회사일수록 왜곡(과장)될 수 있음 — 절대수치도 같이 확인할 것")
    print('=' * 98 + '\n')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('universe', choices=['nasdaq100', 'sp500', 'kospi'], nargs='?', default='nasdaq100')
    args = p.parse_args()

    if args.universe == 'nasdaq100':
        from nasdaq100_scan import get_ndx100_tickers
        tickers = get_ndx100_tickers()
        universe = [(t, t) for t in tickers]
        label = 'NASDAQ100'
    elif args.universe == 'sp500':
        from sp500_scan import get_sp500_tickers
        tickers = get_sp500_tickers()
        universe = [(t, t) for t in tickers]
        label = 'S&P500'
    else:
        from testa_scan import get_universe
        # pykrx용 순수 6자리 코드 → yfinance용 '.KS' 접미사로 변환
        universe = [(f'{code}.KS', name) for code, name in get_universe()]
        label = 'KOSPI(테스타 유니버스)'

    print(f'\n  {label} 쌍바닥 스캔 중... ({len(universe)}종목)')
    results = scan_universe(universe, label)
    print_report(results, label)
