# -*- coding: utf-8 -*-
"""
포트폴리오 수익률 리포트
- trade_log.xlsx 읽기 → 수익률 계산 → 슬랙 전송
실행: python portfolio_report.py
"""
import sys, json, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf
import urllib.request
from datetime import datetime

BASE    = os.path.dirname(os.path.abspath(__file__))
CFG     = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
WEBHOOK = CFG.get('slack_webhook_url', '')
LOG     = os.path.join(BASE, 'trade_log.xlsx')


# ── 거래내역 로드 ─────────────────────────────────────────────
def load_trades() -> pd.DataFrame:
    df = pd.read_excel(LOG, sheet_name='거래내역', dtype=str)
    df.columns = ['date','ticker','name','side','qty','price','currency','account','strategy','memo']
    # 예시 행(회색) 및 안내 행 제거
    df = df[df['side'].isin(['매수','매도'])].copy()
    df['date']   = pd.to_datetime(df['date'], errors='coerce')
    df['qty']    = pd.to_numeric(df['qty'],   errors='coerce').fillna(0)
    df['price']  = pd.to_numeric(df['price'], errors='coerce').fillna(0)
    return df.dropna(subset=['date','ticker'])


# ── 현재가 조회 ───────────────────────────────────────────────
def get_current_price(ticker: str, currency: str) -> float:
    try:
        t = ticker if currency == 'USD' else ticker + '.KS'
        info = yf.Ticker(t).fast_info
        return float(info.last_price)
    except Exception:
        return 0.0


# ── 보유 포지션 계산 (FIFO) ──────────────────────────────────
def calc_positions(df: pd.DataFrame) -> tuple[list, list]:
    """(보유포지션 list, 실현손익 list) 반환"""
    holdings = {}   # ticker → {'name','currency','account','strategy','lots': [(qty,price)]}
    realized = []

    for _, row in df.sort_values('date').iterrows():
        tk  = str(row['ticker']).strip()
        key = (tk, str(row['account']).strip())

        if row['side'] == '매수':
            if key not in holdings:
                holdings[key] = {
                    'ticker':   tk,
                    'name':     str(row['name']).strip(),
                    'currency': str(row['currency']).strip(),
                    'account':  str(row['account']).strip(),
                    'strategy': str(row['strategy']).strip(),
                    'lots':     [],
                }
            holdings[key]['lots'].append((row['qty'], row['price']))

        elif row['side'] == '매도' and key in holdings:
            sell_qty   = row['qty']
            sell_price = row['price']
            lots       = holdings[key]['lots']
            cost       = 0.0
            remaining  = sell_qty
            new_lots   = []
            for (bq, bp) in lots:
                if remaining <= 0:
                    new_lots.append((bq, bp))
                elif bq <= remaining:
                    cost      += bq * bp
                    remaining -= bq
                else:
                    cost      += remaining * bp
                    new_lots.append((bq - remaining, bp))
                    remaining  = 0
            holdings[key]['lots'] = new_lots
            pnl = (sell_price - cost / sell_qty) / (cost / sell_qty) * 100 if sell_qty else 0
            realized.append({
                'date':     row['date'].strftime('%Y-%m-%d'),
                'ticker':   tk,
                'name':     holdings[key]['name'],
                'strategy': holdings[key]['strategy'],
                'qty':      sell_qty,
                'entry':    round(cost / sell_qty, 2) if sell_qty else 0,
                'exit':     sell_price,
                'pnl_pct':  round(pnl, 2),
                'pnl_amt':  round((sell_price - cost / sell_qty) * sell_qty, 0) if sell_qty else 0,
            })
            if not holdings[key]['lots']:
                del holdings[key]

    # 보유 포지션 정리
    open_pos = []
    for key, h in holdings.items():
        if not h['lots']:
            continue
        total_qty  = sum(q for q, _ in h['lots'])
        total_cost = sum(q * p for q, p in h['lots'])
        avg_price  = total_cost / total_qty if total_qty else 0
        curr_price = get_current_price(h['ticker'], h['currency'])
        ret        = (curr_price - avg_price) / avg_price * 100 if avg_price else 0
        open_pos.append({
            'ticker':    h['ticker'],
            'name':      h['name'],
            'currency':  h['currency'],
            'account':   h['account'],
            'strategy':  h['strategy'],
            'qty':       total_qty,
            'avg_price': round(avg_price, 2),
            'curr_price':round(curr_price, 2),
            'ret_pct':   round(ret, 2),
            'cost':      round(total_cost, 0),
            'value':     round(curr_price * total_qty, 0),
            'pnl_amt':   round((curr_price - avg_price) * total_qty, 0),
        })

    return sorted(open_pos, key=lambda x: x['ret_pct'], reverse=True), realized


# ── 출력 ─────────────────────────────────────────────────────
def print_report(positions: list, realized: list):
    print(f'\n{"="*65}')
    print(f'  포트폴리오 수익률 리포트  {datetime.now().strftime("%Y-%m-%d")}')
    print(f'{"="*65}')

    if positions:
        print('\n  [보유 포지션]')
        print(f'  {"종목":<14} {"수량":>5} {"평균단가":>10} {"현재가":>10} {"수익률":>8} {"전략"}')
        print('  ' + '-'*62)
        for p in positions:
            curr = f"${p['curr_price']:.2f}" if p['currency']=='USD' else f"{p['curr_price']:,.0f}"
            avg  = f"${p['avg_price']:.2f}"  if p['currency']=='USD' else f"{p['avg_price']:,.0f}"
            icon = '▲' if p['ret_pct'] > 0 else '▽'
            print(f'  {p["name"]:<14} {p["qty"]:>5.0f} {avg:>10} {curr:>10} '
                  f'{icon}{p["ret_pct"]:>6.1f}%  {p["strategy"]}')

        total_cost  = sum(p['cost']  for p in positions)
        total_value = sum(p['value'] for p in positions)
        total_ret   = (total_value - total_cost) / total_cost * 100 if total_cost else 0
        print(f'\n  총 평가수익률: {total_ret:+.2f}%')

    if realized:
        print('\n  [실현 손익]')
        print(f'  {"날짜":<12} {"종목":<14} {"수익률":>8} {"손익금액":>12}')
        print('  ' + '-'*50)
        for r in sorted(realized, key=lambda x: x['date'], reverse=True)[:20]:
            icon = '✅' if r['pnl_pct'] >= 0 else '❌'
            amt  = f"+{r['pnl_amt']:,.0f}" if r['pnl_amt'] >= 0 else f"{r['pnl_amt']:,.0f}"
            print(f'  {r["date"]:<12} {r["name"]:<14} {icon}{r["pnl_pct"]:>6.1f}%  {amt}')

        wins   = [r for r in realized if r['pnl_pct'] >= 0]
        losses = [r for r in realized if r['pnl_pct'] < 0]
        wr     = len(wins) / len(realized) * 100 if realized else 0
        avg_w  = sum(r['pnl_pct'] for r in wins)   / len(wins)   if wins   else 0
        avg_l  = sum(r['pnl_pct'] for r in losses) / len(losses) if losses else 0
        print(f'\n  실현 승률: {wr:.1f}%  |  평균수익: {avg_w:+.1f}%  |  평균손실: {avg_l:+.1f}%')


# ── 슬랙 전송 ─────────────────────────────────────────────────
def send_slack(positions: list, realized: list):
    if not WEBHOOK:
        return
    today = datetime.now().strftime('%Y.%m.%d')

    pos_lines = []
    for p in positions:
        curr = f"${p['curr_price']:.1f}" if p['currency']=='USD' else f"{p['curr_price']:,.0f}원"
        avg  = f"${p['avg_price']:.1f}"  if p['currency']=='USD' else f"{p['avg_price']:,.0f}원"
        icon = '📈' if p['ret_pct'] > 0 else '📉'
        pos_lines.append(
            f'{icon} `{p["ticker"]}` *{p["name"]}*  {avg}→{curr}  *{p["ret_pct"]:+.1f}%*  [{p["account"]}]'
        )

    total_cost  = sum(p['cost']  for p in positions) if positions else 0
    total_value = sum(p['value'] for p in positions) if positions else 0
    total_ret   = (total_value - total_cost) / total_cost * 100 if total_cost else 0

    real_lines = []
    for r in sorted(realized, key=lambda x: x['date'], reverse=True)[:10]:
        icon = '✅' if r['pnl_pct'] >= 0 else '❌'
        real_lines.append(f'{icon} `{r["ticker"]}` {r["name"]}  *{r["pnl_pct"]:+.1f}%*  ({r["date"]})')

    wins = [r for r in realized if r['pnl_pct'] >= 0]
    wr   = len(wins) / len(realized) * 100 if realized else 0

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"💼 포트폴리오 수익률 리포트  {today}"}},
    ]

    if positions:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"*보유 포지션 {len(positions)}개  |  총 평가수익률 *{total_ret:+.2f}%*"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": '\n'.join(pos_lines)}})
        blocks.append({"type": "divider"})

    if realized:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"*실현 손익 {len(realized)}건  |  승률 {wr:.1f}%*"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": '\n'.join(real_lines)}})

    payload = json.dumps({'text': f'포트폴리오 리포트 {today}', 'blocks': blocks},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(WEBHOOK, data=payload,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print(f'Slack 전송: {"성공" if res.read().decode()=="ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


# ── 메인 ─────────────────────────────────────────────────────
if __name__ == '__main__':
    if not os.path.exists(LOG):
        print(f'trade_log.xlsx 없음: {LOG}')
        print('거래내역을 먼저 입력해 주세요.')
    else:
        df = load_trades()
        if df.empty:
            print('거래내역 없음 — trade_log.xlsx에 매수/매도 내역을 입력해 주세요.')
        else:
            positions, realized = calc_positions(df)
            print_report(positions, realized)
            send_slack(positions, realized)
