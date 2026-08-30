# -*- coding: utf-8 -*-
"""
관리종목 실적발표일 알림
  - config.json의 stocks 전체를 스캔해서, 앞으로 N일 이내 실적발표 예정 종목을 슬랙으로 알림
  - 매매법 신호(월봉MA10)와는 별개의 정보성 알림 — 실적 전후 변동성을 미리 이해하기 위한 용도

사용법: python earnings_calendar.py [slack]
"""
import sys, io, json, os, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)

STOCKS   = CFG['stocks']
ALERT_URL = CFG.get('slack_webhook_url', '')
WINDOW_DAYS = 10  # 이 기간 이내 실적발표 예정만 알림 (매주 점검이라 주기(7일)보다 여유있게 잡아 누락 방지)


def find_upcoming_earnings():
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=WINDOW_DAYS)
    results = []
    total = len(STOCKS)

    for i, (ticker, info) in enumerate(STOCKS.items(), 1):
        name = info[0] if isinstance(info, list) else str(info)
        print(f'  {i:02d}/{total} {ticker}...', end='\r')
        try:
            ed = yf.Ticker(ticker).get_earnings_dates(limit=4)
            if ed is None or ed.empty:
                continue
            # 리포트된 실적(Reported EPS 존재)은 이미 지난 것 — 미래 예정만 필터
            future = ed[ed.index >= now]
            if future.empty:
                continue
            next_date = future.index.min()
            if next_date <= cutoff:
                results.append((next_date, ticker, name))
        except Exception:
            continue

    print(' ' * 30, end='\r')
    results.sort(key=lambda x: x[0])
    return results


def send_slack(results):
    if not ALERT_URL:
        print('[슬랙 미설정] 콘솔 출력만')
        return
    if not results:
        print('7일 이내 실적발표 예정 종목 없음 — 전송 스킵')
        return

    import urllib.request

    lines = []
    for dt, ticker, name in results:
        d_str = dt.strftime('%m/%d(%a)')
        lines.append(f'• `{ticker}` {name} — {d_str}')

    blocks = [
        {"type": "header", "text": {"type": "plain_text",
         "text": f"📅 실적발표 예정 (앞으로 {WINDOW_DAYS}일 이내)"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": "\n".join(lines)}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": "_매매법 신호와 별개의 정보성 알림 — 실적 전후 변동성 참고용_"}]},
    ]
    payload = json.dumps({"text": "실적발표 예정 알림", "blocks": blocks},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(ALERT_URL, data=payload,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            ok = res.read().decode() == 'ok'
            print(f'슬랙 전송: {"성공" if ok else "실패"}')
            if not ok:
                print('::error::실적캘린더 슬랙 전송 실패')
                sys.exit(1)
    except Exception as e:
        print(f'슬랙 전송 오류: {e}')
        print('::error::실적캘린더 슬랙 전송 예외')
        sys.exit(1)


if __name__ == '__main__':
    slack_mode = len(sys.argv) > 1 and sys.argv[1] == 'slack'

    print(f'\n실적발표일 스캔 시작 (관리종목 {len(STOCKS)}개, {datetime.now().strftime("%Y-%m-%d %H:%M")})')
    results = find_upcoming_earnings()

    print(f'\n{WINDOW_DAYS}일 이내 실적발표 예정: {len(results)}건')
    for dt, ticker, name in results:
        print(f'  {ticker}({name}) — {dt.strftime("%Y-%m-%d %H:%M %Z")}')

    if slack_mode:
        send_slack(results)
