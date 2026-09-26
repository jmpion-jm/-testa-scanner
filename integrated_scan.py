# -*- coding: utf-8 -*-
"""
월봉매매법 통합 스캔 — 대화 없이 한 번에 종합 판단이 나오도록 여러 체크를 합친 스크립트.

배경(2026-09-23): 기존엔 월봉×주봉(us_weekly_scan.py)과 차트패턴(bullish_pattern_scan.py)이
서로 다른 스크립트·다른 슬랙 채널로 따로 돌았고, 매출·영업이익 성장 체크는 자동화가 아예
안 돼 있어서 사용자가 매번 대화로 하나씩 물어봐야 종합 판단이 나오는 문제가 있었다
("대화를 한참해야 올바른 종목이 나오는 느낌"). 이 스크립트는 그 네 가지를 한 번에 돌려서
바로 순위(1~3군)까지 매긴 결과를 낸다.

⚠️ 매수 규칙 자체를 바꾸지 않는다 — 책의 공식 신호는 월봉MA10 하나뿐이고, 주봉 눌림목은
사용자의 진입 타이밍 보조 도구다. 원서패턴(원서 2장, book_patterns.py)·매출/이익 성장(사용자 추가
지표)은 여러 매수 가능 종목 중 우선순위를 매기는 용도로만 쓴다. "1군만 매수 가능"이
아니라 "1군이 가장 확신도 높음, 2·3군도 이미 매수 조건은 충족한 종목"이다.
(2026-09-26 패턴 판정을 원서 기준으로 교체 — 캔들차트(성승현작가)/패턴_구현명세.md)

실행: python integrated_scan.py           (콘솔 출력만)
      python integrated_scan.py slack     (슬랙 전송까지)
"""
import sys, io, json, os, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import urllib.request
from datetime import datetime

import bullish_pattern_scan as bp   # fetch_monthly·get_fundamentals만 사용
import book_patterns as bkp        # 원서 패턴 판정(캔들차트(성승현작가)/패턴_구현명세.md)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)

STOCKS = CFG['stocks']
STOCK_THEMES = CFG.get('stock_themes', {})   # 김학주 교수 자료 기반 투자테마(업종과 별개, 참고용)
MA_MONTH = CFG.get('ma_period', 10)
MA_WEEK = 10
ZONE_PCT = 5        # 주봉MA10 대비 이 %이내 = 눌림목
STRETCH_WATCH_PCT = 20   # 월봉이 5~20% 뻗은 종목 = "관찰 대상"으로 별도 표시


def fetch(ticker, interval, period):
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def opinc_yoy(ticker: str) -> dict:
    """최근 분기 영업이익 vs 1년 전 같은 분기 비교. {'pct','note','ok'} 반환.

    분기 중 하나라도 Operating Income이 NaN이면 dropna()가 행을 통째로 지워서 단순히
    4칸 뒤(iloc[4])를 짚으면 실제로는 1년보다 더 먼 분기와 비교하게 될 수 있다 — 날짜
    컬럼으로 정확히 '365일 전에 가장 가까운 분기'를 찾고, 45일 이상 어긋나면 포기한다.

    적자↔흑자 전환 구간은 %가 왜곡된다(예: -10→+5는 공식상 +150%로 나와 마치 큰 성장처럼
    보이지만 실제론 흑자전환일 뿐) — CEVA 사례로 확인(2026-09-23). 부호가 바뀌는 경우는
    %를 숨기고 '적자→흑자전환'/'흑자→적자전환' 문구로만 표시한다.
    """
    try:
        q = yf.Ticker(ticker).quarterly_financials
        if 'Operating Income' not in q.index:
            return {'pct': None, 'note': '데이터없음', 'ok': False}
        oi = q.loc['Operating Income'].dropna()
        if len(oi) < 2:
            return {'pct': None, 'note': '데이터부족', 'ok': False}
        latest_date, latest = oi.index[0], oi.iloc[0]
        target = latest_date - pd.Timedelta(days=365)
        diffs = (oi.index - target).to_series().abs()
        closest_pos = diffs.values.argmin()
        if diffs.iloc[closest_pos] > pd.Timedelta(days=45):
            return {'pct': None, 'note': '1년전 분기 못찾음', 'ok': False}
        yoy = oi.iloc[closest_pos]
        if not yoy:
            return {'pct': None, 'note': '기준분기 0원', 'ok': False}

        if yoy < 0 and latest >= 0:
            return {'pct': None, 'note': '적자→흑자전환', 'ok': True}
        if yoy >= 0 and latest < 0:
            return {'pct': None, 'note': '흑자→적자전환', 'ok': True}
        if yoy < 0 and latest < 0:
            # 둘 다 적자: 적자 폭이 줄었는지(축소) 늘었는지(확대)만 부호 없이 표시
            shrink = latest > yoy
            return {'pct': None, 'note': f"적자{'축소' if shrink else '확대'}", 'ok': True}
        pct = round((latest - yoy) / abs(yoy) * 100, 1)
        return {'pct': pct, 'note': None, 'ok': True}
    except Exception as e:
        return {'pct': None, 'note': f'조회실패:{type(e).__name__}', 'ok': False}


def pattern_status(ticker: str):
    """지금의 상승을 시작한 후킹(10이평 상향 관통 양봉)이 원서 패턴 완성이었는지.

    반환: (패턴 라벨 또는 None, 원서패턴완성 여부 bool, "후킹 월) 정배열·240 정보" 또는 정배열·240 정보만)
    판정은 book_patterns.last_breakout_pattern — 규칙은 패턴_구현명세.md(원서 쪽수 근거, 원서 예시 10개 재현)에 고정.
    2026-09-26: 이전 판(넥라인 돌파 + is_uptrend)은 원서와 달라 폐기(명세서 §9). 백테스트상 원서 패턴으로 시작한
    상승은 매매법 거래 평균 +42.8% vs 패턴 없는 후킹 +18.9%(명세서 §7) — 우선순위 근거로 쓴다.
    """
    try:
        df = bp.fetch_monthly(ticker)
    except Exception:
        return None, False, None
    # 정배열(원서 p.333)·240이평(p.331, 돌반지 p.344~353) — 패턴 유무와 상관없이 표시
    d = bkp.add_long_mas(bkp.prepare(df))
    jb = bkp.jeongbaeyeol(d)
    extra = f"정배열 {'O' if jb['정배열'] else 'X'} · 240 {bkp.ma240_status(d) or '데이터없음'}"
    r = bkp.last_breakout_pattern(df)
    if r is None:
        return None, False, extra
    names = ([r['bull']['pattern']] if r['bull'] else []) + [c['pattern'] for c in r['composite']]
    month = r['hook_date'].strftime('%Y-%m')
    if r['months_since_hook'] == 0:
        month += ' 진행중·미확정'   # 후킹이 아직 안 끝난 이번 달 봉 — 월말 종가로 뒤집힐 수 있음
    if r['bull']:
        t240 = bkp.pattern_240(d, r['bull'])
        if t240:
            extra += ' · ' + ' · '.join(t240)
    return '+'.join(names), True, f'{month}) {extra}'


def scan():
    buy_candidates = []   # 월봉+주봉 조건 충족 (매수 가능)
    watch_list = []       # 월봉만 충족 + 5~20% 뻗음 (조정 시 재진입 후보로 관찰)
    skipped = []          # 데이터 부족/조회 실패로 판단 자체를 못한 종목 — 조용히 넘어가지 않고 기록

    total = len(STOCKS)
    for i, (ticker, (name, sector)) in enumerate(STOCKS.items(), 1):
        print(f'  {i:02d}/{total} {ticker}...', end='\r')
        try:
            dm = fetch(ticker, '1mo', '3y')
            if len(dm) < MA_MONTH + 2:
                skipped.append((ticker, name, f'월봉 데이터 부족({len(dm)}개월)'))
                continue
            dm['MA'] = dm['Close'].rolling(MA_MONTH).mean()
            m_close, m_ma = float(dm.iloc[-1]['Close']), float(dm.iloc[-1]['MA'])
            m_pct = (m_close - m_ma) / m_ma * 100
            if m_close <= m_ma:
                continue   # 월봉MA10 아래 — 매수 후보도 관찰 대상도 아님 (정상적인 제외, 스킵 아님)

            dw = fetch(ticker, '1wk', '2y')
            if len(dw) < MA_WEEK + 2:
                skipped.append((ticker, name, f'주봉 데이터 부족({len(dw)}주)'))
                continue
            dw['MA'] = dw['Close'].rolling(MA_WEEK).mean()
            w_close, w_ma = float(dw.iloc[-1]['Close']), float(dw.iloc[-1]['MA'])
            w_pct = (w_close - w_ma) / w_ma * 100

            if m_pct <= ZONE_PCT and w_close > w_ma and w_pct <= ZONE_PCT:
                # ── 매수 후보: 월봉·주봉 둘 다 조건 충족 → 패턴/펀더멘털까지 확인 ──
                pattern, broke_up, hook_month = pattern_status(ticker)
                fnd = bp.get_fundamentals(ticker)
                opinc = opinc_yoy(ticker)
                buy_candidates.append(dict(
                    ticker=ticker, name=name, sector=sector,
                    m_pct=round(m_pct, 1), w_pct=round(w_pct, 1),
                    pattern=pattern, broke_up=bool(broke_up), hook_month=hook_month,
                    revenue_growth=round(fnd['revenue_growth'] * 100, 1) if fnd['revenue_growth'] is not None else None,
                    opinc_pct=opinc['pct'], opinc_note=opinc['note'],
                ))
            elif m_pct <= STRETCH_WATCH_PCT:
                # ── 관찰 대상: 월봉 추세는 살아있는데 매수조건(월봉·주봉 둘 다 5%이내) 미충족.
                # 진짜 이유(월봉이 뻗음/주봉이 눌림목 아님)를 구분해서 담아 출력 라벨을 정확히 한다.
                reason = '월봉뻗음' if m_pct > ZONE_PCT else '주봉눌림목아님'
                watch_list.append(dict(
                    ticker=ticker, name=name, sector=sector,
                    m_pct=round(m_pct, 1), w_pct=round(w_pct, 1), reason=reason,
                ))
        except Exception as e:
            # 2026-09-23 수정: 예외를 조용히 삼키지 않고 어떤 종목이 왜 빠졌는지 기록한다
            # (CLAUDE.md가 GitHub Actions에 대해 경고한 "내부 예외가 조용히 삼켜지는" 문제와 동일한
            # 함정이 이 스크립트에도 있었음).
            skipped.append((ticker, name, f'{type(e).__name__}: {e}'))
            continue

    def opinc_score(c):
        if c['opinc_pct'] is not None:
            return c['opinc_pct']
        return {'적자→흑자전환': 100, '적자축소': 10, '적자확대': -10, '흑자→적자전환': -100}.get(c['opinc_note'], 0)

    def score(c):
        rev = c['revenue_growth'] or 0
        op = opinc_score(c)
        return (0 if c['broke_up'] else 1, -(rev + op))

    buy_candidates.sort(key=score)
    watch_list.sort(key=lambda x: x['m_pct'])
    return buy_candidates, watch_list, skipped


def _opinc_positive(c):
    if c['opinc_pct'] is not None:
        return c['opinc_pct'] > 0
    return c['opinc_note'] in ('적자→흑자전환', '적자축소')


def tier_of(c):
    growth_ok = (c['revenue_growth'] or 0) > 0 and _opinc_positive(c)
    if c['broke_up'] and growth_ok:
        return 1
    if c['broke_up'] or growth_ok:
        return 2
    return 3


def fmt_pct(v):
    return f'{v:+.1f}%' if v is not None else '데이터없음'


def fmt_opinc(c):
    return c['opinc_note'] if c['opinc_note'] else fmt_pct(c['opinc_pct'])


def fmt_tag(ticker, sector):
    """[업종] 또는 [업종|테마] — 테마는 김학주 교수 자료 기반 참고용, 업종과 다를 때만 붙인다."""
    theme = STOCK_THEMES.get(ticker)
    if theme and theme != sector:
        return f'[{sector}|테마:{theme}]'
    return f'[{sector}]'


def print_report(buy_candidates, watch_list, skipped):
    now = datetime.today().strftime('%Y-%m-%d')
    print()
    print('=' * 84)
    print(f'  월봉매매법 통합 스캔 (월봉MA10 + 주봉눌림목 + 원서패턴 + 매출·영업이익성장)  [{now}]')
    print('=' * 84)
    print('  ※ 공식 매수 신호는 월봉MA10뿐. 아래 순위는 여러 매수가능 종목 중 확신도 우선순위임.')
    print()

    if not buy_candidates:
        print('  매수 후보 없음 (월봉MA10 위 + 주봉눌림목 5% 이내 종목 없음)')
    else:
        print(f'  ✅ 아래 {len(buy_candidates)}종목 전부 지금 매수 조건(월봉+주봉) 충족 — 군 구분은 매수가능 여부가')
        print('     아니라 참고용 확신도 순위일 뿐(원서패턴=원서 2장 기준, 매출·이익=사용자 추가 지표)')
        print()
    for tier in (1, 2, 3):
        rows = [c for c in buy_candidates if tier_of(c) == tier]
        if not rows:
            continue
        label = {1: '1군 [매수가능] — 참고지표(원서패턴 완성+매출·이익 동반성장) 전부 충족, 확신도 최상',
                  2: '2군 [매수가능] — 참고지표 일부만 충족, 확신도 중간',
                  3: '3군 [매수가능] — 참고지표는 아직인데 월봉+주봉 조건만으로 매수가능'}[tier]
        print(f'  [{label}]')
        for c in rows:
            pat = (f"원서패턴:{c['pattern']}(후킹 {c['hook_month']}" if c['pattern']
                   else f"원서패턴:없음(이번 상승이 패턴 없는 후킹) {c['hook_month'] or ''}")
            print(f"    {c['ticker']:<6} {c['name']:<14} {fmt_tag(c['ticker'], c['sector'])}  월봉{fmt_pct(c['m_pct'])} 주봉{fmt_pct(c['w_pct'])}"
                  f" | {pat} | 매출{fmt_pct(c['revenue_growth'])} 영업이익{fmt_opinc(c)}")
        print()

    if watch_list:
        print(f'  [관찰 대상 — 월봉 추세는 살아있으나 매수조건(월봉·주봉 둘 다 {ZONE_PCT}%이내) 미충족]')
        for w in watch_list:
            print(f"    {w['ticker']:<6} {w['name']:<14} {fmt_tag(w['ticker'], w['sector'])}  월봉{fmt_pct(w['m_pct'])} 주봉{fmt_pct(w['w_pct'])} | 사유:{w['reason']}")

    if skipped:
        print(f'\n  [판단 불가 — 데이터 조회 실패/부족으로 스킵된 종목 {len(skipped)}개 (조용히 안 넘어감)]')
        for tk, nm, why in skipped:
            print(f"    {tk:<6} {nm:<14} {why}")
    print('=' * 84)


def send_slack(buy_candidates, watch_list, skipped):
    url = CFG.get('slack_webhook_url', '')
    if not url:
        return
    now = datetime.today().strftime('%Y-%m-%d')
    lines = [f'*월봉매매법 통합 스캔* ({now})',
             '_공식 매수신호는 월봉MA10뿐. 아래 종목은 전부 이미 매수조건(월봉+주봉) 충족 —_',
             '_군 구분은 매수가능 여부가 아니라 패턴·매출·이익(참고지표) 확신도 순위일 뿐_']
    tier_labels = {1: '🥇 1군 [매수가능] 참고지표 전부 충족(최상)', 2: '🥈 2군 [매수가능] 참고지표 일부 충족',
                   3: '🥉 3군 [매수가능] 참고지표 아직(월봉+주봉만)'}
    any_row = False
    for tier in (1, 2, 3):
        rows = [c for c in buy_candidates if tier_of(c) == tier]
        if not rows:
            continue
        any_row = True
        lines.append(f'\n*{tier_labels[tier]}*')
        for c in rows:
            pat = f"원서패턴:{c['pattern']}(후킹 {c['hook_month']}" if c['pattern'] else f"원서패턴:없음 {c['hook_month'] or ''}"
            lines.append(f"`{c['ticker']}` {c['name']} `{fmt_tag(c['ticker'], c['sector'])}`  월봉{fmt_pct(c['m_pct'])} 주봉{fmt_pct(c['w_pct'])}"
                          f"  {pat}  매출{fmt_pct(c['revenue_growth'])} 영업이익{fmt_opinc(c)}")
    if not any_row:
        lines.append('매수 후보 없음')
    if watch_list:
        lines.append(f'\n*👀 관찰 대상 (매수조건 미충족, 조정/재돌파 시 후보 편입 가능)*')
        for w in watch_list[:10]:
            lines.append(f"`{w['ticker']}` {w['name']} `{fmt_tag(w['ticker'], w['sector'])}`  월봉{fmt_pct(w['m_pct'])} 주봉{fmt_pct(w['w_pct'])} ({w['reason']})")
    if skipped:
        lines.append(f'\n*⚠️ 판단 불가 {len(skipped)}종목* (데이터 조회 실패/부족)')
        for tk, nm, why in skipped[:10]:
            lines.append(f'`{tk}` {nm} — {why}')

    payload = {'text': '\n'.join(lines)}
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                  headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f'슬랙 전송 실패: {e}')


if __name__ == '__main__':
    buy_candidates, watch_list, skipped = scan()
    print_report(buy_candidates, watch_list, skipped)
    if len(sys.argv) > 1 and sys.argv[1] == 'slack':
        send_slack(buy_candidates, watch_list, skipped)
        print('슬랙 전송 완료')
