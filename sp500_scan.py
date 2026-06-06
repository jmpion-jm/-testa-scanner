# -*- coding: utf-8 -*-
"""
S&P 500 전체 월봉 MA10 스캔
월 1회 실행 → 진입 가능 종목 리스트 출력 + Slack 전송
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


# ── S&P 500 티커 목록 ─────────────────────────────────────────
def get_sp500_tickers() -> list:
    """Wikipedia에서 S&P 500 구성 종목 가져오기"""
    import requests
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=15)
        from io import StringIO
        tables = pd.read_html(StringIO(resp.text))
        df = tables[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        print(f'S&P 500 티커 로드 완료: {len(tickers)}개')
        return tickers
    except Exception as e:
        print(f'Wikipedia 로드 실패: {e}')
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
        {"type": "divider"},
    ]

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
            tl.append(f'{"22:30~":<9} {"미래에셋":<10} 🟢 {r["ticker"]} ${r["close"]:.2f}  매수 ({sig} {r["pct"]:+.1f}%){warn}')
        table = f'```\n{header}\n{sep}\n' + '\n'.join(tl) + '\n```'
        blocks.append({"type": "divider"})
        blocks.append({"type": "header",
                       "text": {"type": "plain_text", "text": "📋 실행 타임라인 (미국 시장 22:30~)"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": table}})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "_⚠️ 매수 전 반드시 현재가 재확인  |  손절가(MA10 이탈) 기억_"}})

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
