# -*- coding: utf-8 -*-
"""
테스타 아침 진입 확인 알림
- 매일 09:10 자동 실행
- 전날 신호 종목의 오늘 시가 확인
- 진입가 ±3% 이내이면 ✅ 진입 가능 / 초과이면 ❌ 패스
"""
import os, sys, io, json, requests
from datetime import datetime, timedelta, date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    from pykrx import stock as krx
except ImportError:
    print("pykrx 미설치: pip install pykrx")
    sys.exit(1)

# ── 설정 ──────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
SIG_FILE  = os.path.join(BASE_DIR, 'testa_signals.json')
CFG_FILE  = os.path.join(BASE_DIR, 'config.json')
ENTRY_TOL = 3.0   # 시가 허용 범위 ±3%

with open(CFG_FILE, encoding='utf-8') as f:
    SLACK_URL = json.load(f).get('slack_webhook_url', '')


def get_open_price(ticker: str) -> int | None:
    """오늘 시가 조회"""
    today = datetime.today().strftime('%Y%m%d')
    try:
        df = krx.get_market_ohlcv_by_date(today, today, ticker)
        if df.empty:
            return None
        col = '시가' if '시가' in df.columns else df.columns[0]
        return int(df[col].iloc[-1])
    except Exception:
        return None


def send_slack(blocks: list, text: str):
    if not SLACK_URL or SLACK_URL.startswith('여기에'):
        print('[Slack 미설정]')
        return
    payload = json.dumps({'text': text, 'blocks': blocks}, ensure_ascii=False)
    try:
        r = requests.post(SLACK_URL, data=payload.encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, timeout=15)
        print(f'Slack 전송: {"성공" if r.text == "ok" else f"실패({r.text})"}')
    except Exception as e:
        print(f'Slack 전송 오류: {e}')


def main():
    today_str = datetime.today().strftime('%Y.%m.%d (%a)')
    print(f'\n테스타 아침 진입 확인  {today_str}')

    # 신호 파일 읽기
    if not os.path.exists(SIG_FILE):
        print('신호 파일 없음 — 전날 신호가 없었습니다.')
        return

    with open(SIG_FILE, encoding='utf-8') as f:
        data = json.load(f)

    sig_date = data.get('date', '')
    signals  = data.get('signals', [])

    # 전날 신호인지 확인 (주말/휴일 고려: 최근 3거래일 이내)
    today      = date.today()
    sig_parsed = datetime.strptime(sig_date, '%Y-%m-%d').date()
    days_ago   = (today - sig_parsed).days

    if days_ago > 3:
        print(f'신호가 {days_ago}일 전 것 — 너무 오래돼서 건너뜁니다.')
        return

    if not signals:
        print('전날 매수 신호 없음 — 오늘 진입 대상 없습니다.')
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"테스타 진입 확인  {today_str}"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": f"_전날({sig_date}) 매수 신호 없음 — 오늘 진입 대상 없습니다._"}}
        ]
        send_slack(blocks, f'테스타진입확인 {today_str}')
        return

    # 시가 확인
    results = []
    for s in signals:
        ticker = s['ticker']
        name   = s['name']
        entry  = s['entry']
        stop   = s['stop']
        target = s['target']
        risk   = s['risk_pct']

        open_p = get_open_price(ticker)
        if open_p is None:
            results.append({**s, 'open': None, 'gap': None, 'ok': None})
            continue

        gap_pct = (open_p - entry) / entry * 100
        ok      = abs(gap_pct) <= ENTRY_TOL

        results.append({
            **s,
            'open': open_p,
            'gap': round(gap_pct, 1),
            'ok': ok,
        })

        icon = '✅' if ok else '❌'
        reason = f'진입 가능 ({open_p:,}원)' if ok else f'갭 과다 ({gap_pct:+.1f}%), 오늘 패스'
        print(f'  {icon} {name}({ticker})  진입가 {entry:,} → 시가 {open_p:,}  ({gap_pct:+.1f}%)  {reason}')

    # Slack 블록 구성
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"테스타 진입 확인  {today_str} 09:10"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"_전날 신호 {len(signals)}종목 | 시가 ±{ENTRY_TOL}% 이내만 진입_"}},
        {"type": "divider"},
    ]

    can_enter  = [r for r in results if r['ok'] is True]
    cant_enter = [r for r in results if r['ok'] is False]
    no_data    = [r for r in results if r['ok'] is None]

    for r in can_enter:
        lo = int(r['entry'] * (1 - ENTRY_TOL / 100))
        hi = int(r['entry'] * (1 + ENTRY_TOL / 100))
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn",
                 "text": f"✅ *{r['name']}* (`{r['ticker']}`)  진입 가능"},
                {"type": "mrkdwn",
                 "text": f"시가  *{r['open']:,}원*  ({r['gap']:+.1f}%)"},
                {"type": "mrkdwn",
                 "text": f"매수 범위  {lo:,} ~ {hi:,}원"},
                {"type": "mrkdwn",
                 "text": f"손절  {r['stop']:,}원  |  목표  {r['target']:,}원"},
            ]
        })
        blocks.append({"type": "divider"})

    for r in cant_enter:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"❌ *{r['name']}* (`{r['ticker']}`)  갭 {r['gap']:+.1f}% — 오늘 진입 패스"}
        })

    for r in no_data:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"⚠️ *{r['name']}* (`{r['ticker']}`)  시가 데이터 없음 — 직접 확인"}
        })

    if can_enter:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_손절가 이탈 시 즉시 매도. 1회 리스크 = 계좌의 1~2% 이하_"}
        })

    send_slack(blocks, f'테스타진입확인 {today_str}')


if __name__ == '__main__':
    main()
