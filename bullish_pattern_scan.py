# -*- coding: utf-8 -*-
"""
월말 신규 후보 발굴 — 성승현 원서 2장 상승 패턴이 이번 달에 완성된 종목 (월봉 기준).

패턴 판정은 전부 `book_patterns.py`가 한다. 규칙은 `캔들차트(성승현작가)/패턴_구현명세.md`에 원서 쪽수와 함께
고정돼 있고 tests/test_book_patterns.py가 원서 예시 10개 재현으로 잠가 둔다 — 여기서 판정 규칙을 새로 만들지 말 것.

## 2026-09-26 재작성 (이전 판 폐기 사유 — 명세서 §9)
이전 판은 완성 조건을 "두 저점 사이 최고가(넥라인) 돌파 + 지금 10이평 위"로 구현해서 원서(10이평을 아래에서 뚫는
후킹 캔들, p.256)와 달랐고, 상승 중인 종목이 거의 다 "쌍바닥"으로 잡혔다. "백테스트 59~83% 검증"도 기준선
비교가 없어 사실상 검증이 아니었다.

## 무엇을 보고하나
원서 진입 자리(p.256): 후킹 캔들 종가 / 10이평 지지를 확인한 펌핑 캔들 종가.
  - 후킹: 이번 달 봉이 원서 상승 패턴(쌍바닥·역H&S·삼중바닥, 되돌림·겹/대쌍바닥)을 완성한 후킹 캔들
  - 펌핑: 지난달이 그 후킹이고 이번 달도 10이평 위에서 마감
사용자 규칙(원서 규칙 아님): 우상향(is_uptrend) + 매출성장률 10% 이상.
2026-09-26 사용자 결정: 이 두 조건은 제외 필터가 아니라 우선순위 — 원서 패턴 종목은 전부 보여주고,
두 조건을 모두 충족한 종목을 ⭐최우선으로 맨 위에 올린다(우상향 필터가 원서 패턴의 약 81%를 지웠고
효과는 통계적으로 불확실 — 명세서 §7).

실행: python bullish_pattern_scan.py [nasdaq100|sp500|kospi]
      (GitHub Actions: 매월 말일 미국장 마감 후 nasdaq100)
"""
import sys, json, os, warnings, urllib.request
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
from datetime import datetime

import book_patterns as bkp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)
MA_PERIOD = CFG.get('ma_period', 10)
WEBHOOK = CFG.get('slack_webhook_url_discovery', '')
STOCK_INFO = CFG.get('stocks', {})          # 티커 -> [한글명, 업종]
STOCK_THEMES = CFG.get('stock_themes', {})  # 티커 -> 김학주 교수 자료 기반 투자테마
MIN_REVENUE_GROWTH = 0.10  # 사용자 규칙: 매출성장률 10% 이상 — 제외가 아니라 우선순위(2026-09-26)


def fetch_monthly(ticker: str) -> pd.DataFrame:
    # period="max": 원서 240이평(월봉=240개월) 판정에 20년 이상 필요(매매법_전체_구현명세.md C2·C6)
    df = yf.Ticker(ticker).history(period="max", interval="1mo", auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()


def is_uptrend(ticker: str) -> bool:
    """사용자 요청 필터(2026-09 CRSP 사례 이후): 최근 월봉MA10이 3개월 전보다 높고 가격이 6개월 전보다 높은가.
    원서 규칙이 아니다 — 원서 패턴은 하락 끝 바닥에서 나오므로 이 필터가 대부분을 걸러낸다(명세서 §7)."""
    try:
        dm = yf.Ticker(ticker).history(period="2y", interval="1mo", auto_adjust=True)
        if len(dm) < 8:
            return False
        dm['MA10'] = dm['Close'].rolling(MA_PERIOD).mean()
        return float(dm['MA10'].iloc[-1]) > float(dm['MA10'].iloc[-4]) and float(dm['Close'].iloc[-1]) > float(dm['Close'].iloc[-7])
    except Exception:
        return False


def get_fundamentals(ticker: str) -> dict:
    """yfinance .info 한 번으로 매출성장률/이익성장률/영문 회사명/업종."""
    try:
        info = yf.Ticker(ticker).info
    except Exception:
        info = {}
    g = info.get('revenueGrowth')
    e = info.get('earningsGrowth')
    return {
        'revenue_growth': float(g) if g is not None else None,
        'earnings_growth': float(e) if e is not None else None,
        'long_name': info.get('longName') or info.get('shortName'),
        'sector': info.get('industry') or info.get('sector'),
    }


def _label(bull, comps):
    names = ([bull['pattern']] if bull else []) + [c['pattern'] for c in comps]
    return '+'.join(names)


def _extra(d, bull, last):
    """정배열(p.333)·240이평(p.331, 돌반지 p.344~353) 정보."""
    jb = bkp.jeongbaeyeol(d, last)
    info = {'정배열': jb['정배열'], '240': bkp.ma240_status(d, last) or '데이터없음(상장 20년 미만)'}
    info['240유형'] = bkp.pattern_240(d, bull) if bull else []
    return info


def _stage(d, last):
    """원서 진입 단계(p.256, p.401, 거래량 p.365). 반환: (후킹 봉 인덱스, 단계, 거래량 메모) 또는 None.
      후킹: 이번 달이 후킹 캔들
      펌핑: 지난달 후킹, 이번 달 10이평 위에서 쉬어감 — 원서 "펌핑 캔들은 거래량이 나오지 않는 것이 좋다"
      랠리: 두 달 전 후킹, 지난달 펌핑, 이번 달 10이평 위 양봉 — 원서 "랠리 캔들에는 거래량이 붙어야"
    """
    vol = d['Volume']
    if bkp.is_hook(d, last):
        return last, '후킹', ''
    if last >= 1 and bkp.is_hook(d, last - 1) and d['above'].iat[last]:
        quiet = vol.iat[last] < vol.iat[last - 1]
        return last - 1, '펌핑', ('펌핑 거래량 감소 O(p.365)' if quiet else '펌핑 거래량 증가 X — 원서: 상승 가능성 희박(p.365)')
    if (last >= 2 and bkp.is_hook(d, last - 2) and d['above'].iat[last - 1] and d['above'].iat[last]
            and d['Close'].iat[last] > d['Open'].iat[last]):
        with_vol = vol.iat[last] > vol.iat[last - 1]
        return last - 2, '랠리', ('랠리 거래량 동반 O(p.365)' if with_vol else '랠리 거래량 부족 X(p.365)')
    return None


def scan_bullish(universe: list[tuple[str, str]], label: str) -> tuple[list[dict], dict]:
    """반환: (최종 후보, 단계별 개수·포킹 목록). 원서 상승 패턴이 이번 달 후킹/펌핑/랠리 단계인 종목 + 월봉 포킹."""
    found, forking, errors = [], [], []
    total = len(universe)
    for n, (ticker, name) in enumerate(universe, 1):
        print(f'  [{label}] {n:>3}/{total} {ticker:<8}', end='\r')
        try:
            d = bkp.add_long_mas(bkp.prepare(fetch_monthly(ticker)))
        except Exception as e:
            errors.append((ticker, f'{type(e).__name__}: {e}'))
            continue
        last = len(d) - 1
        if last < 2:
            errors.append((ticker, '데이터 부족'))
            continue
        if bkp.is_forking(d, last):   # 원서 p.384~387 월봉 포킹 검색식(5·10·20 동시 돌파)
            forking.append({'ticker': ticker, 'name': name, **_extra(d, None, last)})
        st = _stage(d, last)
        if st is None:
            continue
        k, stage, vol_note = st
        bull = bkp.bullish_at(d, k)
        comps = bkp.composite_at(d, k)
        if not bull and not comps:
            continue
        found.append({
            'ticker': ticker, 'name': name, 'stage': stage, 'vol_note': vol_note,
            'hook_date': d.index[k].strftime('%Y-%m'), 'pattern_label': _label(bull, comps),
            'quality': (bull or {}).get('quality', {}),
            'cur_close': float(d['Close'].iat[last]), 'cur_ma10': float(d['MA'].iat[last]),
            'box': bkp.in_box(d, last),   # 박스권 안(p.309) — 후순위 표시
            **_extra(d, bull, last),
        })
    print(' ' * 50, end='\r')

    counts = {'pattern': len(found), 'errors': errors, 'forking': forking}
    for r in found:
        t = r['ticker']
        r['uptrend'] = is_uptrend(t)
        fund = get_fundamentals(t)
        r['revenue_growth'] = fund['revenue_growth']
        r['earnings_growth'] = fund['earnings_growth']  # 표시만
        r['rev_ok'] = fund['revenue_growth'] is not None and fund['revenue_growth'] >= MIN_REVENUE_GROWTH
        r['top'] = r['uptrend'] and r['rev_ok']        # 사용자 규칙 둘 다 충족 = 최우선
        if t in STOCK_INFO:
            r['name'] = STOCK_INFO[t][0]
            r['sector'] = STOCK_INFO[t][1]
            r['theme'] = STOCK_THEMES.get(t)
        else:
            r['name'] = fund['long_name'] or t
            r['sector'] = fund['sector']
            r['theme'] = None
    found.sort(key=lambda r: (r['box'], not r['top'], not r['rev_ok'], not r['uptrend']))
    counts['uptrend'] = sum(r['uptrend'] for r in found)
    counts['growth'] = sum(r['rev_ok'] for r in found)
    counts['top'] = sum(r['top'] for r in found)
    return found, counts


def _funnel(counts):
    return (f"원서 패턴 {counts['pattern']}종목 (⭐최우선 = 우상향+매출성장 {MIN_REVENUE_GROWTH*100:.0f}%↑ 둘 다: "
            f"{counts['top']} / 우상향 {counts['uptrend']} / 매출성장 {counts['growth']})")


def _book_info(r):
    parts = [f"정배열 {'O' if r.get('정배열') else 'X'}", f"240 {r.get('240')}"]
    parts += r.get('240유형', [])
    if r.get('vol_note'):
        parts.append(r['vol_note'])
    return ' · '.join(parts)


def _mark(r):
    return (('⭐최우선 ' if r['top'] and not r['box'] else '') + f"우상향 {'O' if r['uptrend'] else 'X'}"
            + (' 📦박스권(상단 돌파 전)' if r['box'] else ''))


def _pct(v):
    return f"{v*100:+.0f}%" if v is not None else "N/A"


def print_report(results: list[dict], counts: dict, label: str):
    print(f"\n{'=' * 100}\n  원서 상승 패턴 월말 스캔 — {label}   [{datetime.today().strftime('%Y-%m-%d')}]\n{'=' * 100}")
    print('  ' + _funnel(counts))
    for t, why in counts['errors']:
        print(f'  [판단 불가] {t}: {why}')
    if not results:
        print('  원서 패턴 종목 없음')
    for r in results:
        sec = r.get('sector') or ''
        theme = f"|테마:{r['theme']}" if r.get('theme') and r['theme'] != sec else ''
        print(f"  {_mark(r)} [{r['pattern_label']}·{r['stage']}] {r['ticker']:<8} {r['name']} [{sec}{theme}] "
              f"후킹 {r['hook_date']}  종가 {r['cur_close']:,.2f} (10이평 {r['cur_ma10']:,.2f})  "
              f"매출 {_pct(r['revenue_growth'])} 이익 {_pct(r.get('earnings_growth'))}  "
              f"| {_book_info(r)}")
    if counts['forking']:
        print(f"\n  [월봉 포킹 — 종가가 5·10·20이평 동시 돌파, 원서 p.384~387] {len(counts['forking'])}종목")
        for f in counts['forking']:
            print(f"    {f['ticker']:<8} {f['name']}  | 정배열 {'O' if f['정배열'] else 'X'} · 240 {f['240']}")
    print('  후킹·펌핑(p.256) 종목은 원서 매수 자리 — ⭐는 사용자 규칙 우선순위일 뿐, 제외 기준 아님')
    print('=' * 100 + '\n')


def send_slack(results: list[dict], counts: dict, label: str):
    """discovery 채널로 전송."""
    if not WEBHOOK:
        print('  WEBHOOK 없음 — Slack 전송 생략')
        return
    today = datetime.today().strftime('%Y.%m.%d')
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📐 원서 상승 패턴 월말 스캔 — {label}  {today}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text":
            "성승현 원서 2장 패턴(쌍바닥·역H&S·삼중바닥, 되돌림·겹/대쌍바닥)이 이번 달 후킹(10이평 상향 관통 양봉)으로 "
            "완성됐거나 지난달 후킹 후 펌핑 중인 종목. 원서 예시 10개 재현 검증 완료. "
            "후킹·펌핑은 원서 매수 자리(p.256). ⭐최우선 = 사용자 규칙(우상향+매출성장10%↑) 충족 — 순위일 뿐 제외 기준 아님."}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": _funnel(counts)}},
        {"type": "divider"},
    ]
    if not results:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "원서 패턴 종목 없음."}})
    for r in results[:30]:  # 슬랙 블록 50개 제한
        sec = r.get('sector') or ''
        theme = f"|테마:{r['theme']}" if r.get('theme') and r['theme'] != sec else ''
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
            f"{_mark(r)} *[{r['pattern_label']}·{r['stage']}]* `{r['ticker']}` {r['name']} `[{sec}{theme}]`  "
            f"후킹 {r['hook_date']}  매출 {_pct(r['revenue_growth'])} 이익 {_pct(r.get('earnings_growth'))}\n"
            f"_{_book_info(r)}_"}})
    if counts['forking']:
        fl = counts['forking'][:15]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
            f"*🔀 월봉 포킹* (종가가 5·10·20이평 동시 돌파, 원서 p.384~387) — {len(counts['forking'])}종목\n" +
            '\n'.join(f"`{f['ticker']}` {f['name']}  정배열 {'O' if f['정배열'] else 'X'} · 240 {f['240']}" for f in fl)}})
    if counts['errors']:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn",
                       "text": f"⚠️ 판단 불가 {len(counts['errors'])}종목(데이터 조회 실패) — 로그 참고"}]})
    payload = json.dumps({'text': f'원서 상승 패턴 월말 스캔 {today}', 'blocks': blocks}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print(f'Slack 전송: {"성공" if res.read().decode() == "ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('universe', choices=['nasdaq100', 'sp500', 'kospi'], nargs='?', default='nasdaq100')
    p.add_argument('--no-slack', action='store_true')
    args = p.parse_args()

    # 2026-09-26: 28~31일 매일 미완성 월봉으로 발송하던 문제 — 말일 미국장 마감 후에만 진행.
    import market_time as mt
    if not mt.should_run_monthly_scan():
        print('말일 미국장 마감 후가 아니라 스킵 (market_time.should_run_monthly_scan)')
        sys.exit(0)

    def _name(t):
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

    print(f'\n  {label} 원서 상승 패턴 스캔 중 (월봉)... ({len(universe)}종목)')
    results, counts = scan_bullish(universe, label)
    print_report(results, counts, label)
    if not args.no_slack:
        send_slack(results, counts, label)
