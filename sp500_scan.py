# -*- coding: utf-8 -*-
"""
S&P 500 전체 월봉 MA10 스캔
월 1회 실행 → 진입 가능 종목 리스트 출력 + Slack 전송

⚠️ 2026-09-02 경고: 이 스크립트는 월봉만 확인하고 주봉MA10 눌림목은
검증하지 않는다. "실행/검토 타임라인"은 확정 매수신호가 아니라 참고용
후보다 — 실제 매수 판단 전 `us_weekly_scan.py`(월봉+주봉 이중조건) 결과와
반드시 교차 확인할 것.
"""
import sys, json, os, time, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf
import urllib.request, urllib.error

# ── 설정 ─────────────────────────────────────────────────────
BASE    = os.path.dirname(os.path.abspath(__file__))
try:
    CFG       = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
    WEBHOOK   = CFG.get('slack_webhook_url_sp500', '')
    MA_PERIOD = CFG.get('ma_period', 10)
except Exception:
    WEBHOOK   = os.environ.get('SLACK_SP500_WEBHOOK_URL', '')
    MA_PERIOD = 10
ENTRY_LIMIT = 15.0   # 추세권 상한 (MA10 대비 +15% 이내)

# ── AI 관련 종목 판별 ────────────────────────────────────────
_AI_KEYWORDS = [
    'artificial intelligence', ' ai ', 'ai-', 'machine learning',
    'generative ai', 'neural network', 'deep learning',
]
_AI_INDUSTRY_HINTS = [
    'semiconductor', 'software - infrastructure', 'software - application',
    'information technology services', 'internet content',
    'computer hardware', 'data processing',
]

def is_ai_related(ticker: str) -> bool:
    """섹터/업종/사업설명 기반 AI 관련 여부 대략 판별 (완벽하지 않음, 참고용)"""
    try:
        info = yf.Ticker(ticker).info
        sector   = (info.get('sector') or '').lower()
        industry = (info.get('industry') or '').lower()
        summary  = (info.get('longBusinessSummary') or '').lower()

        if any(h in industry for h in _AI_INDUSTRY_HINTS) and sector == 'technology':
            if any(k in summary for k in _AI_KEYWORDS):
                return True
        if any(k in summary[:2000] for k in _AI_KEYWORDS):
            return True
        return False
    except Exception:
        return False


# ── S&P 500 티커 목록 ─────────────────────────────────────────
def get_sp500_tickers(retries: int = 3) -> list:
    """Wikipedia에서 S&P 500 구성 종목 가져오기 (일시적 실패 대비 재시도)"""
    import requests, time, traceback
    from io import StringIO

    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            tables = pd.read_html(StringIO(resp.text))
            df = tables[0]
            tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
            print(f'S&P 500 티커 로드 완료: {len(tickers)}개')
            return tickers
        except Exception as e:
            print(f'Wikipedia 로드 실패 ({attempt}/{retries}회차): {type(e).__name__}: {e}')
            traceback.print_exc()
            if attempt < retries:
                time.sleep(5 * attempt)
    return []


# ── 월봉 MA10 스캔 ────────────────────────────────────────────
def scan_sp500(tickers: list) -> list:
    results = []
    total   = len(tickers)
    errors  = 0

    print(f'\n스캔 시작: {total}개 종목')
    print('=' * 60)

    # yfinance 배치 다운로드 (전체 한 번에)
    print('월봉 데이터 다운로드 중... (약 3~5분 소요)')
    try:
        raw = yf.download(
            tickers,
            period='3y',
            interval='1mo',
            auto_adjust=True,
            group_by='ticker',
            progress=False,
            threads=True
        )
    except Exception as e:
        print(f'배치 다운로드 오류: {e}')
        return []

    print('다운로드 완료. 분석 중...')

    for i, ticker in enumerate(tickers):
        try:
            # group_by='ticker' → raw[ticker]['Close'] 구조
            if ticker not in raw.columns.get_level_values(0):
                continue
            close_series = raw[ticker]['Close'].dropna()
            if close_series.empty:
                continue
            df = close_series.to_frame(name='Close')
            df = df.dropna()
            if len(df) < MA_PERIOD + 2:
                continue

            df['MA10'] = df['Close'].rolling(MA_PERIOD).mean()
            df = df.dropna()

            curr  = df.iloc[-1]
            prev  = df.iloc[-2]
            prev2 = df.iloc[-3]

            close = float(curr['Close'])
            ma10  = float(curr['MA10'])
            pct   = (close - ma10) / ma10 * 100

            above = close > ma10
            if not above:
                continue  # MA10 아래 → 스킵

            # 신규 돌파 여부 (지난달 MA10 아래 → 이번달 위)
            fresh = (float(prev['Close']) < float(prev['MA10'])) and above

            # 3개월 연속 하락 여부
            decline3 = (float(df.iloc[-1]['Close']) < float(df.iloc[-2]['Close']) <
                        float(df.iloc[-3]['Close']))

            # 백테스트 최적화 결과: 신규돌파는 괴리율 무제한이 최고 성과
            if fresh:
                signal = '★★ 신규돌파'   # 괴리율 무제한 — 성승현 원본
                priority = 1
            elif not fresh and pct <= 5:
                signal = '★ 지지권'       # 정보용 (매수 참고)
                priority = 2
            elif not fresh and 5 < pct <= 30:
                signal = '● 추세권'
                priority = 3
            elif pct > 30:
                signal = '△ 고점권'
                priority = 4
            else:
                continue

            results.append({
                'ticker'   : ticker,
                'close'    : round(close, 2),
                'ma10'     : round(ma10, 2),
                'pct'      : round(pct, 1),
                'signal'   : signal,
                'priority' : priority,
                'fresh'    : fresh,
                'decline3' : decline3,
            })

        except Exception:
            errors += 1
            continue

        if (i + 1) % 50 == 0:
            print(f'  {i+1}/{total} 처리 완료...')

    print(f'\n스캔 완료: {len(results)}개 신호 (오류: {errors}개)')
    return results


# ── 결과 출력 ─────────────────────────────────────────────────
def print_results(results: list):
    if not results:
        print('진입 가능 종목 없음')
        return

    df = pd.DataFrame(results)
    df = df.sort_values(['priority', 'pct'])

    print(f'\n{"="*65}')
    print(f'  S&P 500 월봉 MA10 스캔 결과')
    print(f'  진입 가능 종목: {len(df[df["priority"] <= 3])}개')
    print(f'{"="*65}')

    for sig, group in df.groupby('signal', sort=False):
        if group.empty:
            continue
        print(f'\n[ {sig} ] — {len(group)}종목')
        print(f'  {"티커":<8} {"현재가":>8} {"MA10":>8} {"괴리율":>7}  {"주의"}')
        print(f'  {"-"*50}')
        for _, r in group.iterrows():
            warn = '⚠️연속하락' if r['decline3'] else ''
            print(f'  {r["ticker"]:<8} ${r["close"]:>7.2f}  ${r["ma10"]:>7.2f}  {r["pct"]:>+6.1f}%  {warn}')


# ── CSV 저장 ──────────────────────────────────────────────────
def save_csv(results: list):
    if not results:
        return
    path = os.path.join(BASE, 'sp500_scan_result.csv')
    pd.DataFrame(results).sort_values(['priority', 'pct']).to_csv(
        path, index=False, encoding='utf-8-sig'
    )
    print(f'\nCSV 저장: {path}')


# ── Slack 전송 ────────────────────────────────────────────────
def send_slack(results: list):
    if not WEBHOOK or not results:
        return

    from datetime import datetime
    df  = pd.DataFrame(results).sort_values(['priority', 'pct'])
    today = datetime.today().strftime('%Y.%m.%d')

    fresh = df[df['priority'] == 1]           # ★★ 신규돌파
    dip   = df[df['priority'] == 2].head(10)  # ★ 지지권 상위 10
    trend = df[df['priority'] == 3].head(10)  # ● 추세권 상위 10

    def fmt(r):
        warn = ' ⚠️' if r['decline3'] else ''
        return f'`{r["ticker"]}`  ${r["close"]:.2f}  *{r["pct"]:+.1f}%*{warn}'

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"S&P 500 월봉 MA10 스캔  {today}"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f'진입 가능: *{len(df[df["priority"]<=3])}종목* '
                           f'(신규돌파 {len(fresh)} / 지지권 {len(df[df["priority"]==2])} / 추세권 {len(df[df["priority"]==3])})\n'
                           f'_CSV 저장 완료 — 전체 목록은 sp500_scan_result.csv 확인_')}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": "⚠️ *월봉만 확인 — 주봉MA10 눌림목 미검증.* 매수 전 "
                 "`us_weekly_scan.py` 결과와 교차 확인하세요."}]},
        {"type": "divider"},
    ]

    ai_tickers = [r['ticker'] for _, r in df.head(30).iterrows() if is_ai_related(r['ticker'])]
    if ai_tickers:
        ai_df = df[df['ticker'].isin(ai_tickers)]
        lines = '\n'.join(fmt(r) for _, r in ai_df.iterrows())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*🤖 AI 관련 종목만 — {len(ai_df)}종목*\n{lines}"}})
        blocks.append({"type": "divider"})

    if not fresh.empty:
        lines = '\n'.join(fmt(r) for _, r in fresh.iterrows())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": f"*★★ 신규 돌파 — {len(fresh)}종목*\n{lines}"}})
        blocks.append({"type": "divider"})

    if not dip.empty:
        lines = '\n'.join(fmt(r) for _, r in dip.iterrows())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": f"*★ 지지권 (MA10 +5% 이내) — 상위 10종목*\n{lines}"}})
        blocks.append({"type": "divider"})

    if not trend.empty:
        lines = '\n'.join(fmt(r) for _, r in trend.iterrows())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": f"*● 추세권 (MA10 +5~15%, 보유 유지/신규 진입 주의) — 상위 10종목*\n{lines}"}})

    # ── 실행 타임라인 ──
    action_rows = pd.concat([fresh, dip]).sort_values('pct').head(5)
    if not action_rows.empty:
        header = f'{"시간":<9} {"계좌":<10} 행동'
        sep    = '─' * 55
        tl     = []
        for _, r in action_rows.iterrows():
            sig  = '신규돌파' if r['fresh'] else '지지권'
            warn = ' ⚠️연속하락' if r['decline3'] else ''
            tl.append(f'{"22:30~":<9} {"미래에셋":<10} 🟡 {r["ticker"]} ${r["close"]:.2f}  검토후보 ({sig} {r["pct"]:+.1f}%){warn}')
        table = f'```\n{header}\n{sep}\n' + '\n'.join(tl) + '\n```'
        blocks.append({"type": "divider"})
        blocks.append({"type": "header",
                       "text": {"type": "plain_text", "text": "📋 검토 타임라인 (미국 시장 22:30~) — 매수확정 아님"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": table}})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "_⚠️ 이 목록은 매수신호가 아니라 검토후보입니다. 주봉 눌림목 미검증 — "
                                        "`us_weekly_scan.py`로 재확인 후 진입 판단하세요._"}})

    payload = json.dumps({'text': f'SP500 스캔 {today}', 'blocks': blocks},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        WEBHOOK, data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print(f'Slack 전송: {"성공" if res.read().decode()=="ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


# ── 메인 ─────────────────────────────────────────────────────
def save_signals(results: list):
    """신규돌파 + 지지권 종목을 sp500_signals.json 으로 저장"""
    from datetime import datetime
    signals = [
        {'ticker': r['ticker'], 'name': r['ticker'],
         'pct': r['pct'], 'priority': r['priority']}
        for r in results if r['priority'] <= 2
    ]
    path = os.path.join(BASE, 'sp500_signals.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'date': datetime.today().strftime('%Y-%m-%d'), 'signals': signals},
                  f, ensure_ascii=False, indent=2)
    print(f'신호 저장: {len(signals)}종목 → sp500_signals.json')


if __name__ == '__main__':
    from datetime import datetime
    print(f'\nS&P 500 월봉 MA10 스캔  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'기준: MA{MA_PERIOD} | 추세권 +{ENTRY_LIMIT}% 이내\n')

    tickers = get_sp500_tickers()
    if not tickers:
        sys.exit(1)

    results = scan_sp500(tickers)
    print_results(results)
    save_csv(results)
    save_signals(results)
    send_slack(results)

    # 트래커 연동 — 신규돌파 자동 기록
    try:
        sys.path.insert(0, BASE)
        import signal_tracker as tracker
        for r in results:
            if r.get('fresh'):
                tracker.record_signal(r['ticker'], r['ticker'], '월봉MA10',
                                      r['close'], r['ma10'])
    except Exception as e:
        print(f'[트래커] {e}')
