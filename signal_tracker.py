# -*- coding: utf-8 -*-
"""
전략 검증 트래커
- 신호 발생 시 자동 기록
- 매일 열린 신호 결과 업데이트
- 월간 검증 리포트 → 슬랙 전송

사용법:
  python signal_tracker.py update   # 열린 신호 결과 업데이트 + 슬랙 알림
  python signal_tracker.py report   # 월간 통계 리포트 전송
  python signal_tracker.py list     # 현재 열린 신호 목록 출력
"""
import sys, json, os, uuid, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import urllib.request
from datetime import datetime, date

BASE     = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, 'signal_log.json')
CFG      = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
WEBHOOK  = CFG.get('slack_webhook_url_tracker', '')
MA_PERIOD = CFG.get('ma_period', 10)


# ── 로그 파일 읽기/쓰기 ───────────────────────────────────────
def _load() -> list:
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, encoding='utf-8') as f:
        return json.load(f).get('signals', [])


def _save(signals: list):
    with open(LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump({'updated': date.today().isoformat(), 'signals': signals},
                  f, ensure_ascii=False, indent=2)


# ── 신호 기록 (외부에서 호출) ─────────────────────────────────
def record_signal(ticker: str, name: str, strategy: str,
                  entry_price: float, ma10: float,
                  stop: float = None, target: float = None,
                  signal_type: str = 'buy'):
    """
    신호 발생 시 기록.
    strategy: '월봉MA10' | '테스타일봉' | '이슈섹터'
    """
    signals = _load()

    # 같은 티커로 이미 열린 신호가 있으면 중복 기록 안 함
    open_tickers = {s['ticker'] for s in signals if s['status'] == 'open'}
    if ticker in open_tickers:
        print(f'[트래커] {ticker} 이미 열린 신호 있음 — 중복 기록 생략')
        return

    entry = {
        'id':           str(uuid.uuid4())[:8],
        'date':         date.today().isoformat(),
        'ticker':       ticker,
        'name':         name,
        'strategy':     strategy,
        'signal_type':  signal_type,
        'entry_price':  round(float(entry_price), 2),
        'ma10':         round(float(ma10), 2),
        'stop':         round(float(stop), 2) if stop else None,
        'target':       round(float(target), 2) if target else None,
        'status':       'open',
        'exit_date':    None,
        'exit_price':   None,
        'return_pct':   None,
        'exit_reason':  None,
        'peak_price':   round(float(entry_price), 2),
    }
    signals.append(entry)
    _save(signals)
    print(f'[트래커] 기록: {name}({ticker})  {strategy}  진입 {entry_price:.2f}')
    _notify_record(entry)


def record_sell_signal(ticker: str, name: str, strategy: str,
                       exit_price: float, reason: str = 'MA10이탈'):
    """매도 신호 발생 시 열린 신호 청산."""
    signals = _load()
    closed = 0
    for s in signals:
        if s['ticker'] == ticker and s['status'] == 'open':
            ret = (exit_price - s['entry_price']) / s['entry_price'] * 100
            s['status']      = 'win' if ret >= 0 else 'loss'
            s['exit_date']   = date.today().isoformat()
            s['exit_price']  = round(float(exit_price), 2)
            s['return_pct']  = round(ret, 2)
            s['exit_reason'] = reason
            closed += 1
            print(f'[트래커] 청산: {name}({ticker})  {ret:+.1f}%  ({reason})')
            _notify_close(s)
    if closed:
        _save(signals)


# ── 열린 신호 결과 업데이트 (매일 실행) ───────────────────────
def update_open_signals():
    signals = _load()
    open_sigs = [s for s in signals if s['status'] == 'open']
    if not open_sigs:
        print('[트래커] 열린 신호 없음')
        return

    updated = []
    for s in open_sigs:
        try:
            ticker = s['ticker']
            t      = yf.Ticker(ticker)

            # 현재가
            info  = t.fast_info
            price = float(info.last_price)

            # 고점 갱신
            peak = max(s.get('peak_price', s['entry_price']), price)
            s['peak_price'] = round(peak, 2)

            # 테스타 일봉: 목표/손절 체크
            if s['strategy'] == '테스타일봉':
                if s['target'] and price >= s['target']:
                    _close_signal(s, price, '목표달성')
                    updated.append(s)
                    continue
                if s['stop'] and price <= s['stop']:
                    _close_signal(s, price, '손절')
                    updated.append(s)
                    continue

            # 월봉 MA10: 이탈 체크
            if s['strategy'] in ('월봉MA10', '이슈섹터'):
                df = t.history(period='6mo', interval='1mo', auto_adjust=True)
                if not df.empty and len(df) >= MA_PERIOD:
                    df['MA10'] = df['Close'].rolling(MA_PERIOD).mean()
                    df = df.dropna()
                    if len(df) >= 2:
                        curr_close = float(df['Close'].iloc[-1])
                        curr_ma10  = float(df['MA10'].iloc[-1])
                        prev_close = float(df['Close'].iloc[-2])
                        prev_ma10  = float(df['MA10'].iloc[-2])
                        if prev_close > prev_ma10 and curr_close < curr_ma10:
                            _close_signal(s, curr_close, 'MA10이탈')
                            updated.append(s)
                            continue

            updated.append(s)

        except Exception as e:
            print(f'[트래커] {s["ticker"]} 업데이트 오류: {e}')
            updated.append(s)

    # 변경사항 반영
    for s in signals:
        for u in updated:
            if s['id'] == u['id']:
                s.update(u)
    _save(signals)
    print(f'[트래커] 업데이트 완료: {len(open_sigs)}개 열린 신호 점검')


def _close_signal(s: dict, exit_price: float, reason: str):
    ret = (exit_price - s['entry_price']) / s['entry_price'] * 100
    s['status']      = 'win' if ret >= 0 else 'loss'
    s['exit_date']   = date.today().isoformat()
    s['exit_price']  = round(float(exit_price), 2)
    s['return_pct']  = round(ret, 2)
    s['exit_reason'] = reason
    print(f'[트래커] 청산: {s["name"]}({s["ticker"]})  {ret:+.1f}%  ({reason})')
    _notify_close(s)


# ── 통계 계산 ──────────────────────────────────────────────────
def calc_stats(signals: list, strategy: str = None) -> dict:
    sigs = [s for s in signals if s['status'] != 'open']
    if strategy:
        sigs = [s for s in sigs if s['strategy'] == strategy]
    if not sigs:
        return {}

    total  = len(sigs)
    wins   = [s for s in sigs if s['status'] == 'win']
    losses = [s for s in sigs if s['status'] == 'loss']
    wr     = len(wins) / total * 100 if total else 0
    avg_w  = sum(s['return_pct'] for s in wins)  / len(wins)  if wins   else 0
    avg_l  = sum(s['return_pct'] for s in losses) / len(losses) if losses else 0
    ev     = (wr / 100 * avg_w) + ((100 - wr) / 100 * avg_l)

    open_sigs = [s for s in signals if s['status'] == 'open'
                 and (not strategy or s['strategy'] == strategy)]
    open_rets = []
    for s in open_sigs:
        try:
            price = float(yf.Ticker(s['ticker']).fast_info.last_price)
            open_rets.append((price - s['entry_price']) / s['entry_price'] * 100)
        except Exception:
            pass

    return {
        'total': total, 'open': len(open_sigs),
        'wins': len(wins), 'losses': len(losses),
        'win_rate': round(wr, 1),
        'avg_win': round(avg_w, 1),
        'avg_loss': round(avg_l, 1),
        'ev': round(ev, 2),
        'open_avg_ret': round(sum(open_rets) / len(open_rets), 1) if open_rets else 0,
    }


# ── 월간 리포트 ────────────────────────────────────────────────
def send_report():
    signals  = _load()
    today    = datetime.today().strftime('%Y.%m.%d')
    open_cnt = sum(1 for s in signals if s['status'] == 'open')

    strategies = ['월봉MA10', '테스타일봉', '이슈섹터']
    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"📊 전략 검증 리포트  {today}"}},
    ]

    any_data = False
    for strat in strategies:
        st = calc_stats(signals, strat)
        if not st or st['total'] == 0:
            continue
        any_data = True
        open_s = [s for s in signals if s['status'] == 'open' and s['strategy'] == strat]
        verdict = ('✅ 유효' if st['ev'] > 0 and st['win_rate'] >= 45
                   else '⚠️ 검증중' if st['total'] < 20
                   else '❌ 재검토필요')
        text = (
            f"*{strat}*  {verdict}\n"
            f"완료 {st['total']}건  |  진행중 {len(open_s)}건\n"
            f"승률 *{st['win_rate']}%*  |  평균수익 *{st['avg_win']:+.1f}%*  |  평균손실 {st['avg_loss']:+.1f}%\n"
            f"기대수익(EV) *{st['ev']:+.2f}%/거래*\n"
            f"진행중 평균 수익률: {st['open_avg_ret']:+.1f}%"
        )
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})
        blocks.append({"type": "divider"})

    if not any_data:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "아직 완료된 신호가 없습니다. 데이터가 쌓이면 통계가 표시됩니다."}})

    # 열린 신호 목록
    open_sigs = [s for s in signals if s['status'] == 'open']
    if open_sigs:
        lines = []
        for s in open_sigs:
            try:
                price = float(yf.Ticker(s['ticker']).fast_info.last_price)
                ret   = (price - s['entry_price']) / s['entry_price'] * 100
                lines.append(f'`{s["ticker"]}` {s["name"]}  진입 {s["entry_price"]:.1f} → 현재 {price:.1f}  *{ret:+.1f}%*  [{s["strategy"]}]')
            except Exception:
                lines.append(f'`{s["ticker"]}` {s["name"]}  진입 {s["entry_price"]:.1f}  [{s["strategy"]}]')
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*📂 열린 신호 {len(open_sigs)}건*\n" + '\n'.join(lines)}})

    _send(blocks, f'전략검증 리포트 {today}')


# ── 신호 기록 즉시 알림 ────────────────────────────────────────
def _notify_record(s: dict):
    stop_str   = f'  손절 {s["stop"]:.2f}' if s['stop'] else ''
    target_str = f'  목표 {s["target"]:.2f}' if s['target'] else ''
    text = (
        f"*📝 신호 기록*  `{s['ticker']}` {s['name']}\n"
        f"전략: {s['strategy']}  |  진입 *{s['entry_price']:.2f}*  |  MA10 {s['ma10']:.2f}"
        f"{stop_str}{target_str}\n"
        f"기록일: {s['date']}"
    )
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    _send(blocks, f"신호기록: {s['name']}")


# ── 청산 알림 ──────────────────────────────────────────────────
def _notify_close(s: dict):
    icon  = '🟢' if s['status'] == 'win' else '🔴'
    hold  = ''
    if s['exit_date'] and s['date']:
        try:
            days = (date.fromisoformat(s['exit_date']) - date.fromisoformat(s['date'])).days
            hold = f'  보유 {days}일'
        except Exception:
            pass
    text = (
        f"*{icon} 청산 확정*  `{s['ticker']}` {s['name']}\n"
        f"전략: {s['strategy']}  |  사유: {s['exit_reason']}\n"
        f"진입 {s['entry_price']:.2f} → 청산 {s['exit_price']:.2f}  "
        f"*{s['return_pct']:+.1f}%*{hold}"
    )
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    _send(blocks, f"청산: {s['name']} {s['return_pct']:+.1f}%")


# ── Slack 전송 ─────────────────────────────────────────────────
def _send(blocks: list, text: str = ''):
    if not WEBHOOK:
        return
    payload = json.dumps({'text': text, 'blocks': blocks},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            ok = res.read().decode() == 'ok'
            print(f'[트래커] 슬랙 전송: {"성공" if ok else "실패"}')
    except Exception as e:
        print(f'[트래커] 슬랙 오류: {e}')


# ── 목록 출력 ──────────────────────────────────────────────────
def print_list():
    signals  = _load()
    open_s   = [s for s in signals if s['status'] == 'open']
    closed_s = [s for s in signals if s['status'] != 'open']

    print(f'\n열린 신호 ({len(open_s)}건)')
    print('-' * 60)
    for s in open_s:
        print(f'  {s["date"]}  {s["ticker"]:<6} {s["name"]:<12} {s["strategy"]:<10} 진입 {s["entry_price"]:.2f}')

    print(f'\n완료 신호 ({len(closed_s)}건)')
    print('-' * 60)
    for s in closed_s:
        icon = '✅' if s['status'] == 'win' else '❌'
        print(f'  {icon} {s["exit_date"]}  {s["ticker"]:<6} {s["name"]:<12} {s["return_pct"]:+.1f}%  ({s["exit_reason"]})')

    for strat in ['월봉MA10', '테스타일봉', '이슈섹터']:
        st = calc_stats(signals, strat)
        if st and st['total'] > 0:
            print(f'\n[{strat}] 완료 {st["total"]}건  승률 {st["win_rate"]}%  EV {st["ev"]:+.2f}%')


# ── 메인 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'update'

    if cmd == 'update':
        print(f'\n열린 신호 업데이트  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        update_open_signals()

    elif cmd == 'report':
        print(f'\n검증 리포트 생성  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        send_report()

    elif cmd == 'list':
        print_list()

    elif cmd == 'test':
        # 테스트: 샘플 신호 기록
        record_signal('MU', '마이크론', '월봉MA10', 94.0, 92.1)
        record_signal('IONQ', '아이온큐', '테스타일봉', 18.5, 15.2, stop=14.0, target=25.0)
        send_report()
