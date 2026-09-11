# -*- coding: utf-8 -*-
"""
패턴 스캐너 — 성승현 매매법 2장 원문 확인 기하학적 패턴 6종
(캔들차트(성승현작가)/성승현_매매법_핵심정리.md § 6 참고, 원서 p.252~301 전량 이미지 대조 완료 2026-09-12)

⚠️ 2026-09-12 두 차례 재보정:
1차) 최초 버전은 일봉(10일 이평)으로 만들었더니 관심종목 65개 중 30개(약 46%)가
   "쌍봉/삼고점"으로 잡히는 등 사실상 스크리너 기능을 못했다 → 주봉으로 교체해 65개 중
   5종목으로 정상화.
2차) 그런데 이 매매법의 정체성 자체가 **월봉 10이평선**이다(핵심정리.md §1, CLAUDE.md
   "노후자산 시스템" 규칙 — 매도 조건은 오직 "월봉MA10 하향 이탈"). 주봉은 이 시스템에서
   눌림목 진입 타이밍을 잡는 보조 신호일 뿐, 장기 추세 판정의 주인은 항상 월봉이다.
   원서 2장의 이 패턴들 실전 사례도 카카오·S&P500은 월봉, SAMG엔터·삼성전자는 주봉으로
   혼재하지만, "보유 종목의 장기 추세가 꺾였는가"를 묻는 이 스크립트의 목적상 실제
   매매 시스템과 동일한 **월봉(1mo) 데이터 + 월봉 10이평**으로 다시 맞췄다.

상승(신규 매수 후보 발굴, 시장 유니버스 스캔):
  - 역H&S   (원서 p.269~272)
  - 삼중바닥 (원서 p.276~278)
  - 컵위드핸들(원형바닥, 원서 p.280~282) — 손잡이 깊이 기준은 원서에 수치가 없어
    일반적인 차트 실무 관행(컵 깊이의 50% 이내)을 근사치로 사용함. 원서 수치 아님.

하락(보유·관심 종목 조기경보, config.json의 stocks 워치리스트 대상):
  - 쌍봉    (원서 p.260~264)
  - H&S    (원서 p.266~268)
  - 삼고점  (원서 p.273~275)

원형천장(p.279)은 원서가 "10이평 하향 이탈 시 청산하면 그만, 신경 쓸 필요 없는 패턴"이라고
직접 명시하고 있어 별도 탐지 코드를 두지 않았다.

겹쌍봉/겹쌍바닥/대쌍봉/대쌍바닥/되돌림 1~4패턴은 기본 패턴 2개가 시간차를 두고 연속
발생하는 복합 패턴이라, 종목별 과거 패턴 탐지 이력을 누적 저장해야 판정할 수 있다.
이번 1차 구현 범위에서는 제외했다(핵심정리.md 참고).

이 스크립트는 아직 어떤 GitHub Actions 워크플로우에도 연결되지 않은 독립 실행 도구다.
ssangbadak_scan.py와 마찬가지로 1차 스크리너일 뿐이므로 결과는 반드시 차트로 육안
재확인할 것.
"""
import sys, json, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from datetime import datetime

from ssangbadak_scan import is_uptrend, MA_PERIOD

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)

ORDER_MONTHS    = 2   # 극점(고점/저점) 판정 시 좌우 몇 "개월" 이내 최댓값/최솟값이어야 하는지
LOOKBACK_MONTHS = 60  # 5년 — 원서 실전 사례(카카오 2~3년, S&P500 3년, 호치민 4년 등)에 맞춤
MIN_GAP_MONTHS  = 2   # 두 극점 사이 최소 간격(너무 붙어있으면 같은 파동의 노이즈로 간주)
MAX_GAP_MONTHS  = 48  # 두 극점 사이 최대 간격(4년 이상 벌어지면 별개 사이클로 간주)
LEVEL_TOL = 0.03      # 고점/저점 "비슷한 수준" 판정 허용폭 3%


def fetch_monthly(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period="15y", interval="1mo", auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def _extrema_idx(values, comparator, order=ORDER_MONTHS):
    idx = list(argrelextrema(values, comparator, order=order)[0])
    return [i for n, i in enumerate(idx) if n == 0 or i - idx[n - 1] > order]


def _extrema_sequence(win: pd.DataFrame, order=ORDER_MONTHS):
    """고점(P)·저점(T)을 시간순으로 섞어서 반환. H&S류 5점 패턴 탐지에 사용."""
    peak_idx = _extrema_idx(win['High'].values, np.greater_equal, order)
    trough_idx = _extrema_idx(win['Low'].values, np.less_equal, order)
    points = sorted([(i, 'P') for i in peak_idx] + [(i, 'T') for i in trough_idx])
    cleaned = []
    for i, kind in points:
        if cleaned and cleaned[-1][1] == kind and i - cleaned[-1][0] <= order:
            cleaned[-1] = (i, kind)
        else:
            cleaned.append((i, kind))
    return cleaned


def _window(df: pd.DataFrame):
    if len(df) < LOOKBACK_MONTHS // 2:  # 최소 2.5년치는 있어야 의미있는 패턴 판단 가능
        return None
    win = df.iloc[-LOOKBACK_MONTHS:].copy()
    win['MA10'] = df['Close'].rolling(MA_PERIOD).mean().iloc[-LOOKBACK_MONTHS:]
    return win


def _cur_ma10(win: pd.DataFrame):
    v = win['MA10'].iloc[-1]
    return None if pd.isna(v) else float(v)


def _valid_gap(i1, i2):
    gap = i2 - i1
    return MIN_GAP_MONTHS <= gap <= MAX_GAP_MONTHS


# ---------------------------------------------------------------- 쌍봉 (하락)
def find_ssangbong(df: pd.DataFrame) -> dict | None:
    """쌍봉 패턴 — 원서 p.260~264, p.272 표. (월봉 기준)
    전제조건: 2차 고점이 전고점을 유의미하게 못 넘음. 완성: 10월이평 하향 이탈(저승사자 캔들)
    이면서 동시에 넥라인(중간 저점)도 이탈해야 신호로 인정 — 둘 중 하나만으로는 일시적
    눌림과 구분이 안 돼 오탐이 너무 많았음(2026-09-12 재보정).
    """
    win = _window(df)
    if win is None:
        return None
    peak_idx = _extrema_idx(win['High'].values, np.greater_equal)
    if len(peak_idx) < 2:
        return None
    i1, i2 = peak_idx[-2], peak_idx[-1]
    if not _valid_gap(i1, i2):
        return None

    high1, high2 = float(win['High'].iloc[i1]), float(win['High'].iloc[i2])
    if high2 > high1 * (1 + LEVEL_TOL):
        return None  # 전고점을 의미 있게 돌파 → 쌍봉 아님

    neckline = float(win.iloc[i1:i2 + 1]['Low'].min())
    cur_close = float(win['Close'].iloc[-1])
    cur_ma10 = _cur_ma10(win)
    broke_neckline = cur_close <= neckline * (1 + LEVEL_TOL)
    below_ma10 = cur_ma10 is not None and cur_close < cur_ma10
    broke_down = broke_neckline and below_ma10

    vol1 = float(win['Volume'].iloc[max(0, i1 - 1):i1 + 2].mean())
    vol2 = float(win['Volume'].iloc[max(0, i2 - 1):i2 + 2].mean())

    return {
        'pattern': '쌍봉', 'high1': high1, 'high2': high2, 'neckline': neckline,
        'cur_close': cur_close, 'cur_ma10': cur_ma10, 'broke_down': broke_down,
        'vol_declining': vol2 < vol1, 'lower_high': high2 < high1 * 0.99,
        'months_since_high2': len(win) - 1 - i2,
    }


# ------------------------------------------------------------- H&S (하락)
def find_hns(df: pd.DataFrame) -> dict | None:
    """H&S 패턴 — 원서 p.266~268, p.272 표. (월봉 기준)
    왼어깨(A)-머리(C)-오른어깨(E), C>A and C>E, E<=A(1+tol). 넥라인(B,D 저점) 하향 이탈
    + 10월이평 하향 이탈이 함께 확인돼야 완성으로 인정.
    """
    win = _window(df)
    if win is None:
        return None
    seq = _extrema_sequence(win)
    if len(seq) < 5:
        return None
    last5 = seq[-5:]
    if [k for _, k in last5] != ['P', 'T', 'P', 'T', 'P']:
        return None
    (iA, _), (iB, _), (iC, _), (iD, _), (iE, _) = last5
    if not (_valid_gap(iA, iC) and _valid_gap(iC, iE)):
        return None
    A, B = float(win['High'].iloc[iA]), float(win['Low'].iloc[iB])
    C, D = float(win['High'].iloc[iC]), float(win['Low'].iloc[iD])
    E = float(win['High'].iloc[iE])
    if not (C > A and C > E):
        return None  # 머리가 양 어깨보다 높아야 함
    if E > A * (1 + LEVEL_TOL):
        return None  # 오른쪽 어깨가 전고점보다 유의미하게 높으면 H&S 아님

    neckline = (B + D) / 2
    cur_close = float(win['Close'].iloc[-1])
    cur_ma10 = _cur_ma10(win)
    broke_neckline = cur_close <= neckline * (1 + LEVEL_TOL)
    below_ma10 = cur_ma10 is not None and cur_close < cur_ma10
    broke_down = broke_neckline and below_ma10

    vol_c = float(win['Volume'].iloc[max(0, iC - 1):iC + 2].mean())
    vol_e = float(win['Volume'].iloc[max(0, iE - 1):iE + 2].mean())

    return {
        'pattern': 'H&S', 'left_shoulder': A, 'head': C, 'right_shoulder': E,
        'neckline': neckline, 'cur_close': cur_close, 'cur_ma10': cur_ma10,
        'broke_down': broke_down, 'vol_declining': vol_e < vol_c,
        'months_since_right_shoulder': len(win) - 1 - iE,
    }


# ----------------------------------------------------------- 역H&S (상승)
def find_inverse_hns(df: pd.DataFrame) -> dict | None:
    """역H&S 패턴 — 원서 p.269~272. (월봉 기준) H&S를 거꾸로 뒤집은 형태.
    저점마다 거래량 점차 증가해야 진짜(원문: 거래량 줄면 페이크).
    넥라인 상향 이탈 + 10월이평 상향 돌파가 함께 확인돼야 완성으로 인정.
    """
    win = _window(df)
    if win is None:
        return None
    seq = _extrema_sequence(win)
    if len(seq) < 5:
        return None
    last5 = seq[-5:]
    if [k for _, k in last5] != ['T', 'P', 'T', 'P', 'T']:
        return None
    (iA, _), (iB, _), (iC, _), (iD, _), (iE, _) = last5
    if not (_valid_gap(iA, iC) and _valid_gap(iC, iE)):
        return None
    A, B = float(win['Low'].iloc[iA]), float(win['High'].iloc[iB])
    C, D = float(win['Low'].iloc[iC]), float(win['High'].iloc[iD])
    E = float(win['Low'].iloc[iE])
    if not (C < A and C < E):
        return None  # 머리(C)가 양 어깨보다 낮아야 함
    if E < A * (1 - LEVEL_TOL):
        return None  # 오른쪽 어깨가 전저점보다 유의미하게 낮으면 역H&S 아님(전저점 이탈)

    neckline = (B + D) / 2
    cur_close = float(win['Close'].iloc[-1])
    cur_ma10 = _cur_ma10(win)
    broke_neckline = cur_close >= neckline * (1 - LEVEL_TOL)
    above_ma10 = cur_ma10 is not None and cur_close > cur_ma10
    broke_up = broke_neckline and above_ma10

    vol_c = float(win['Volume'].iloc[max(0, iC - 1):iC + 2].mean())
    vol_e = float(win['Volume'].iloc[max(0, iE - 1):iE + 2].mean())

    return {
        'pattern': '역H&S', 'left_shoulder': A, 'head': C, 'right_shoulder': E,
        'neckline': neckline, 'cur_close': cur_close, 'cur_ma10': cur_ma10,
        'broke_up': broke_up, 'vol_increasing': vol_e > vol_c,
        'months_since_right_shoulder': len(win) - 1 - iE,
    }


# ---------------------------------------------------------- 삼고점 (하락)
def find_samgojeom(df: pd.DataFrame) -> dict | None:
    """삼고점 패턴 — 원서 p.273~275. (월봉 기준) 고점 3회(높이 조금씩 달라도,
    3차가 더 낮아도 무방), 거래량 추이 감소. 지지선(구간 저점) 하향 이탈 + 10월이평
    하향 이탈이 함께 확인돼야 완성 — 쌍봉보다도 강력한 "끝장 신호".
    """
    win = _window(df)
    if win is None:
        return None
    peak_idx = _extrema_idx(win['High'].values, np.greater_equal)
    if len(peak_idx) < 3:
        return None
    i1, i2, i3 = peak_idx[-3], peak_idx[-2], peak_idx[-1]
    if not (_valid_gap(i1, i2) and _valid_gap(i2, i3)):
        return None
    h1, h2, h3 = (float(win['High'].iloc[i1]), float(win['High'].iloc[i2]), float(win['High'].iloc[i3]))
    levels = [h1, h2, h3]
    if (max(levels) - min(levels)) / min(levels) > LEVEL_TOL * 2:
        return None  # 세 고점이 비슷한 저항선 수준이 아님

    support = float(win.iloc[i1:i3 + 1]['Low'].min())
    cur_close = float(win['Close'].iloc[-1])
    cur_ma10 = _cur_ma10(win)
    broke_support = cur_close <= support * (1 + LEVEL_TOL)
    below_ma10 = cur_ma10 is not None and cur_close < cur_ma10
    broke_down = broke_support and below_ma10

    v1 = float(win['Volume'].iloc[max(0, i1 - 1):i1 + 2].mean())
    v2 = float(win['Volume'].iloc[max(0, i2 - 1):i2 + 2].mean())
    v3 = float(win['Volume'].iloc[max(0, i3 - 1):i3 + 2].mean())

    return {
        'pattern': '삼고점', 'peaks': levels, 'support': support,
        'cur_close': cur_close, 'cur_ma10': cur_ma10, 'broke_down': broke_down,
        'vol_declining': v3 < v2 < v1, 'months_since_last_peak': len(win) - 1 - i3,
    }


# --------------------------------------------------------- 삼중바닥 (상승)
def find_samjungbadak(df: pd.DataFrame) -> dict | None:
    """삼중바닥 패턴 — 원서 p.276~278. (월봉 기준) 저점 3회 사수(계단식으로 높아지면
    더 강함), 저점마다 거래량 우상향 + 장대양봉 브레이킹. 저항선 상향 이탈 + 10월이평
    상향 돌파가 함께 확인돼야 완성.
    """
    win = _window(df)
    if win is None:
        return None
    trough_idx = _extrema_idx(win['Low'].values, np.less_equal)
    if len(trough_idx) < 3:
        return None
    i1, i2, i3 = trough_idx[-3], trough_idx[-2], trough_idx[-1]
    if not (_valid_gap(i1, i2) and _valid_gap(i2, i3)):
        return None
    l1, l2, l3 = (float(win['Low'].iloc[i1]), float(win['Low'].iloc[i2]), float(win['Low'].iloc[i3]))
    if l3 < l1 * (1 - LEVEL_TOL):
        return None  # 전저점 사수 실패

    resistance = float(win.iloc[i1:i3 + 1]['High'].max())
    cur_close = float(win['Close'].iloc[-1])
    cur_ma10 = _cur_ma10(win)
    broke_resistance = cur_close >= resistance * (1 - LEVEL_TOL)
    above_ma10 = cur_ma10 is not None and cur_close > cur_ma10
    broke_up = broke_resistance and above_ma10

    v1 = float(win['Volume'].iloc[max(0, i1 - 1):i1 + 2].mean())
    v2 = float(win['Volume'].iloc[max(0, i2 - 1):i2 + 2].mean())
    v3 = float(win['Volume'].iloc[max(0, i3 - 1):i3 + 2].mean())

    return {
        'pattern': '삼중바닥', 'troughs': [l1, l2, l3], 'resistance': resistance,
        'cur_close': cur_close, 'cur_ma10': cur_ma10, 'broke_up': broke_up,
        'vol_increasing': v1 < v2 < v3, 'stairstep': l1 <= l2 <= l3,
        'months_since_last_trough': len(win) - 1 - i3,
    }


# ------------------------------------------------------ 컵위드핸들 (상승)
def find_cup_handle(df: pd.DataFrame) -> dict | None:
    """컵위드핸들(원형바닥) — 원서 p.280~282. (월봉 기준) 완만한 U자형 바닥(Cup) 후
    우측에서 한 번 출렁이는 손잡이(Handle) 구간 형성, 손잡이 자리에서 10월이평 상향
    돌파 시 완성.
    ⚠️ 손잡이 깊이 기준(컵 깊이의 50% 이내)은 원서에 수치가 없어 일반적인 차트 실무
    관행을 근사치로 사용한 것 — 원서 수치가 아님.
    """
    win = _window(df)
    if win is None:
        return None

    left_win = max(4, len(win) // 8)
    left_high_idx = win['High'].iloc[:left_win].idxmax()
    left_high = float(win.loc[left_high_idx, 'High'])
    cup_bottom_idx = win['Low'].idxmin()
    bottom_pos = win.index.get_loc(cup_bottom_idx)
    if bottom_pos < left_win or bottom_pos > len(win) - 4:
        return None  # 바닥이 구간 중앙 부근에 있어야 U자형 성립 (손잡이 형성할 여유 필요)
    cup_bottom = float(win.loc[cup_bottom_idx, 'Low'])

    post_bottom = win.loc[cup_bottom_idx:]
    rim_idx = post_bottom['High'].idxmax()
    rim = float(post_bottom.loc[rim_idx, 'High'])
    if rim < left_high * (1 - LEVEL_TOL * 2):
        return None  # 컵 우측이 좌측 고점(기준선) 근처까지 회복 못함

    after_rim = win.loc[rim_idx:]
    if len(after_rim) < 2:
        return None
    handle_low = float(after_rim['Low'].min())
    cup_depth = left_high - cup_bottom
    handle_depth = rim - handle_low
    if cup_depth <= 0 or handle_depth > cup_depth * 0.5:
        return None  # 손잡이가 컵 깊이의 절반을 넘으면 손잡이로 보기 어려움(실무 관행, 원서 수치 아님)

    cur_close = float(win['Close'].iloc[-1])
    cur_ma10 = _cur_ma10(win)
    breakout = cur_close >= rim * (1 - LEVEL_TOL) and cur_ma10 is not None and cur_close > cur_ma10

    return {
        'pattern': '컵위드핸들', 'left_high': left_high, 'cup_bottom': cup_bottom,
        'rim': rim, 'handle_low': handle_low, 'cur_close': cur_close,
        'cur_ma10': cur_ma10, 'breakout': breakout,
    }


# --------------------------------------------------------------- 스캔 실행
BULLISH_FINDERS = (find_inverse_hns, find_samjungbadak, find_cup_handle)
BEARISH_FINDERS = (find_ssangbong, find_hns, find_samgojeom)


def scan_bullish(universe: list[tuple[str, str]], label: str) -> list[dict]:
    """상승 반전 패턴(역H&S/삼중바닥/컵위드핸들) 신규 매수 후보 — 시장 유니버스 대상."""
    results = []
    total = len(universe)
    for n, (ticker, name) in enumerate(universe, 1):
        print(f'  [{label}] {n:>3}/{total} {ticker:<8}', end='\r')
        try:
            df = fetch_monthly(ticker)
        except Exception:
            continue
        for finder in BULLISH_FINDERS:
            try:
                r = finder(df)
            except Exception:
                continue
            if r and (r.get('broke_up') or r.get('breakout')) and is_uptrend(ticker):
                r.update(ticker=ticker, name=name)
                results.append(r)
    print(' ' * 40, end='\r')
    return results


def scan_bearish_watchlist() -> list[dict]:
    """하락 전환 패턴(쌍봉/H&S/삼고점) 조기경보 — config.json의 관심·보유 종목(stocks) 대상."""
    results = []
    items = list(CFG.get('stocks', {}).items())
    for ticker, meta in items:
        name = meta[0] if isinstance(meta, list) else str(meta)
        try:
            df = fetch_monthly(ticker)
        except Exception:
            continue
        for finder in BEARISH_FINDERS:
            try:
                r = finder(df)
            except Exception:
                continue
            if r and r.get('broke_down'):
                r.update(ticker=ticker, name=name)
                results.append(r)
    return results


def print_bullish_report(results: list[dict], label: str):
    print(f"\n{'=' * 98}\n  상승 반전 패턴 스캐너 — {label}   [{datetime.today().strftime('%Y-%m-%d')}]\n{'=' * 98}")
    if not results:
        print("  조건 만족 종목 없음 (역H&S/삼중바닥/컵위드핸들 + 우상향 추세 전부 통과한 종목 없음)")
        print('=' * 98 + '\n')
        return
    for r in results:
        ma10_str = f"  (10월이평 {r['cur_ma10']:,.2f})" if r.get('cur_ma10') else ""
        print(f"  [{r['pattern']}] {r['ticker']:<8} {r['name']:<20} 현재가 {r['cur_close']:,.2f}{ma10_str}")
    print(f"\n  총 {len(results)}건 — ⚠️ 1차 스크리너 결과입니다. 반드시 차트로 육안 재확인 후 매매 판단하세요.")
    print('=' * 98 + '\n')


def print_bearish_report(results: list[dict]):
    print(f"\n{'=' * 98}\n  하락 전환 패턴 조기경보 — 관심·보유 종목   [{datetime.today().strftime('%Y-%m-%d')}]\n{'=' * 98}")
    if not results:
        print("  경보 없음 (쌍봉/H&S/삼고점 완성 + 10월이평 하향 이탈 조건 통과한 종목 없음)")
        print('=' * 98 + '\n')
        return
    for r in results:
        print(f"  [{r['pattern']}] {r['ticker']:<8} {r['name']:<20} 현재가 {r['cur_close']:,.2f}")
    print(f"\n  총 {len(results)}건 — ⚠️ 이 스크립트는 참고용 1차 경보이며, 실제 매도 판단은 월봉 MA10"
          f" 이탈 원칙(핵심 매매 규칙)을 따르세요.")
    print('=' * 98 + '\n')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['bullish', 'bearish'])
    p.add_argument('universe', choices=['nasdaq100', 'sp500', 'kospi'], nargs='?', default='nasdaq100')
    args = p.parse_args()

    if args.mode == 'bearish':
        print('\n  하락 전환 패턴 조기경보 스캔 중 (관심·보유 종목, 월봉 기준)...')
        results = scan_bearish_watchlist()
        print_bearish_report(results)
    else:
        if args.universe == 'nasdaq100':
            from nasdaq100_scan import get_ndx100_tickers
            universe = [(t, t) for t in get_ndx100_tickers()]
            label = 'NASDAQ100'
        elif args.universe == 'sp500':
            from sp500_scan import get_sp500_tickers
            universe = [(t, t) for t in get_sp500_tickers()]
            label = 'S&P500'
        else:
            from testa_scan import get_universe
            universe = [(f'{code}.KS', name) for code, name in get_universe()]
            label = 'KOSPI(테스타 유니버스)'

        print(f'\n  {label} 상승 반전 패턴 스캔 중 (월봉 기준)... ({len(universe)}종목)')
        results = scan_bullish(universe, label)
        print_bullish_report(results, label)
