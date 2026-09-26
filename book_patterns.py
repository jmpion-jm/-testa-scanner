# -*- coding: utf-8 -*-
"""
성승현 『캔들차트 하나로 끝내는 추세추종 투자』 2장 패턴 — 원서 규칙 구현.

⚠️ 이 파일의 규칙은 `캔들차트(성승현작가)/패턴_구현명세.md`에 쪽수 근거와 함께 고정돼 있다.
   명세서와 tests/test_book_patterns.py(원서 예시 차트 재현 테스트)를 먼저 바꾸지 않고
   이 파일의 판정 규칙을 바꾸지 말 것 — 사용자 승인 없이 해석을 바꾸면 안 됨(2026-09-26 사용자 요청).

타임프레임 공통(월봉·주봉·일봉). 사용자 시스템은 월봉을 쓴다.

[핵심 판정 = 원서 본문 + 원서 예시 차트가 모두 만족하는 조건]
- 상승 패턴 완성 = 후킹 캔들: 직전 봉 종가 ≤ 10이평, 이번 봉 양봉, 시가 ≤ 10이평 < 종가
  (p.256 "10이평선을 강력하게 뚫고 올리는 장대양봉", p.270, p.272 표)
- 하락 패턴 완성: 직전 봉 종가 ≥ 10이평, 이번 봉 음봉, 시가 ≥ 10이평 > 종가
  (p.261 "10이평선을 뚫는 음봉", p.267~268 "단 10원이라도 뚫리는 음봉", p.273)
- 바닥 높이 비교는 저가 기준 (p.254 "왼 바닥보다 오른 바닥 높이가 결코 낮아서는 안 된다",
  원서 나스닥 월봉 예시 p.258이 저가 기준으로만 성립)

[참고 등급(quality) = 원서에 있지만 원서 월봉·주봉 예시가 스스로 충족하지 않는 조건]
원서 예시가 통과하지 못하는 조건을 필수로 걸면 원서 예시가 탈락하므로, 판정에는 쓰지 않고
충족 여부를 결과에 표시한다(명세서 §3 근거 표 참고).
"""
import numpy as np
import pandas as pd

MA_PERIOD = 10
AVG_WIN = 10          # 몸통·거래량 "평균" 비교 구간(직전 10봉). 원서에 구간 수치 없음 — MA10과 같은 호흡(구현값)
VOL_BURST_MULT = 3.0  # 원서 5장 p.364: "거래량 최저점 후 평균 거래량의 3배 이상 증가는 상승 추세 반전 신호"
VOL_2ND = (0.7, 2.0)  # 원서 p.255: 2차 브레이킹 거래량은 첫 번째 캔들의 70~200%
BIG_BODY_PCT = 0.05   # 장대봉 = 몸통 길이가 전일(직전 봉) 대비 5~7% 이상 — 원서 p.216. 하한 5% 사용
PULLBACK_VOL_IDEAL = 1 / 7   # 눌림목 거래량 ≤ 상승구간 최대 거래량의 1/7~1/20이면 금상첨화 — 원서 p.364
BREAK_SEARCH = 2      # 저점 봉 포함 이후 몇 봉 안의 첫 양봉을 브레이킹 캔들로 보나 — 구현값
LOOKBACK = 24         # 패턴 탐색 구간(봉). 원서 월봉·주봉 예시는 모두 이 안에 들어옴 — 구현값
MAX_OPPOSITE = 2      # 패턴 구간 안에서 반대편(바닥 패턴이면 10이평 위) 종가 허용 봉수. 원서 S&P500 월봉 역H&S(p.271)는
                      # 머리→넥라인 구간 2022-11에 1봉 일시 돌파가 있음. 이보다 많으면 그 앞은 별개 추세로 보고 구간에서 뺀다 — 구현값
EQ_TOL = 0.01         # "같은 높이" 허용폭 1%. 원서는 눈으로 판정 — 원서 예시를 재현하는 최소값:
                      # p.271 S&P500 2022 H&S 오른어깨가 왼어깨보다 +0.6%, p.298 나스닥 주봉 2022-12 두번째 고점 +0.2%


# ------------------------------------------------------------------ 준비
def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV(시간 오름차순) → MA10·몸통·평균 컬럼 추가. auto_adjust 여부와 무관하게 동작."""
    d = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    d['MA'] = d['Close'].rolling(MA_PERIOD).mean()
    d['body'] = (d['Close'] - d['Open']) / d['Open']
    d['abs_body'] = d['body'].abs()
    d['avg_body'] = d['abs_body'].rolling(AVG_WIN).mean().shift(1)
    d['avg_vol'] = d['Volume'].rolling(AVG_WIN).mean().shift(1)
    d['above'] = d['Close'] > d['MA']
    return d


def _ok(d, i):
    return 0 < i < len(d) and not pd.isna(d['MA'].iat[i]) and not pd.isna(d['MA'].iat[i - 1])


def is_hook(d: pd.DataFrame, t: int) -> bool:
    """후킹 캔들: 10이평을 아래에서 위로 뚫는 양봉(몸통이 10이평을 관통)."""
    if not _ok(d, t):
        return False
    o, c, ma = d['Open'].iat[t], d['Close'].iat[t], d['MA'].iat[t]
    return d['Close'].iat[t - 1] <= d['MA'].iat[t - 1] and c > o and o <= ma < c


def is_breakdown(d: pd.DataFrame, j: int) -> bool:
    """하락 완성 캔들: 10이평을 위에서 아래로 뚫는 음봉(몸통이 10이평을 관통)."""
    if not _ok(d, j):
        return False
    o, c, ma = d['Open'].iat[j], d['Close'].iat[j], d['MA'].iat[j]
    return d['Close'].iat[j - 1] >= d['MA'].iat[j - 1] and c < o and o >= ma > c


def _run_start(d, i, bull=True):
    """i에서 끝나는 같은 색 연속 캔들의 시작 봉. 원서 p.217·249 "작은 양봉이 연속되면 합쳐서 장대양봉(은둔형)",
    p.265 카카오 "음봉 세 개를 합치면 저승사자 캔들"."""
    if bull:
        same = lambda k: d['Close'].iat[k] > d['Open'].iat[k]
    else:
        same = lambda k: d['Close'].iat[k] < d['Open'].iat[k]
    if not same(i):
        return None
    k = i
    while k - 1 >= 0 and same(k - 1):
        k -= 1
    return k


def run_body_pct(d, i, bull=True):
    """i에서 끝나는 연속 양봉(음봉)을 한 캔들로 합친 몸통 크기 ÷ 그 직전 봉 종가. 합칠 수 없으면 0."""
    k = _run_start(d, i, bull)
    if k is None or k == 0:
        return 0.0
    base = d['Close'].iat[k - 1]
    return float(abs(d['Close'].iat[i] - d['Open'].iat[k]) / base) if base else 0.0


def is_big_bull(d, i):
    """장대양봉(원서 p.216 몸통 ≥ 전일 대비 5%, 연속 양봉 합산 p.249)."""
    return run_body_pct(d, i, bull=True) >= BIG_BODY_PCT


def is_big_bear(d, i):
    """장대음봉(원서 p.216 기준의 거울, 연속 음봉 합산 p.265)."""
    return run_body_pct(d, i, bull=False) >= BIG_BODY_PCT


def _big_bull(d, i):
    return is_big_bull(d, i)


def _vol_ratio(d, i):
    av = d['avg_vol'].iat[i]
    return None if pd.isna(av) or av <= 0 else float(d['Volume'].iat[i] / av)


def _breaking(d, i, limit):
    """저점 봉 i 이후(포함) BREAK_SEARCH봉 안의 첫 양봉 = 브레이킹 캔들 (limit 이하)."""
    for k in range(i, min(i + BREAK_SEARCH, limit) + 1):
        if d['Close'].iat[k] > d['Open'].iat[k]:
            return k
    return None


# ------------------------------------------------------------------ 극점
def _pivots(d, lo, hi, kind, completion=None):
    """[lo, hi] 안의 바닥('T') 또는 천장('P') 봉 목록.

    바닥 = 저가가 양옆보다 낮거나, 종가가 양옆보다 낮은 봉(둘 중 하나). 월봉에서는 되돌림이 종가로만
    보이는 경우가 있다 — 원서 p.271 S&P500 월봉 역H&S의 오른쪽 어깨(2022-12)는 저가 극소가 아니라
    종가 극소다. 반대로 p.258 나스닥 월봉 쌍바닥의 두 바닥(2022-10, 2022-12)은 저가로만 성립한다.
    바로 붙은 두 봉이 모두 걸리면 같은 바닥(한 파동)이므로 저가가 더 낮은 쪽 하나만 남긴다.
    천장은 그 거울(고가/종가 극대, 고가 높은 쪽).
    바닥은 10이평 아래(종가 ≤ MA), 천장은 10이평 위(종가 ≥ MA)에서 형성된 것만 인정한다.

    completion(후킹/이탈 봉)은 오른쪽 이웃이 없어도 직전 봉보다 극단이면 마지막 극점으로 인정한다 —
    원서 p.298 나스닥 주봉: 2022-12-12 장대음봉이 한 봉 안에서 두 번째 고점을 찍고 10이평을 뚫었다.
    """
    lvl = 'Low' if kind == 'T' else 'High'
    sgn = 1 if kind == 'T' else -1          # 바닥은 작을수록, 천장은 클수록 극점
    cand = []

    def add(i):
        if cand and i - cand[-1] == 1:
            if sgn * d[lvl].iat[i] < sgn * d[lvl].iat[cand[-1]]:
                cand[-1] = i
        else:
            cand.append(i)

    for i in range(max(lo, 1), hi + 1):
        if i + 1 >= len(d):
            break
        side_ok = d['Close'].iat[i] <= d['MA'].iat[i] if kind == 'T' else d['Close'].iat[i] >= d['MA'].iat[i]
        if not side_ok:
            continue
        for col in (lvl, 'Close'):
            v, p, n = sgn * d[col].iat[i], sgn * d[col].iat[i - 1], sgn * d[col].iat[i + 1]
            if v <= p and v < n:
                add(i)
                break
    if completion is not None and cand and sgn * d[lvl].iat[completion] <= sgn * d[lvl].iat[completion - 1]:
        # 완성 봉은 기존 극점들 중 가장 극단인 높이와 "같은 높이"(EQ_TOL)까지 되돌아왔을 때만 재시험 극점으로 인정.
        # p.298 나스닥 주봉 2022-12-12(첫 천장 높이 재시험) ○ / p.290 다우 주봉 2016-02-24(바닥보다 4.6% 위) ×
        ext = min(sgn * d[lvl].iat[i] for i in cand)
        v = sgn * d[lvl].iat[completion]
        if v <= ext + abs(ext) * EQ_TOL:
            add(completion)
    return cand


def _window_start(d, end, kind):
    """패턴 탐색 시작점: end 직전부터 거슬러 올라가며 반대편 종가가 MAX_OPPOSITE봉을 넘기 직전까지(최대 LOOKBACK)."""
    opp = 0
    start = max(1, end - LOOKBACK)
    for k in range(end - 1, start - 1, -1):
        above = d['Close'].iat[k] > d['MA'].iat[k]
        if (kind == 'T' and above) or (kind == 'P' and not above):
            opp += 1
            if opp > MAX_OPPOSITE:
                return k + 1
    return start


def _troughs(d, lo, hi):
    return _pivots(d, lo, hi, 'T')


def _peaks(d, lo, hi):
    return _pivots(d, lo, hi, 'P')


# ------------------------------------------------------------------ 공통 구조 판정
def _structure(d, end, kind, completed):
    """end(후킹/이탈 봉, 또는 형성중 판정 시 len(d)) 직전의 바닥('T')/천장('P') 구조.

    반환: (이름, 극점목록) 또는 None
      바닥: 삼중바닥(p.276) / 역H&S(p.269) / 쌍바닥(p.253)
      천장: 삼고점(p.273)   / H&S(p.266)   / 쌍봉(p.260)
    """
    lo = _window_start(d, end, kind)
    comp = end if completed else None
    pv = _pivots(d, lo, end - 1, kind, completion=comp)
    if len(pv) < 2:
        return None
    last = pv[-1]
    # 마지막 극점 이후 완성 전에 이미 반대편으로 넘어갔으면, end는 이 구조의 완성이 아님
    seg = d['above'].iloc[last + 1:end]
    if (kind == 'T' and seg.any()) or (kind == 'P' and (~seg).any()):
        return None
    lvl = 'Low' if kind == 'T' else 'High'
    vals = [d[lvl].iat[i] for i in pv]
    if kind == 'T':
        ext = min(vals)
        m = next(i for i in pv if d[lvl].iat[i] <= ext * (1 + EQ_TOL))   # 가장 낮은 바닥과 같은 높이(1%)인 첫 바닥
    else:
        ext = max(vals)
        m = next(i for i in pv if d[lvl].iat[i] >= ext * (1 - EQ_TOL))   # 가장 높은 천장과 같은 높이(1%)인 첫 천장
    if m == last:
        return None  # 두 번째 바닥/천장이 없음(V자·역V자) — p.254 "V자 반등은 거의 발생하지 않는다"
    post = [i for i in pv if i > m]
    pre = [i for i in pv if i < m]
    if len(post) >= 2:
        return ('삼중바닥' if kind == 'T' else '삼고점'), [m, post[-2], post[-1]]
    e = post[0]
    if pre:
        a = pre[-1]
        if kind == 'T' and d[lvl].iat[e] >= d[lvl].iat[a] * (1 - EQ_TOL):
            return '역H&S', [a, m, e]   # 오른어깨 ≥ 왼어깨 (H&S p.267 "첫 고점 A보다 같거나 낮으며"의 거울)
        if kind == 'P' and d[lvl].iat[e] <= d[lvl].iat[a] * (1 + EQ_TOL):
            return 'H&S', [a, m, e]     # p.267 오른어깨 ≤ 왼어깨
    return ('쌍바닥' if kind == 'T' else '쌍봉'), [m, e]


# ------------------------------------------------------------------ 상승 패턴(후킹 시점 t에서 판정)
def bullish_at(d: pd.DataFrame, t: int) -> dict | None:
    """t가 후킹 캔들이면, 그 직전 바닥 구조로 쌍바닥/역H&S/삼중바닥 판정. 아니면 None."""
    if not is_hook(d, t):
        return None
    r = _structure(d, t, 'T', completed=True)
    if r is None:
        return None
    name, bottoms = r
    brk = [_breaking(d, b, t) for b in bottoms]
    q = _quality(d, name, bottoms, brk, t)
    return {
        'pattern': name, 'hook': t, 'bottoms': bottoms, 'breaking': brk,
        'bottom_lows': [float(d['Low'].iat[b]) for b in bottoms],
        'stairstep': all(d['Low'].iat[a] <= d['Low'].iat[b] for a, b in zip(bottoms, bottoms[1:])),
        'quality': q,
    }


def _quality(d, name, bottoms, brk, t):
    """원서의 추가 조건 충족 여부 — 판정이 아니라 표시용(명세서 §3)."""
    q = {}
    b1 = brk[0]
    if name == '쌍바닥':
        vr1 = _vol_ratio(d, b1) if b1 is not None else None
        q['1차브레이킹_장대양봉(p.254)'] = bool(b1 is not None and _big_bull(d, b1))
        q['1차브레이킹_거래량3배(p.254,p.364)'] = bool(vr1 is not None and vr1 >= VOL_BURST_MULT)
        b2 = brk[1]
        if b1 is not None and b2 is not None and d['Volume'].iat[b1] > 0:
            r = d['Volume'].iat[b2] / d['Volume'].iat[b1]
            q['2차브레이킹_거래량70~200%(p.255)'] = bool(VOL_2ND[0] <= r <= VOL_2ND[1])
        else:
            q['2차브레이킹_거래량70~200%(p.255)'] = False
        # p.255 "전저점은 1차 브레이킹 캔들의 꼬리가 아니라 시가 기준" — 두 번째 바닥 저가가 그 시가 이상인가
        q['전저점_시가기준_사수(p.255)'] = bool(b1 is not None and d['Low'].iat[bottoms[1]] >= d['Open'].iat[b1])
    else:
        vols = [d['Volume'].iat[b] if b is not None else np.nan for b in brk]
        key = '저점마다_거래량증가(p.269~270)' if name == '역H&S' else '거래량_우상향(p.276)'
        q[key] = bool(all(not np.isnan(v) for v in vols) and all(a < b for a, b in zip(vols, vols[1:])))
        q['브레이킹_장대양봉_모두(p.269,p.276)'] = bool(all(b is not None and _big_bull(d, b) for b in brk))
    q['후킹_장대양봉(p.256)'] = _big_bull(d, t)
    vr = _vol_ratio(d, t)
    q['후킹_거래량_평균이상(5장 p.365)'] = bool(vr is not None and vr >= 1.0)
    return q


# ------------------------------------------------------------------ 하락 패턴(이탈 시점 j에서 판정)
def bearish_at(d: pd.DataFrame, j: int) -> dict | None:
    """j가 하락 완성 캔들이면, 그 직전 천장 구조로 쌍봉/H&S/삼고점 판정. 아니면 None."""
    if not is_breakdown(d, j):
        return None
    return _top_structure(d, j, completed=True)


def _top_structure(d, j, completed):
    r = _structure(d, j, 'P', completed=completed)
    if r is None:
        return None
    name, tops = r
    vols = [d['Volume'].iat[i] for i in tops]
    q = {'두번째고점_거래량감소(p.261,p.266)': bool(vols[-1] < vols[0])}
    if completed:
        q['이탈_장대음봉=저승사자(p.262,p.264)'] = is_big_bear(d, j)
    return {'pattern': name, 'break': j if completed else None, 'tops': tops,
            'top_highs': [float(d['High'].iat[i]) for i in tops], 'quality': q}


def bearish_forming(d: pd.DataFrame) -> dict | None:
    """보유종목 경고용: 아직 10이평 위지만 천장 구조(쌍봉/H&S/삼고점)가 이미 만들어졌는가.
    p.263 "쌍봉 패턴은 형태가 보일 때부터 무조건 경계해야 하는 하락 패턴" — 매도는 10이평 이탈 때(기존 규칙)."""
    n = len(d) - 1
    if n < 2 or not d['above'].iat[n]:
        return None
    r = _top_structure(d, n + 1, completed=False)
    if r is None or d['Close'].iat[n] >= r['top_highs'][-1]:
        return None  # 마지막 천장 이후 아직 내려오지 않았으면(신고가 진행 중) 형성 중이 아님
    return r


# ------------------------------------------------------------------ 복합·되돌림 패턴(후킹 시점 t에서 판정)
_FAMILY = {'쌍바닥': '쌍', '쌍봉': '쌍', '역H&S': 'HS', 'H&S': 'HS', '삼중바닥': '삼', '삼고점': '삼'}


def _prev_breakdown(d, before):
    """before 이전의 가장 최근 10이평 하향 이탈 봉(패턴 여부 무관)."""
    for k in range(before - 1, 0, -1):
        if d['Close'].iat[k] < d['MA'].iat[k] and d['Close'].iat[k - 1] >= d['MA'].iat[k - 1]:
            return k
    return None


def _prev_hook(d, before):
    for k in range(before - 1, 0, -1):
        if is_hook(d, k):
            return k
    return None


def composite_at(d: pd.DataFrame, t: int) -> list[dict]:
    """후킹 t에 걸리는 되돌림1·2·3, 겹쌍바닥, 대쌍바닥 판정(p.283~301). 여러 개가 동시에 성립할 수 있다."""
    if not is_hook(d, t):
        return []
    out = []
    bull = bullish_at(d, t)

    # 되돌림3 (p.297~299): 하락 패턴 완성(이탈 j) 후, 새 바닥 구조 없이 장대양봉 하나가 j의 하락을 전부 상쇄
    j = _prev_breakdown(d, t)
    if j is not None and not d['above'].iloc[j:t].any():
        bear_j = bearish_at(d, j)
        if bear_j and bull is None and d['Close'].iat[t] >= d['Open'].iat[j]:
            out.append({'pattern': '되돌림3', 'undone': bear_j['pattern'], 'break': j, 'hook': t})

    if bull is None:
        return out

    first_bottom = bull['bottoms'][0]
    j = _prev_breakdown(d, first_bottom + 1)
    bear_j = bearish_at(d, j) if j is not None else None

    # 되돌림1·2 (p.294~296): 하락 패턴을 상승 패턴으로 되돌림(같은 계열=1, 다른 계열=2).
    # "곧바로"(p.292)를 기간·폭 수치로 제한하지 않는다 — 원서 p.295 HLB글로벌 예시는 쌍바닥 후킹 뒤 6주간 +245% 오른
    # 다음의 쌍봉을 되돌림으로 본다. 직전 반대 패턴을 되돌렸는가만 본다.
    if bear_j:
        kind = '되돌림1' if _FAMILY[bear_j['pattern']] == _FAMILY[bull['pattern']] else '되돌림2'
        out.append({'pattern': kind, 'undone': bear_j['pattern'], 'by': bull['pattern'], 'break': j, 'hook': t})

    # 겹쌍바닥(p.286~287)·대쌍바닥(p.289~291): 앞선 상승 패턴(후킹 t0) → 다시 밀림(이탈 j) →
    # 전저점을 깨지 않고 다시 상승 패턴(후킹 t).
    #   대쌍바닥 = 그 사이에 하락 패턴(천장 구조)이 완성됨 — "쌍바닥을 쌍봉으로 되돌렸는데 그걸 다시 쌍바닥으로"
    #             (p.290 그림 제목 "되돌림 대쌍바닥 패턴"). 천장 구조가 쌍봉이 아니라 삼고점/H&S로 잡혀도 같은 되돌림으로 본다
    #             — 원서 p.290 다우 주봉 예시의 천장이 코드 판정상 삼고점(2015-10-28, 12-02, 12-23)이다.
    #   겹쌍바닥 = 사이에 하락 패턴 없이 되밀린 경우.
    #   앞선 상승 패턴 t0 = 천장 구조의 첫 천장(없으면 이탈 j) 이전의 가장 최근 "패턴이 있는" 후킹. 그 사이의 잠깐 돌파
    #   (p.290 다우 2015-12-23 같은 패턴 없는 후킹)는 건너뛴다.
    if j is not None:
        anchor = bear_j['tops'][0] if bear_j else j
        t0 = None
        for k in range(anchor - 1, max(0, anchor - LOOKBACK) - 1, -1):
            if is_hook(d, k) and bullish_at(d, k):
                t0 = k
                break
        prev = bullish_at(d, t0) if t0 is not None else None
        if prev and min(bull['bottom_lows']) >= min(prev['bottom_lows']) * (1 - EQ_TOL):
            kind = '대쌍바닥' if bear_j else '겹쌍바닥'
            out.append({'pattern': kind, 'first_hook': t0, 'first': prev['pattern'], 'break': j, 'hook': t,
                        'inner_top': bear_j['pattern'] if bear_j else None})
    return out


def bear_composite_at(d: pd.DataFrame, j: int) -> list[dict]:
    """이탈 j에 걸리는 하락 쪽 복합 패턴 — composite_at의 거울. 매도 자체는 10이평 이탈 규칙 그대로이고,
    이 판정은 하락 강도 경고용이다(p.285 겹쌍봉 "수년간 지리멸렬", p.289 대쌍봉 "2년은 주가가 맥을 못춘다").
      되돌림3(하락): 상승 패턴 완성 후 새 천장 구조 없이 장대음봉 하나가 상승을 전부 상쇄(p.297 정의의 거울)
      되돌림1·2(하락): 상승 패턴을 하락 패턴으로 되돌림 — p.294 "쌍바닥을 쌍봉 패턴으로 잡는 것", p.295 HLB글로벌 예시
      겹쌍봉(p.284~285): 하락 패턴 완성 → 반등 → 이전보다 높지 않은 천장에서 다시 하락 패턴
      대쌍봉(p.287~289): 그 반등이 상승 패턴(작은 쌍바닥)이었던 경우 — "쌍봉 안에 작은 쌍바닥을 품고 있는 것"
    """
    if not is_breakdown(d, j):
        return []
    out = []
    bear = bearish_at(d, j)

    h = _prev_hook(d, j)
    if h is not None and d['above'].iloc[h:j].all():
        bull_h = bullish_at(d, h)
        if bull_h and bear is None and d['Close'].iat[j] <= d['Open'].iat[h]:
            out.append({'pattern': '되돌림3(하락)', 'undone': bull_h['pattern'], 'hook': h, 'break': j})

    if bear is None:
        return out

    h = _prev_hook(d, bear['tops'][0] + 1)
    bull_h = bullish_at(d, h) if h is not None else None
    if bull_h:  # 기간·폭 제한 없음 — 위 되돌림1·2 주석과 같은 이유(p.295 HLB글로벌 예시)
        kind = '되돌림1(하락)' if _FAMILY[bull_h['pattern']] == _FAMILY[bear['pattern']] else '되돌림2(하락)'
        out.append({'pattern': kind, 'undone': bull_h['pattern'], 'by': bear['pattern'], 'hook': h, 'break': j})

    if h is not None:
        anchor = bull_h['bottoms'][0] if bull_h else h
        j0 = None
        for k in range(anchor - 1, max(0, anchor - LOOKBACK) - 1, -1):
            if is_breakdown(d, k) and bearish_at(d, k):
                j0 = k
                break
        prev = bearish_at(d, j0) if j0 is not None else None
        if prev and max(bear['top_highs']) <= max(prev['top_highs']) * (1 + EQ_TOL):
            kind = '대쌍봉' if bull_h else '겹쌍봉'
            out.append({'pattern': kind, 'first_break': j0, 'first': prev['pattern'], 'hook': h, 'break': j,
                        'inner_bottom': bull_h['pattern'] if bull_h else None})
    return out


# ------------------------------------------------------------------ 편의 함수
def scan_events(df: pd.DataFrame) -> list[dict]:
    """전체 히스토리에서 모든 상승 패턴·복합 패턴 이벤트(후킹 시점 기준)."""
    d = prepare(df)
    ev = []
    for t in range(1, len(d)):
        if not is_hook(d, t):
            continue
        b = bullish_at(d, t)
        comps = composite_at(d, t)
        if b or comps:
            ev.append({'t': t, 'date': d.index[t], 'bull': b, 'composite': comps})
    return ev


def last_breakout_pattern(df: pd.DataFrame, max_age: int = 12) -> dict | None:
    """지금 10이평 위에 있는 종목이, 이번 상승을 시작한 후킹 때 어떤 패턴을 완성했는지.
    (매수 후보 우선순위용 — 원서 진입 자리는 후킹·펌핑·랠리, p.256)"""
    d = prepare(df)
    n = len(d) - 1
    if n < 1 or not d['above'].iat[n]:
        return None
    k = n
    while k > 0 and d['above'].iat[k - 1]:
        k -= 1
    if n - k > max_age:
        return None
    b = bullish_at(d, k)
    comps = composite_at(d, k)
    if not b and not comps:
        return None
    return {'hook_date': d.index[k], 'months_since_hook': n - k, 'bull': b, 'composite': comps}



# ================================================================== 1·4·5·6장 규칙 (매매법_전체_구현명세.md)
def add_long_mas(d: pd.DataFrame) -> pd.DataFrame:
    """정배열·포킹·240이평 판정용 이평선(월봉이면 5·20·60·120·240개월). MA10은 prepare()의 'MA'."""
    for n in (5, 20, 60, 120, 240):
        d[f'MA{n}'] = d['Close'].rolling(n).mean()
    d['MA10'] = d['MA']
    return d


def jeongbaeyeol(d: pd.DataFrame, i: int = None) -> dict:
    """정배열(원서 p.333~335: 위에서부터 5·10·20·60·120·240). 240 데이터가 없으면 있는 이평까지만 본다."""
    i = len(d) - 1 if i is None else i
    order = [n for n in (5, 10, 20, 60, 120, 240) if not pd.isna(d[f'MA{n}'].iat[i])]
    vals = [d[f'MA{n}'].iat[i] for n in order]
    ok = len(vals) >= 2 and all(a > b for a, b in zip(vals, vals[1:]))
    return {'정배열': bool(ok), '확인한_이평': order, '240없음': 240 not in order}


def ma240_status(d: pd.DataFrame, i: int = None):
    """240이평선 위/아래(원서 p.331~332: 위에 있으면 강력한 저항, 본격 상승은 240 돌파부터). 데이터 없으면 None."""
    i = len(d) - 1 if i is None else i
    v = d['MA240'].iat[i]
    if pd.isna(v):
        return None
    return '위' if d['Close'].iat[i] > v else '아래'


def pattern_240(d: pd.DataFrame, bull: dict) -> list:
    """돌반지 240 유형(원서 p.344~353) 중 해당하는 것.
      - 후킹이 10·240 동시 돌파(p.351) — 가장 강력
      - 240밑 바닥→240 돌파(옥석 중의 옥석, p.350) — 바닥들이 240 아래였고 후킹이 240까지 돌파
      - 240 지지 바닥(p.346~348) — 바닥 저가가 240에 닿았지만(1% 이내) 종가는 240 위
    """
    out = []
    t = bull['hook']
    ma = d['MA240']
    if pd.isna(ma.iat[t]) or pd.isna(ma.iat[t - 1]):
        return out
    crosses = d['Close'].iat[t - 1] <= ma.iat[t - 1] and d['Close'].iat[t] > ma.iat[t]
    below = all(not pd.isna(ma.iat[b]) and d['Close'].iat[b] < ma.iat[b] for b in bull['bottoms'])
    if crosses:
        out.append('후킹이 10·240 동시 돌파(p.351)')
        if below:
            out.append('240밑 바닥→240 돌파(옥석 중의 옥석, p.350)')
    supported = all(not pd.isna(ma.iat[b]) and d['Low'].iat[b] <= ma.iat[b] * (1 + EQ_TOL)
                    and d['Close'].iat[b] >= ma.iat[b] for b in bull['bottoms'])
    if supported:
        out.append('240 지지 바닥(p.346~348)')
    return out


def is_forking(d: pd.DataFrame, t: int, periods=(5, 10, 20)) -> bool:
    """월봉 포킹(원서 p.384~387 검색식): 이번 봉 종가가 5·10·20 단순이평을 동시에 돌파
    (이평마다 직전 봉 종가 ≤ 그 이평, 이번 봉 종가 > 그 이평)."""
    if t < 1:
        return False
    for n in periods:
        col = 'MA' if n == 10 else f'MA{n}'
        a, b = d[col].iat[t - 1], d[col].iat[t]
        if pd.isna(a) or pd.isna(b) or not (d['Close'].iat[t - 1] <= a and d['Close'].iat[t] > b):
            return False
    return True


def four_division(d: pd.DataFrame, lookback: int = 12):
    """장대양봉 4등분선(원서 p.219~223). 가장 최근 장대양봉(연속 양봉은 합쳐서 한 캔들, p.222)의 몸통을
    4등분하고(꼬리 제외) 현재 종가 위치로 남은 상승 에너지를 본다.
      75% 이상 안전지대 — 에너지 온전 / 50% 매입원가 — 건드리면 "빨간불, 언제 나갈지 고민" /
      25% 절대자리 — 훼손되면 에너지 0 "곧 붕괴 가능성"
    ⚠️ 매도 결정은 이 매매법의 규칙대로 월봉 10이평 이탈 — 이 판정은 경고용."""
    n = len(d) - 1
    for i in range(n - 1, max(0, n - lookback) - 1, -1):
        is_run_end = d['Close'].iat[i] > d['Open'].iat[i] and not (d['Close'].iat[i + 1] > d['Open'].iat[i + 1])
        if not (is_run_end and is_big_bull(d, i)):
            continue
        k = _run_start(d, i, True)
        lo, hi = d['Open'].iat[k], d['Close'].iat[i]
        if hi <= lo:
            return None
        pos = (d['Close'].iat[n] - lo) / (hi - lo)
        if pos >= 0.75:
            zone, label = '안전지대', '75% 이상 — 상승 에너지 온전(p.220)'
        elif pos >= 0.5:
            zone, label = '매입원가 위', '50~75% — 매입원가(50%)는 지키는 중(p.221)'
        elif pos >= 0.25:
            zone, label = '매입원가 훼손', '25~50% — 매입원가 훼손, 원서 "빨간불, 언제 나갈지 고민"(p.221)'
        else:
            zone, label = '절대자리 훼손', '25% 아래 — 상승 에너지 0, 원서 "곧 붕괴 가능성"(p.221)'
        return {'zone': zone, 'label': label, 'pos': round(float(pos), 2),
                'candle_from': d.index[k], 'candle_to': d.index[i],
                'body_pct': round(run_body_pct(d, i) * 100, 1),
                'q1': float(lo + (hi - lo) * .25), 'q2': float(lo + (hi - lo) * .5), 'q3': float(lo + (hi - lo) * .75)}
    return None


def pullback_volume(d: pd.DataFrame):
    """눌림목 거래량(원서 p.364): 지금 10이평 위 상승구간(이번 상승을 시작한 봉~최고가 봉)의 최대 거래량 대비
    현재 봉 거래량. 1/7~1/20 이하면 "금상첨화" — 나간 물량이 거의 없다는 뜻.
    10이평 아래이거나 현재 봉이 상승구간 최고가 봉이면(눌림이 아니면) None."""
    n = len(d) - 1
    if not d['above'].iat[n]:
        return None
    k = n
    while k > 0 and d['above'].iat[k - 1]:
        k -= 1
    peak = int(d['High'].iloc[k:n + 1].values.argmax()) + k
    if peak == n:
        return None
    vmax = float(d['Volume'].iloc[k:peak + 1].max())
    if vmax <= 0:
        return None
    ratio = float(d['Volume'].iat[n] / vmax)
    return {'ratio': round(ratio, 3), 'ideal': ratio <= PULLBACK_VOL_IDEAL,
            'leg_start': d.index[k], 'peak': d.index[peak]}
