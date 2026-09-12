# -*- coding: utf-8 -*-
"""
상승 반전 패턴 스크리너 — 성승현 매매법 2장 원문 확인 + 백테스트 검증 완료된
3개 패턴만 담은 통합 도구 (쌍바닥/삼중바닥/역H&S).

(캔들차트(성승현작가)/성승현_매매법_핵심정리.md § 6, § 백테스트 결론 참고)

## 이 파일이 왜 있는가 (2026-09-12, ssangbadak_scan.py + pattern_scan.py 통합)
원래 이 매매법에 패턴 인식을 보강하면서 스크립트가 두 벌 생겼다:
`ssangbadak_scan.py`(쌍바닥 1개, 일봉 기준)와 `pattern_scan.py`(쌍봉·H&S·역H&S·
삼고점·삼중바닥·컵위드핸들 6개, 최종적으로 월봉 기준). nasdaq100 96종목 +
김학주 관심종목 65종목, 15년치 월봉, 강세장/약세장 구간 교차검증(총
2,000건+ 이벤트)으로 백테스트한 결과:

- **쌍바닥·삼중바닥·역H&S(상승) → 검증됨**: 4가지 컷 전부에서 59~83% 적중률로
  일관되게 하회하지 않음. 이 3개만 이 파일에 담았다.
- 쌍봉·H&S·삼고점(하락)은 4가지 컷 전부에서 기대 방향(하락)이 안 나와 부적합
  판정(적중률 24~44%, 실제 약세장구간에서 오히려 더 나쁨). 매도 신호로 쓸
  근거 없음 — 원서 매매법의 유일한 매도 기준(월봉MA10 하향 이탈, 예외 없음)
  이 왜 옳은지 이 결과가 재확인해준다.
- 컵위드핸들(상승)은 방향은 맞으나 표본 부족(n=18)으로 보류.

실패/보류 판정된 4개 패턴의 코드와 근거는 `archive/캔들차트_성승현/`에 보존됨
(폐기가 아니라 "지금은 신호로 안 쓴다"는 뜻 — 데이터가 쌓이면 재검증 가능).
서로 다른 두 스크립트로 나뉘어 있던 이유가 없어졌으므로, 검증된 3개만
타임프레임·완성조건 기준을 통일해 이 파일 하나로 합쳤다.

## 핵심 규칙 (전부 월봉 기준 — CLAUDE.md "월봉 vs 주봉" 참고)
매수 승인 신호는 전부 **월봉 데이터 + 월봉 10이평(MA10)**으로만 판단한다.
주봉은 이 매매법에서 진입 타이밍을 앞당기는 보조 도구일 뿐, 신규 매수
승인·매도 결정에는 쓰지 않는다.

- 쌍바닥  (원서 p.253~259): 전저점 사수 + 넥라인(중간고점) 상향 이탈 + 월봉MA10 상향 돌파
- 삼중바닥 (원서 p.276~278): 저점 3회 사수(계단식이면 더 강함) + 저항선 이탈 + 월봉MA10 돌파
- 역H&S   (원서 p.269~272): 저점→고점→저점(머리, 최저)→고점→저점 구조, 넥라인 이탈 + 월봉MA10 돌파

이 스크립트는 아직 어떤 GitHub Actions 워크플로우에도 연결되지 않은 독립
실행 도구다. 1차 스크리너일 뿐이므로 결과는 반드시 차트로 육안 재확인할 것.
"""
import sys, json, os, warnings, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)
MA_PERIOD = CFG.get('ma_period', 10)
WEBHOOK = CFG.get('slack_webhook_url_discovery', '')
STOCK_INFO = CFG.get('stocks', {})  # 티커 -> [한글명, 섹터/테마] — 김학주 관심종목 목록과 겹치면 재사용

ORDER_MONTHS    = 2   # 극점(고점/저점) 판정 시 좌우 몇 "개월" 이내 최댓값/최솟값이어야 하는지
LOOKBACK_MONTHS = 60  # 5년 — 원서 실전 사례(카카오 2~3년, S&P500 3년, 호치민 4년 등)에 맞춤
MIN_GAP_MONTHS  = 2   # 두 극점 사이 최소 간격(너무 붙어있으면 같은 파동의 노이즈로 간주)
MAX_GAP_MONTHS  = 48  # 두 극점 사이 최대 간격(4년 이상 벌어지면 별개 사이클로 간주)
LEVEL_TOL = 0.03      # 고점/저점 "비슷한 수준" 판정 허용폭 3%
MIN_REVENUE_GROWTH = 0.10  # 성장성 없는 종목은 제외 — 최소 매출성장률 10%


def fetch_monthly(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period="15y", interval="1mo", auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def _extrema_idx(values, comparator, order=ORDER_MONTHS):
    idx = list(argrelextrema(values, comparator, order=order)[0])
    return [i for n, i in enumerate(idx) if n == 0 or i - idx[n - 1] > order]


def _extrema_sequence(win: pd.DataFrame, order=ORDER_MONTHS):
    """고점(P)·저점(T)을 시간순으로 섞어서 반환. 역H&S 5점 패턴 탐지에 사용."""
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


# --------------------------------------------------------------- 쌍바닥
def find_ssangbadak(df: pd.DataFrame) -> dict | None:
    """쌍바닥 패턴 — 원서 p.253~259. (월봉 기준, 2026-09-12 백테스트 검증:
    nasdaq100 63~73% / 김학주종목 60~71% 적중)
    전제조건: 2차 저점이 전저점을 유의미하게 못 깸. 완성: 넥라인(중간 고점)
    상향 이탈 + 월봉MA10 상향 돌파가 함께 확인돼야 함.
    """
    win = _window(df)
    if win is None:
        return None
    trough_idx = _extrema_idx(win['Low'].values, np.less_equal)
    if len(trough_idx) < 2:
        return None
    i1, i2 = trough_idx[-2], trough_idx[-1]
    if not _valid_gap(i1, i2):
        return None

    low1, low2 = float(win['Low'].iloc[i1]), float(win['Low'].iloc[i2])
    if low2 < low1 * (1 - LEVEL_TOL):
        return None  # 전저점을 유의미하게 깼음 → 쌍바닥 아님(원서: 전저점 사수 필수)

    neckline = float(win.iloc[i1:i2 + 1]['High'].max())
    cur_close = float(win['Close'].iloc[-1])
    cur_ma10 = _cur_ma10(win)
    broke_neckline = cur_close >= neckline * (1 - LEVEL_TOL)
    above_ma10 = cur_ma10 is not None and cur_close > cur_ma10
    broke_up = broke_neckline and above_ma10

    return {
        'pattern': '쌍바닥', 'low1': low1, 'low2': low2, 'neckline': neckline,
        'cur_close': cur_close, 'cur_ma10': cur_ma10, 'broke_up': broke_up,
        'strong': low2 > low1 * 1.01,  # 짝궁둥이형(오른쪽 저점이 더 높음, 원서: 더 강력)
        'months_since_low2': len(win) - 1 - i2,
    }


# --------------------------------------------------------------- 삼중바닥
def find_samjungbadak(df: pd.DataFrame) -> dict | None:
    """삼중바닥 패턴 — 원서 p.276~278. (월봉 기준, 2026-09-12 백테스트 검증:
    nasdaq100 59~72% / 김학주종목 59~74% 적중)
    저점 3회 사수(계단식으로 높아지면 더 강함), 저항선 상향 이탈 + 월봉MA10
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


# --------------------------------------------------------------- 역H&S
def find_inverse_hns(df: pd.DataFrame) -> dict | None:
    """역H&S 패턴 — 원서 p.269~272. (월봉 기준, 2026-09-12 백테스트 검증:
    nasdaq100 71~74% / 김학주종목 61~83% 적중)
    H&S를 거꾸로 뒤집은 형태. 저점마다 거래량 점차 증가해야 진짜(원문:
    거래량 줄면 페이크). 넥라인 상향 이탈 + 월봉MA10 상향 돌파가 함께
    확인돼야 완성.
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
        ma_now = float(dm['MA10'].iloc[-1])
        ma_3m_ago = float(dm['MA10'].iloc[-4])
        price_now = float(dm['Close'].iloc[-1])
        price_6m_ago = float(dm['Close'].iloc[-7])
        return ma_now > ma_3m_ago and price_now > price_6m_ago
    except Exception:
        return False


def get_revenue_growth(ticker: str) -> float | None:
    """전년동기대비 매출성장률. 데이터 없으면 None (필터에서 제외 대상)."""
    try:
        info = yf.Ticker(ticker).info
        g = info.get('revenueGrowth')
        return float(g) if g is not None else None
    except Exception:
        return None


def get_earnings_growth(ticker: str) -> float | None:
    """전년동기대비 이익성장률(yfinance 'earningsGrowth') — 매출은 늘어도 이익이
    안 늘거나 적자가 커지는 종목을 걸러내기 위한 참고 지표. 데이터 없으면 None
    (표시만 안 됨, 필터에서 제외하지는 않음 — 매출성장률 필터와 달리 하드 컷 아님).
    """
    try:
        info = yf.Ticker(ticker).info
        g = info.get('earningsGrowth')
        return float(g) if g is not None else None
    except Exception:
        return None


BULLISH_FINDERS = (find_ssangbadak, find_samjungbadak, find_inverse_hns)


def _consolidate(results: list[dict]) -> list[dict]:
    """같은 종목이 여러 패턴에 동시에 걸리면 한 줄로 합친다 (예: 쌍바닥+삼중바닥)."""
    merged: dict[str, dict] = {}
    for r in results:
        t = r['ticker']
        if t not in merged:
            merged[t] = {**r, 'patterns': [r['pattern']]}
        elif r['pattern'] not in merged[t]['patterns']:
            merged[t]['patterns'].append(r['pattern'])
    for r in merged.values():
        r['pattern_label'] = '+'.join(r['patterns'])
    return list(merged.values())


def scan_bullish(universe: list[tuple[str, str]], label: str) -> list[dict]:
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
            if r and r.get('broke_up') and is_uptrend(ticker):
                r.update(ticker=ticker, name=name)
                results.append(r)
    print(' ' * 40, end='\r')

    consolidated = _consolidate(results)
    print(f'  기술적 필터 통과 {len(consolidated)}종목 — 성장성 확인 중...')
    survivors = []
    for r in consolidated:
        g = get_revenue_growth(r['ticker'])
        r['revenue_growth'] = g
        r['earnings_growth'] = get_earnings_growth(r['ticker'])  # 참고용 표시만, 필터 아님
        if g is not None and g >= MIN_REVENUE_GROWTH:
            survivors.append(r)
    return survivors


def print_report(results: list[dict], label: str):
    print(f"\n{'=' * 100}\n  상승 반전 패턴 스캐너(쌍바닥/삼중바닥/역H&S) — {label}   [{datetime.today().strftime('%Y-%m-%d')}]\n{'=' * 100}")
    if not results:
        print(f"  조건 만족 종목 없음 (패턴 완성 + 우상향 추세 + 매출성장률 {MIN_REVENUE_GROWTH*100:.0f}%+ 전부 통과한 종목 없음)")
        print('=' * 100 + '\n')
        return
    for r in results:
        g = r['revenue_growth']
        g_str = f"{g*100:+.0f}%" if g is not None else "N/A"
        e = r.get('earnings_growth')
        e_str = f"{e*100:+.0f}%" if e is not None else "N/A"
        ma10_str = f"(10월이평 {r['cur_ma10']:,.2f})" if r.get('cur_ma10') else ""
        sector = STOCK_INFO.get(r['ticker'], [None, None])[1]
        sector_str = f" [{sector}]" if sector else ""
        print(f"  [{r['pattern_label']}] {r['ticker']:<8} {r['name']:<20}{sector_str} 현재가 {r['cur_close']:,.2f} {ma10_str}  매출성장률 {g_str}  이익성장률 {e_str}")
    print(f"\n  총 {len(results)}건 — ⚠️ 1차 스크리너 결과입니다. 반드시 차트로 육안 재확인 후 매매 판단하세요.")
    print(f"  ⚠️ 매출성장률은 매출 규모가 작은 회사일수록 왜곡(과장)될 수 있음 — 절대수치도 같이 확인할 것")
    print(f"  ⚠️ 이익성장률은 참고용 표시일 뿐 필터링에는 안 씀 — 매출은 늘어도 적자면 여기서 걸러내세요")
    print('=' * 100 + '\n')


def send_slack(results: list[dict], label: str):
    """discovery 채널로 전송 (2026-09-12 기존 웹훅 재사용, 신규 웹훅 발급 안 함)."""
    if not WEBHOOK:
        print('  WEBHOOK 없음 — Slack 전송 생략')
        return
    today = datetime.today().strftime('%Y.%m.%d')
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"📐 상승 반전 패턴 스캔(쌍바닥/삼중바닥/역H&S) — {label}  {today}"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": "성승현 매매법 2장 패턴, 15년치 월봉 백테스트로 검증된 3개만 포함"
                 "(nasdaq100/김학주종목/강세장/약세장 4가지 컷 모두 59~83% 적중)."
                 " ⚠️ 매수확정 신호 아님 — 차트 육안 재확인 후 판단."}]},
        {"type": "divider"},
    ]
    if not results:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                       "text": "조건 만족 종목 없음."}})
    else:
        for r in results[:30]:  # 슬랙 블록 50개 하드리밋 감안 여유있게 상위 30건만
            g = r['revenue_growth']
            g_str = f"{g*100:+.0f}%" if g is not None else "N/A"
            e = r.get('earnings_growth')
            e_str = f"{e*100:+.0f}%" if e is not None else "N/A"
            sector = STOCK_INFO.get(r['ticker'], [None, None])[1]
            sector_str = f"  _{sector}_" if sector else ""
            blocks.append({"type": "section", "text": {"type": "mrkdwn",
                           "text": f"*[{r['pattern_label']}]* `{r['ticker']}` {r['name']}{sector_str}  "
                                   f"현재가 {r['cur_close']:,.2f}  매출성장률 {g_str}  이익성장률 {e_str}"}})
        if len(results) > 30:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                           "text": f"_외 {len(results)-30}건 생략 — 워크플로우 로그 참고_"}]})

    payload = json.dumps({'text': f'상승 반전 패턴 스캔 {today}', 'blocks': blocks},
                          ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print(f'Slack 전송: {"성공" if res.read().decode()=="ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('universe', choices=['nasdaq100', 'sp500', 'kospi'], nargs='?', default='nasdaq100')
    args = p.parse_args()

    def _name(t):
        # config.json의 stocks(김학주 관심종목)에 한글명이 있으면 그걸 쓰고, 없으면 티커 그대로
        return STOCK_INFO[t][0] if t in STOCK_INFO else t

    if args.universe == 'nasdaq100':
        from nasdaq100_scan import get_ndx100_tickers
        universe = [(t, _name(t)) for t in get_ndx100_tickers()]
        label = 'NASDAQ100'
    elif args.universe == 'sp500':
        from sp500_scan import get_sp500_tickers
        universe = [(t, _name(t)) for t in get_sp500_tickers()]
        label = 'S&P500'
    else:
        from testa_scan import get_universe
        universe = [(f'{code}.KS', name) for code, name in get_universe()]
        label = 'KOSPI(테스타 유니버스)'

    print(f'\n  {label} 상승 반전 패턴 스캔 중 (월봉 기준)... ({len(universe)}종목)')
    results = scan_bullish(universe, label)
    print_report(results, label)
    send_slack(results, label)
