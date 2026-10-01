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
import sys, json, os, uuid, warnings, time
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import yfinance as yf
import pandas as pd
import urllib.request
from datetime import datetime, date

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import market_time as mt

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


def _entry_month(s: dict) -> pd.Period:
    """신호가 기준으로 삼은 월봉. 월말 스캔은 말일(미국 기준) 밤~다음 달 1~2일(한국 날짜)에 기록되므로
    기록일에서 5일을 빼 그 달로 본다(예: 9/1 기록 = 8월 종가 신호, 8/31 기록 = 8월)."""
    return pd.Period(pd.Timestamp(s['date']) - pd.Timedelta(days=5), 'M')


def first_ma10_break(df: pd.DataFrame, entry_m: pd.Period):
    """진입 월부터 처음으로 월말 종가가 MA10 아래로 마감한 (월, 종가). 없으면 None.
    원서 매도 규칙 그대로: 보유 중 월말 종가 10이평 이탈 = 매도. df는 완성된 월봉만 넘길 것.
    진입 월도 포함: 월말 확정 신호는 그 달 종가가 10이평 위라 영향 없고, 예전 규칙으로 월 중간에 들어간
    기록(예: META 7/12)은 그 달 말 종가가 이미 아래면 그 달에 판 것으로 계산해야 한다."""
    ma = df['Close'].rolling(MA_PERIOD).mean()
    for ts, c, m in zip(df.index, df['Close'], ma):
        p = ts.to_period('M')
        if p >= entry_m and pd.notna(m) and float(c) < float(m):
            return p, float(c)
    return None


# ── 열린 신호 결과 업데이트 ───────────────────────────────────
def update_open_signals(notify: bool = True):
    signals = _load()
    open_sigs = [s for s in signals if s['status'] == 'open']
    if not open_sigs:
        print('[트래커] 열린 신호 없음')
        return

    updated, closed = [], []
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

            # 테스타 일봉: 목표/손절 체크 (테스타는 2026-09-26 중지 — 열린 테스타 기록이 있을 때만 해당)
            if s['strategy'] == '테스타일봉':
                if s['target'] and price >= s['target']:
                    _close_signal(s, price, '목표달성', notify=False)
                    closed.append(s)
                    updated.append(s)
                    continue
                if s['stop'] and price <= s['stop']:
                    _close_signal(s, price, '손절', notify=False)
                    closed.append(s)
                    updated.append(s)
                    continue

            # 월봉 MA10: 진입 이후 처음으로 월말 종가가 10이평 아래 마감한 달에, 그 달 종가로 청산(원서 매도 규칙).
            # 이력(2026-09-28·30 수정):
            #  - 예전엔 진행 중인 이번 달 잠정 종가로 판정 → 미확정 데이터로 청산(9/28 사고). 이제 완성된 월봉만 쓴다.
            #  - strategy exact-match('월봉MA10')라 "월봉MA10 돌파(원서)" 기록은 이 분기에 안 걸렸다 → startswith.
            #  - period='6mo'라 월봉 6개로는 MA10(10개월)이 안 나와 청산이 한 번도 기록되지 않았다(6월~9월 98건 전부 open).
            #  - "지난달 위 → 이번달 아래"만 봐서, 이미 이탈한 뒤 밀린 기록은 영원히 open으로 남았다 → 첫 이탈월 방식.
            if s['strategy'].startswith(('월봉MA10', '이슈섹터')):
                import monthly_data   # 야후 월봉은 직전 달 봉이 틀린다 → 일봉으로 만든 월봉(2026-10-01)
                df = monthly_data.history(ticker, '5y')
                df = df[['Close']].dropna()
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                # 완성된 월봉만: 이번 달 봉은 미국 말일 장 마감 후에만 포함
                if len(df) and not mt.is_monthend_after_close() and \
                        df.index[-1].to_period('M') == pd.Period(mt.us_ref_date(), 'M'):
                    df = df.iloc[:-1]
                brk = first_ma10_break(df, _entry_month(s))
                if brk:
                    month, close = brk
                    _close_signal(s, close, 'MA10이탈', exit_date=month.end_time.date().isoformat(),
                                  notify=False)
                    closed.append(s)
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
    print(f'[트래커] 업데이트 완료: {len(open_sigs)}개 열린 신호 점검, 청산 {len(closed)}건')
    if notify and closed:
        _notify_close_batch(closed)


def _notify_close_batch(closed: list):
    """월말 청산을 한 메시지로 — 건별로 수십 번 연달아 보내면 슬랙 웹훅 속도 제한(초당 1건)에 걸려
    일부가 조용히 누락될 수 있다(2026-09-30). 섹션당 3000자·메시지당 50블록 한도 안에서 나눈다."""
    closed = sorted(closed, key=lambda s: s['return_pct'])
    wins = [s for s in closed if s['status'] == 'win']
    lines = []
    for s in closed:
        icon = '🟢' if s['status'] == 'win' else '🔴'
        try:
            days = (date.fromisoformat(s['exit_date']) - date.fromisoformat(s['date'])).days
        except Exception:
            days = '?'
        lines.append(f"{icon} `{s['ticker']}` {s['name']}  {s['entry_price']:.2f}→{s['exit_price']:.2f}  "
                     f"*{s['return_pct']:+.1f}%*  {days}일  ({s['strategy']})")
    blocks = [{"type": "header", "text": {"type": "plain_text",
               "text": f"월말 청산 확정 {len(closed)}건 — 10이평 이탈"}},
              {"type": "section", "text": {"type": "mrkdwn",
               "text": f"수익 {len(wins)}건 / 손실 {len(closed) - len(wins)}건 · "
                       f"평균 {sum(s['return_pct'] for s in closed) / len(closed):+.1f}%  "
                       f"_(가상 추적 — 실제 매매 아님)_"}}]
    chunk = []
    for ln in lines:
        if sum(len(x) + 1 for x in chunk) + len(ln) > 2800:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": '\n'.join(chunk)}})
            chunk = []
        chunk.append(ln)
    if chunk:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": '\n'.join(chunk)}})
    for i in range(0, len(blocks), 50):
        _send(blocks[i:i + 50], f'월말 청산 {len(closed)}건')


def _close_signal(s: dict, exit_price: float, reason: str, exit_date: str = None, notify: bool = True):
    ret = (exit_price - s['entry_price']) / s['entry_price'] * 100
    s['status']      = 'win' if ret >= 0 else 'loss'
    s['exit_date']   = exit_date or date.today().isoformat()
    s['exit_price']  = round(float(exit_price), 2)
    s['return_pct']  = round(ret, 2)
    s['exit_reason'] = reason
    print(f'[트래커] 청산: {s["name"]}({s["ticker"]})  {ret:+.1f}%  ({reason}, {s["exit_date"]})')
    if notify:
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
    time.sleep(1.1)   # 슬랙 웹훅 속도 제한(초당 1건) — 연속 전송 시 429로 누락되지 않게(2026-09-30)


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


# ── 병합 (GitHub 워크플로우 저장용) ────────────────────────────
def merge_into_log(mine_path: str):
    """이번 실행의 기록(mine)을 저장소 최신본(LOG_PATH)에 id 기준으로 합친다.
    월말 밤 워크플로우 5개가 겹쳐 돌아도 서로의 기록을 덮어쓰지 않게 하기 위함(2026-09-30).
    같은 id면 청산된 쪽(status != open)을 우선, 둘 다 같으면 이번 실행 쪽을 쓴다."""
    base = {s['id']: s for s in _load()}
    with open(mine_path, encoding='utf-8') as f:
        mine = json.load(f).get('signals', [])
    for s in mine:
        b = base.get(s['id'])
        if b is None or not (b['status'] != 'open' and s['status'] == 'open'):
            base[s['id']] = s
    _save(list(base.values()))
    print(f'[트래커] 병합 완료: {len(base)}건')


# ── 메인 ──────────────────────────────────────────────────────
if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'update'

    if cmd == 'merge':
        merge_into_log(sys.argv[2])
        sys.exit(0)

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
