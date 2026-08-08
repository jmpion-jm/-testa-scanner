# -*- coding: utf-8 -*-
"""
실적 발표 경고 알림
- 매일 실행
- config.json 관심종목 + 구글시트 보유종목 대상
- 7일 이내 실적 발표 예정 시 슬랙 알림
"""
import sys, json, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import urllib.request
from datetime import datetime, date, timedelta

BASE    = os.path.dirname(os.path.abspath(__file__))
CFG     = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
WEBHOOK = CFG.get('slack_webhook_url', '')        # 기존 관심종목 채널
STOCKS  = CFG.get('stocks', {})                   # {ticker: [name, sector]}


def get_earnings_date(ticker: str) -> date | None:
    """yfinance로 다음 실적 발표일 조회"""
    try:
        t   = yf.Ticker(ticker)
        cal = t.calendar
        if cal is None or cal.empty:
            return None
        # calendar는 DataFrame, 컬럼: Earnings Date, Earnings High, Earnings Low 등
        if 'Earnings Date' in cal.index:
            val = cal.loc['Earnings Date']
            # 여러 날짜가 있을 수 있음 (범위)
            if hasattr(val, '__iter__') and not isinstance(val, str):
                dates = [pd.Timestamp(v).date() for v in val if pd.notna(v)]
            else:
                dates = [pd.Timestamp(val).date()]
            future = [d for d in dates if d >= date.today()]
            return min(future) if future else None
    except Exception:
        pass
    # 대안: fast_info
    try:
        info = yf.Ticker(ticker).fast_info
        if hasattr(info, 'next_fiscal_year_end'):
            return None
    except Exception:
        pass
    return None


def scan_earnings(days_ahead: int = 7) -> list:
    """days_ahead일 이내 실적 발표 종목 반환"""
    cutoff = date.today() + timedelta(days=days_ahead)
    results = []

    tickers = {t: (info[0], info[1]) for t, info in STOCKS.items()
               if not t.endswith('.KS')}   # 미국 종목만

    print(f'실적 발표 조회: {len(tickers)}개 종목 ({days_ahead}일 이내)')

    for ticker, (name, sector) in tickers.items():
        ed = get_earnings_date(ticker)
        if ed and date.today() <= ed <= cutoff:
            days_left = (ed - date.today()).days
            results.append({
                'ticker':    ticker,
                'name':      name,
                'sector':    sector,
                'date':      ed.isoformat(),
                'days_left': days_left,
            })
            print(f'  ★ {name}({ticker})  실적 발표 {ed}  ({days_left}일 후)')

    return sorted(results, key=lambda x: x['days_left'])


def send_slack(results: list):
    if not results or not WEBHOOK:
        return

    today = datetime.today().strftime('%Y.%m.%d')
    lines = []
    for r in results:
        urgency = '🔴' if r['days_left'] <= 1 else ('🟡' if r['days_left'] <= 3 else '🟢')
        lines.append(
            f'{urgency} `{r["ticker"]}` *{r["name"]}*  '
            f'📅 {r["date"]}  (*{r["days_left"]}일 후*)  _{r["sector"]}_'
        )

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"📊 실적 발표 예정 알림  {today}"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f'*7일 이내 실적 발표 예정 종목 {len(results)}개*\n'
                           f'_실적 쇼크 대비 — 발표 전 포지션 크기 점검 권고_')}},
        {"type": "divider"},
        {"type": "section",
         "text": {"type": "mrkdwn", "text": '\n'.join(lines)}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": ('_🔴 1일 이내  🟡 3일 이내  🟢 7일 이내\n'
                           '월봉 전략이라도 실적 쇼크는 -20~30% 발생 가능_')}},
    ]

    payload = json.dumps({'text': f'실적발표 알림 {today}', 'blocks': blocks},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print(f'Slack 전송: {"성공" if res.read().decode() == "ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


if __name__ == '__main__':
    print(f'\n실적 발표 경고 스캔  {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
    results = scan_earnings(days_ahead=7)
    print(f'\n총 {len(results)}개 종목 실적 발표 예정')
    if results:
        send_slack(results)
    else:
        print('7일 이내 실적 발표 없음 — 슬랙 전송 생략')
