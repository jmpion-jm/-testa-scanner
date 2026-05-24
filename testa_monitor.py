# -*- coding: utf-8 -*-
"""
테스타 장중 모니터링
- 09:30 한 번 실행 → 15:30까지 10분마다 체크
- 손절가 이탈 시 🚨 즉시 알림
- 목표가 도달 시 🎯 알림
- 한 번 알림 보낸 종목은 중복 알림 안 함
"""
import os, sys, io, json, time, requests
from datetime import datetime, date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    from pykrx import stock as krx
except ImportError:
    print("pykrx 미설치")
    sys.exit(1)

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SIG_FILE    = os.path.join(BASE_DIR, 'testa_signals.json')
STATE_FILE  = os.path.join(BASE_DIR, 'testa_monitor_state.json')
CFG_FILE    = os.path.join(BASE_DIR, 'config.json')

CHECK_INTERVAL = 10   # 분
MARKET_OPEN    = (9, 30)
MARKET_CLOSE   = (15, 30)

with open(CFG_FILE, encoding='utf-8') as f:
    SLACK_URL = json.load(f).get('slack_webhook_url', '')


def get_current_price(ticker: str) -> int | None:
    today = date.today().strftime('%Y%m%d')
    try:
        df = krx.get_market_ohlcv_by_date(today, today, ticker)
        if df.empty:
            return None
        # 장중에는 종가 컬럼이 현재가(마지막 체결가)
        col = '종가' if '종가' in df.columns else df.columns[3]
        return int(df[col].iloc[-1])
    except Exception:
        return None


def send_slack(text: str, blocks: list):
    if not SLACK_URL or SLACK_URL.startswith('여기에'):
        print(text)
        return
    payload = json.dumps({'text': text, 'blocks': blocks}, ensure_ascii=False)
    try:
        r = requests.post(SLACK_URL, data=payload.encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, timeout=10)
        print(f'Slack 전송: {"성공" if r.text == "ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


def load_signals() -> list:
    if not os.path.exists(SIG_FILE):
        return []
    with open(SIG_FILE, encoding='utf-8') as f:
        data = json.load(f)
    sig_date = data.get('date', '')
    # 오늘 또는 어제(전날) 신호만 유효
    try:
        sig_d = datetime.strptime(sig_date, '%Y-%m-%d').date()
        if (date.today() - sig_d).days > 1:
            return []
    except Exception:
        return []
    return data.get('signals', [])


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {'date': str(date.today()), 'alerted': []}
    with open(STATE_FILE, encoding='utf-8') as f:
        state = json.load(f)
    # 날짜 바뀌면 초기화
    if state.get('date') != str(date.today()):
        return {'date': str(date.today()), 'alerted': []}
    return state


def save_state(state: dict):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False)


def check_once(signals: list, state: dict) -> dict:
    now_str = datetime.now().strftime('%H:%M')

    for s in signals:
        ticker  = s['ticker']
        name    = s['name']
        stop    = s['stop']
        target  = s['target']
        entry   = s['entry']

        alert_key_stop   = f'{ticker}_stop'
        alert_key_target = f'{ticker}_target'

        # 이미 알림 보낸 항목 스킵
        if alert_key_stop in state['alerted'] and alert_key_target in state['alerted']:
            continue

        price = get_current_price(ticker)
        if price is None:
            print(f'  {name}: 데이터 없음')
            continue

        pct = (price - entry) / entry * 100
        print(f'  {name}({ticker})  현재가 {price:,}  진입 {entry:,}  ({pct:+.1f}%)'
              f'  손절 {stop:,}  목표 {target:,}')

        # 손절 알림
        if price <= stop and alert_key_stop not in state['alerted']:
            blocks = [
                {"type": "header",
                 "text": {"type": "plain_text",
                          "text": f"🚨 손절 알림  {now_str}"}},
                {"type": "section",
                 "fields": [
                     {"type": "mrkdwn", "text": f"*{name}* (`{ticker}`)"},
                     {"type": "mrkdwn", "text": f"현재가  *{price:,}원*"},
                     {"type": "mrkdwn", "text": f"손절가  {stop:,}원 이탈"},
                     {"type": "mrkdwn", "text": f"수익률  *{pct:+.1f}%*"},
                 ]},
                {"type": "section",
                 "text": {"type": "mrkdwn",
                          "text": "*➡️ 즉시 시장가 매도하세요. 예외 없음.*"}},
            ]
            send_slack(f'🚨 손절 {name} {price:,}원', blocks)
            state['alerted'].append(alert_key_stop)

        # 목표가 알림
        elif price >= target and alert_key_target not in state['alerted']:
            profit_pct = (target - entry) / entry * 100
            blocks = [
                {"type": "header",
                 "text": {"type": "plain_text",
                          "text": f"🎯 목표가 도달  {now_str}"}},
                {"type": "section",
                 "fields": [
                     {"type": "mrkdwn", "text": f"*{name}* (`{ticker}`)"},
                     {"type": "mrkdwn", "text": f"현재가  *{price:,}원*"},
                     {"type": "mrkdwn", "text": f"목표가  {target:,}원 도달"},
                     {"type": "mrkdwn", "text": f"수익률  *+{profit_pct:.1f}%* 🎉"},
                 ]},
                {"type": "section",
                 "text": {"type": "mrkdwn",
                          "text": "➡️ 지정가 또는 시장가로 매도하세요."}},
            ]
            send_slack(f'🎯 목표달성 {name} {price:,}원', blocks)
            state['alerted'].append(alert_key_target)

    return state


def is_market_open() -> bool:
    now = datetime.now()
    start = now.replace(hour=MARKET_OPEN[0],  minute=MARKET_OPEN[1],  second=0)
    end   = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0)
    return start <= now <= end


def main():
    print(f'\n테스타 장중 모니터링 시작  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  체크 주기: {CHECK_INTERVAL}분  |  종료: {MARKET_CLOSE[0]}:{MARKET_CLOSE[1]:02d}')

    signals = load_signals()
    if not signals:
        print('  모니터링 대상 신호 없음 — 종료')
        return

    print(f'  모니터링 종목: {", ".join(s["name"] for s in signals)}')

    state = load_state()

    while is_market_open():
        now_str = datetime.now().strftime('%H:%M')
        print(f'\n[{now_str}] 가격 확인 중...')
        state = check_once(signals, state)
        save_state(state)

        # 다음 체크까지 대기 (장 마감이면 종료)
        for _ in range(CHECK_INTERVAL * 6):  # 10분을 10초씩 분할
            time.sleep(10)
            if not is_market_open():
                break

    print(f'\n  {datetime.now().strftime("%H:%M")} 장 마감 — 모니터링 종료')


if __name__ == '__main__':
    main()
