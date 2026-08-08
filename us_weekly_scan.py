# -*- coding: utf-8 -*-
"""
미국주식 월봉MA10 + 주봉MA10 이중 필터 스캔
  월봉MA10 위 (매크로 상승추세) + 주봉MA10 눌림목 (진입 타이밍)
"""
import sys, io, json, os, warnings
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
    CFG = json.load(f)

STOCKS   = CFG['stocks']
MA_MONTH = CFG['ma_period']   # 10
MA_WEEK  = 10

ZONE_PCT       = 5    # 주봉MA10 대비 이 % 이내 = 눌림목
BREAKOUT_PCT   = 10   # 이 % 이하 = 진입 고려 가능

def fetch(ticker, interval, period):
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open','High','Low','Close','Volume']].dropna()

def scan():
    rows = []
    total = len(STOCKS)
    for i, (ticker, (name, sector)) in enumerate(STOCKS.items(), 1):
        print(f'  {i:02d}/{total} {ticker}...', end='\r')
        try:
            # ── 월봉 MA10 ──────────────────────────────────────
            dm = fetch(ticker, '1mo', '3y')
            if len(dm) < MA_MONTH + 2:
                continue
            dm['MA'] = dm['Close'].rolling(MA_MONTH).mean()
            m_close = float(dm.iloc[-1]['Close'])
            m_ma    = float(dm.iloc[-1]['MA'])
            m_pct   = (m_close - m_ma) / m_ma * 100
            m_above = m_close > m_ma
            m_fresh = (float(dm.iloc[-2]['Close']) < float(dm.iloc[-2]['MA'])) and m_above

            if not m_above:
                continue   # 월봉MA10 아래 종목은 제외

            # ── 주봉 MA10 ──────────────────────────────────────
            dw = fetch(ticker, '1wk', '2y')
            if len(dw) < MA_WEEK + 2:
                continue
            dw['MA'] = dw['Close'].rolling(MA_WEEK).mean()
            w_close  = float(dw.iloc[-1]['Close'])
            w_ma     = float(dw.iloc[-1]['MA'])
            w_pct    = (w_close - w_ma) / w_ma * 100
            w_prev_c = float(dw.iloc[-2]['Close'])
            w_prev_m = float(dw.iloc[-2]['MA'])
            w_fresh  = (w_prev_c < w_prev_m) and (w_close > w_ma)

            # 주봉 거래량 (눌림 중 거래량 감소 = 건강한 눌림)
            avg_vol = float(dw['Volume'].iloc[-6:-1].mean()) or 1
            w_vol_r = float(dw.iloc[-1]['Volume']) / avg_vol

            # 적정매수단가(지지선=주봉MA10) / 손절가
            # 손절가는 항상 매수단가보다 낮아야 하므로, 최근5일 저가와
            # '지지선 -3%' 중 더 낮은 쪽을 사용 (갭이 큰 종목의 역전 방지)
            limit_buy = round(w_ma, 2)
            dd = fetch(ticker, '1d', '1mo')
            recent_low = float(dd['Low'].iloc[-5:].min())
            stop5 = round(min(recent_low, limit_buy * 0.97), 2)

            # ── 주봉 기준 등급 ─────────────────────────────────
            if w_fresh:
                w_grade = 1   # ★ 주봉 신규돌파
            elif w_close > w_ma and w_pct <= ZONE_PCT:
                w_grade = 2   # ▲ 주봉 눌림목 (MA10 5% 이내)
            elif w_close > w_ma and w_pct <= BREAKOUT_PCT:
                w_grade = 3   # ● 주봉 지지권 (5~10%)
            elif w_close > w_ma:
                w_grade = 4   # ◎ 주봉 추세중 (10% 초과)
            else:
                w_grade = 5   # △ 주봉MA10 아래 (단기 눌림)

            rows.append(dict(
                grade=w_grade, ticker=ticker, name=name, sector=sector,
                m_pct=round(m_pct,1), m_fresh=m_fresh,
                w_pct=round(w_pct,1), w_fresh=w_fresh,
                w_close=round(w_close,2), w_ma=round(w_ma,2),
                w_vol_r=round(w_vol_r,2),
                limit_buy=limit_buy, stop5=stop5,
            ))
        except Exception:
            continue

    rows.sort(key=lambda x: (x['grade'], x['w_pct']))
    return rows

def print_report(rows):
    now = datetime.today().strftime('%Y-%m-%d')
    W = 78
    print()
    print('=' * W)
    print(f'  미국주식 월봉MA10 × 주봉MA10 이중 필터 스캔   [{now}]')
    print(f'  월봉MA10 위 종목만 대상 | 주봉MA10 눌림목 순 정렬')
    print('=' * W)

    labels = {
        1: '★ 주봉 신규돌파  — 즉시 진입 검토',
        2: '▲ 주봉 눌림목    — 주봉MA10 +5% 이내 (최적 진입 자리)',
        3: '● 주봉 지지권    — +5~10%  (진입 고려)',
        4: '◎ 주봉 추세중    — +10% 초과 (눌림 대기)',
        5: '△ 주봉MA10 이탈  — 단기 추세 약화 (관망)',
    }

    HDR = f'  {"티커":<6} {"종목명":<16} {"섹터":<14} {"주봉현재가":>9} {"주봉MA10":>9} {"주봉대비":>8} {"월봉대비":>8} {"거래량":>6}'

    prev_grade = None
    for r in rows:
        if r['grade'] != prev_grade:
            print(f'\n  [{labels[r["grade"]]}]')
            print('  ' + '-' * (W-2))
            print(HDR)
            print('  ' + '-' * (W-2))
            prev_grade = r['grade']

        m_tag = '★' if r['m_fresh'] else ' '
        w_tag = '★' if r['w_fresh'] else ' '
        vol   = f'{r["w_vol_r"]:.1f}x' if r["w_vol_r"] else '-'
        print(f'  {w_tag}{m_tag}{r["ticker"]:<4} {r["name"]:<16} {r["sector"]:<14}'
              f' {r["w_close"]:>9,.2f} {r["w_ma"]:>9,.2f} {r["w_pct"]:>+7.1f}%'
              f' {r["m_pct"]:>+7.1f}%  {vol:>5}')
        if r['grade'] <= 3:
            print(f'        └ 적정매수단가(지지선) {r["limit_buy"]:>9,.2f}'
                  f'   손절가(5일저가) {r["stop5"]:>9,.2f}')

    buy_now = [r for r in rows if r['grade'] <= 2]
    print()
    print('=' * W)
    print(f'  요약: 즉시 진입 후보 {len(buy_now)}개 '
          f'(주봉 신규돌파 {sum(1 for r in rows if r["grade"]==1)}개 + '
          f'눌림목 {sum(1 for r in rows if r["grade"]==2)}개)')
    print('=' * W)

def send_slack(rows):
    with open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    url = cfg.get('slack_webhook_url', '')
    if not url:
        return

    import urllib.request, urllib.error

    buy_now = [r for r in rows if r['grade'] <= 2]
    watch   = [r for r in rows if r['grade'] == 3]

    blocks = []

    def _section(text):
        return {"type": "section", "text": {"type": "mrkdwn", "text": text}}
    def _divider():
        return {"type": "divider"}

    now = datetime.today().strftime('%Y-%m-%d')
    blocks.append(_section(f'*📊 미국주식 월봉×주봉 MA10 스캔* — {now}'))
    blocks.append(_section('_월봉MA10 위 종목 중 주봉MA10 눌림목 진입 후보_'))
    blocks.append(_divider())

    if buy_now:
        blocks.append(_section('*★ 즉시 진입 후보* — 주봉MA10 +5% 이내'))
        for r in buy_now:
            tag = '★ 신규돌파' if r['w_fresh'] else f'+{r["w_pct"]}%'
            blocks.append(_section(
                f'`{r["ticker"]}` *{r["name"]}*  |  주봉 {tag}  |  월봉 +{r["m_pct"]}%\n'
                f'　　적정매수단가 `${r["limit_buy"]:,.2f}`  |  손절가 `${r["stop5"]:,.2f}`'
            ))
        blocks.append(_divider())

    if watch:
        blocks.append(_section('*● 진입 고려* — 주봉MA10 +5~10%'))
        fields = [f'`{r["ticker"]}` {r["name"]}  주봉 +{r["w_pct"]}%' for r in watch]
        blocks.append({"type": "section", "fields":
            [{"type": "mrkdwn", "text": f} for f in fields[:8]]})
        blocks.append(_divider())

    blocks.append(_section(
        f'진입후보 *{len(buy_now)}개* | 관찰 *{len(watch)}개* | '
        f'월봉MA10 위 종목 *{len(rows)}개*'
    ))

    payload = json.dumps({"blocks": blocks}).encode('utf-8')
    req = urllib.request.Request(url, data=payload,
                                 headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
        print('  슬랙 전송 완료')
    except Exception as e:
        print(f'  슬랙 전송 실패: {e}')


if __name__ == '__main__':
    import sys as _sys
    slack_mode = len(_sys.argv) > 1 and _sys.argv[1] == 'slack'

    print('\n  스캔 중... (월봉+주봉 데이터 수집, 약 30초 소요)')
    rows = scan()
    print(' ' * 30, end='\r')
    print_report(rows)

    if slack_mode:
        send_slack(rows)
