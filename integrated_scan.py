# -*- coding: utf-8 -*-
"""
월봉매매법 통합 스캔 — 대화 없이 한 번에 종합 판단이 나오도록 여러 체크를 합친 스크립트.

배경(2026-09-23): 기존엔 월봉×주봉(us_weekly_scan.py)과 차트패턴(bullish_pattern_scan.py)이
서로 다른 스크립트·다른 슬랙 채널로 따로 돌았고, 매출·영업이익 성장 체크는 자동화가 아예
안 돼 있어서 사용자가 매번 대화로 하나씩 물어봐야 종합 판단이 나오는 문제가 있었다
("대화를 한참해야 올바른 종목이 나오는 느낌"). 이 스크립트는 그 네 가지를 한 번에 돌려서
바로 순위(1~3군)까지 매긴 결과를 낸다.

2026-09-26 사용자 결정 — 원서 원칙으로 전환: 매수는 월말 종가로 확정된 신호(돌파=후킹 p.256,
10이평 지지 반등 p.340)만. 주봉 눌림목 매수는 폐지(진입 시점 비교 backtest_entry_timing.py —
주봉 눌림 대기는 이득 없음, 월말 전 조기진입은 29% 실패 손절·승률 하락). 근거: 캔들차트(성승현작가)/매매법_전체_구현명세.md H4.
순위(1~3군)는 원서 패턴 완성·📦박스권만으로 정한다. 매출·영업이익·우상향은 참고 표시만(검증상 성과 차이 없음 —
backtest_fundamentals.py, 2026-09-26 사용자 결정 "결과를 인정해야지").

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
import book_patterns as bkp        # 원서 패턴·매수 신호 판정(캔들차트(성승현작가)/패턴_구현명세.md)
import market_time as mt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)

STOCKS = CFG['stocks']
STOCK_THEMES = CFG.get('stock_themes', {})   # 김학주 교수 자료 기반 투자테마(업종과 별개, 참고용)
MA_MONTH = CFG.get('ma_period', 10)
STRETCH_WATCH_PCT = 20   # 추세 진행 중 종목 중 월말 10이평 대비 20% 이내만 "지지 대기" 관찰로 표시(표시용)


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


def completed_index(d) -> int:
    """월말 종가가 확정된 마지막 월봉의 위치. 진행 중인 이번 달 봉은 판단에 쓰지 않는다(원서 p.235~241).
    미국 기준 말일 장 마감 뒤면 이번 달 봉도 확정으로 본다."""
    last = len(d) - 1
    ref = mt.us_ref_date()
    if d.index[last].to_period('M') == pd.Period(ref, 'M') and not mt.is_monthend_after_close():
        return last - 1
    return last


def scan():
    """원서 원칙(2026-09-26 사용자 결정): 월말 종가 확정 신호로만 매수.
      buy_candidates: 직전 확정 월봉에 매수 신호(돌파=후킹 p.256 / 지지=10이평 눌림 반등 p.340)
      watch_list    : ① 이번 달 돌파 진행 중(월말 확정 전 — 매수 아님) ② 추세 진행 중, 10이평 지지 대기
    """
    buy_candidates, watch_list, skipped = [], [], []
    total = len(STOCKS)
    for i, (ticker, (name, sector)) in enumerate(STOCKS.items(), 1):
        print(f'  {i:02d}/{total} {ticker}...', end='\r')
        try:
            dm = fetch(ticker, '1mo', '3y')
            if len(dm) < MA_MONTH + 3:
                skipped.append((ticker, name, f'월봉 데이터 부족({len(dm)}개월)'))
                continue
            d = bkp.prepare(dm)
            t = completed_index(d)
            last = len(d) - 1
            sig_close, sig_ma = float(d['Close'].iat[t]), float(d['MA'].iat[t])
            now_close = float(d['Close'].iat[last])
            # 진행 중인 달의 잠정 10이평(지난 9개월 확정 종가 + 현재가)
            now_ma = float(d['Close'].iloc[last - MA_MONTH + 1:last + 1].mean()) if last > t else sig_ma
            now_pct = (now_close - now_ma) / now_ma * 100
            sig = bkp.buy_signal(d, t)
            base = dict(ticker=ticker, name=name, sector=sector,
                        sig_month=d.index[t].strftime('%Y-%m'), m_pct=round((sig_close - sig_ma) / sig_ma * 100, 1),
                        now_pct=round(now_pct, 1), since=round((now_close / sig_close - 1) * 100, 1))
            if sig:
                pattern, broke_up, hook_month = pattern_status(ticker)
                fnd = bp.get_fundamentals(ticker)
                opinc = opinc_yoy(ticker)
                buy_candidates.append(dict(
                    base, signal=sig, below_now=now_close <= now_ma, box=bkp.in_box(d, t),
                    pattern=pattern, broke_up=bool(broke_up), hook_month=hook_month,
                    uptrend=bp.is_uptrend(ticker),
                    revenue_growth=round(fnd['revenue_growth'] * 100, 1) if fnd['revenue_growth'] is not None else None,
                    opinc_pct=opinc['pct'], opinc_note=opinc['note'],
                ))
            elif sig_close <= sig_ma:
                if last > t and now_close > now_ma:
                    watch_list.append(dict(base, reason='이번 달 돌파 진행 중(월말 확정 전 — 매수 아님)', kind=1))
            elif base['m_pct'] <= STRETCH_WATCH_PCT:
                reason = ('추세 진행 중 — 10이평 지지(눌림) 대기' if now_close > now_ma else
                          '⚠️ 지금 10이평 아래 — 월말 종가로 지지(매수) 또는 이탈 결정')
                watch_list.append(dict(base, reason=reason, kind=2))
        except Exception as e:
            # 예외를 조용히 삼키지 않고 어떤 종목이 왜 빠졌는지 기록(CLAUDE.md "내부 예외가 조용히 삼켜지는" 함정)
            skipped.append((ticker, name, f'{type(e).__name__}: {e}'))
            continue

    def score(c):
        return (tier_of(c), c['signal'] != '돌파', c['ticker'])   # 매출·이익은 순위에 안 씀(표시만)

    buy_candidates.sort(key=score)
    watch_list.sort(key=lambda x: (x['kind'], x['m_pct']))
    return buy_candidates, watch_list, skipped


def tier_of(c):
    """우선순위(매수 여부 아님 — 목록의 종목은 전부 원서 매수 신호). 원서·검증 근거가 있는 것만 쓴다:
      1군 = 원서 패턴 완성 후킹으로 시작한 상승(명세서 §7: 매매법 거래 평균 +42.8% vs 패턴 없는 후킹 +18.9%, 유의)
      2군 = 원서 매수 신호만
      3군 = 📦박스권 안(p.309, backtest_box_range.py: 박스 안 신호 +9.6% vs 나머지 +28.9%)
    2026-09-26 사용자 결정("시뮬레이션 결과를 인정해야지"): 매출·영업이익 성장·우상향은 순위에서 제외, 표시만.
    근거 backtest_fundamentals.py — SEC 공시 기준 신호 13,155건에서 매출+10%↑&이익증가 신호가 평균 +13.9%·승률 36.7%로
    나머지(+14.1%·41.6%)와 차이 없음(95% 구간 −1.6%~+4.1%). 우상향 필터도 효과 불확실(명세서 §7)."""
    if c['box']:
        return 3
    return 1 if c['broke_up'] else 2


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


TIER_LABEL = {1: '1군 — 원서 패턴 완성으로 시작한 상승(검증상 가장 강함)',
              2: '2군 — 원서 매수 신호',
              3: '3군 — 📦박스권 안(상단 돌파 전, p.309) — 후순위'}
SIG_LABEL = {'돌파': '돌파(후킹 p.256)', '지지': '10이평 지지 반등(p.340)'}


def _buy_line(c):
    pat = (f"원서패턴:{c['pattern']}(후킹 {c['hook_month']}" if c['pattern']
           else f"원서패턴:없음 {c['hook_month'] or ''}")
    warn = ' ⚠️지금 10이평 아래 — 이번 달 말 이탈 위험' if c['below_now'] else ''
    if c['box']:
        warn += ' ' + bkp.BOX_LABEL
    return (f"{c['sig_month']}말 {SIG_LABEL[c['signal']]} · 신호 후 {fmt_pct(c['since'])}{warn} | "
            f"우상향 {'O' if c['uptrend'] else 'X'} · 매출{fmt_pct(c['revenue_growth'])} 영업이익{fmt_opinc(c)} | {pat}")


def print_report(buy_candidates, watch_list, skipped):
    now = datetime.today().strftime('%Y-%m-%d')
    print()
    print('=' * 84)
    print(f'  월봉매매법 통합 스캔 — 원서 원칙(월말 종가 확정 신호로만 매수)  [{now}]')
    print('=' * 84)
    print('  매수 = 월말 종가로 확정된 돌파(후킹) 또는 10이평 지지 반등. 매도 = 월말 종가 10이평 이탈.')
    print('  군 구분 = 원서 패턴 완성(1군) / 신호만(2군) / 📦박스권(3군). 매출·이익·우상향은 참고 표시(순위에 안 씀).')
    print()
    if not buy_candidates:
        print('  원서 매수 신호 종목 없음')
    for tier in (1, 2, 3):
        rows = [c for c in buy_candidates if tier_of(c) == tier]
        if not rows:
            continue
        print(f'  [{TIER_LABEL[tier]}]')
        for c in rows:
            print(f"    {c['ticker']:<6} {c['name']:<14} {fmt_tag(c['ticker'], c['sector'])}  {_buy_line(c)}")
        print()
    if watch_list:
        print('  [관찰 — 매수 신호 아님]')
        for w in watch_list:
            print(f"    {w['ticker']:<6} {w['name']:<14} {fmt_tag(w['ticker'], w['sector'])}  "
                  f"월말 10이평 대비 {fmt_pct(w['m_pct'])} · 현재 {fmt_pct(w['now_pct'])} | {w['reason']}")
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
    lines = [f'*월봉매매법 통합 스캔 — 원서 원칙* ({now})',
             '_매수 = 월말 종가로 확정된 돌파(후킹) 또는 10이평 지지 반등 / 매도 = 월말 종가 10이평 이탈_',
             '_1군 원서 패턴 완성 / 2군 신호만 / 3군 📦박스권 — 매출·이익·우상향은 참고 표시_']
    tier_icon = {1: '🥇', 2: '🥈', 3: '🥉'}
    for tier in (1, 2, 3):
        rows = [c for c in buy_candidates if tier_of(c) == tier]
        if not rows:
            continue
        lines.append(f'\n*{tier_icon[tier]} {TIER_LABEL[tier]}*')
        for c in rows:
            lines.append(f"`{c['ticker']}` {c['name']} `{fmt_tag(c['ticker'], c['sector'])}`  {_buy_line(c)}")
    if not buy_candidates:
        lines.append('원서 매수 신호 종목 없음')
    w1 = [w for w in watch_list if w['kind'] == 1]
    w2 = [w for w in watch_list if w['kind'] == 2]
    if w1:
        lines.append('\n*👀 이번 달 돌파 진행 중 — 월말 종가 확정 전이라 매수 아님*')
        for w in w1[:10]:
            lines.append(f"`{w['ticker']}` {w['name']} `{fmt_tag(w['ticker'], w['sector'])}`  현재 잠정 10이평 대비 {fmt_pct(w['now_pct'])}")
    if w2:
        lines.append('\n*⏳ 추세 진행 중 — 10이평 지지(눌림) 대기*')
        for w in w2[:10]:
            lines.append(f"`{w['ticker']}` {w['name']} `{fmt_tag(w['ticker'], w['sector'])}`  월말 10이평 대비 {fmt_pct(w['m_pct'])}")
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
