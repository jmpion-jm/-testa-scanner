# -*- coding: utf-8 -*-
"""
NASDAQ 100 전체 월봉 MA10 스캔
월 1회 실행 → 진입 가능 종목 리스트 출력 + Slack 전송

2026-09-26 원서 원칙 전환(사용자 결정): 이 스캔의 ★★ 돌파(후킹)·★ 10이평 지지 반등이 곧 원서 매수 신호다.
주봉 눌림목 교차 확인은 폐지(주봉 매수 폐지 — 매매법_전체_구현명세.md H4). 월말 장 마감 후 실행 기준.
"""
import sys, json, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf
import book_patterns as bkp  # 원서 매수 신호(캔들차트(성승현작가)/매매법_전체_구현명세.md)
import urllib.request, urllib.error
from io import StringIO
from datetime import datetime

# ── 설정 ─────────────────────────────────────────────────────
BASE    = os.path.dirname(os.path.abspath(__file__))
try:
    CFG       = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
    WEBHOOK   = CFG.get('slack_webhook_url_ndx100', '')
    MA_PERIOD = CFG.get('ma_period', 10)
except Exception:
    WEBHOOK   = os.environ.get('SLACK_NDX100_WEBHOOK_URL', '')
    MA_PERIOD = 10

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


# ── NASDAQ 100 티커 목록 ──────────────────────────────────────
def get_ndx100_tickers() -> list:
    import requests
    try:
        url = 'https://en.wikipedia.org/wiki/Nasdaq-100'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=15)
        tables = pd.read_html(StringIO(resp.text))
        # 구성 종목 테이블 찾기
        for t in tables:
            cols = [str(c).lower() for c in t.columns]
            if any('ticker' in c or 'symbol' in c for c in cols):
                col = next(c for c in t.columns if 'ticker' in str(c).lower() or 'symbol' in str(c).lower())
                tickers = t[col].dropna().str.replace('.', '-', regex=False).tolist()
                tickers = [t for t in tickers if isinstance(t, str) and t.isalpha() or '-' in str(t)]
                if len(tickers) > 50:
                    print(f'NASDAQ 100 티커 로드 완료: {len(tickers)}개')
                    return tickers
        raise ValueError('티커 테이블을 찾지 못했습니다')
    except Exception as e:
        print(f'Wikipedia 로드 실패: {e}')
        print('하드코딩 목록 사용...')
        return _fallback_tickers()


def _fallback_tickers() -> list:
    """Wikipedia 접근 실패 시 주요 NASDAQ 100 종목 (하드코딩)"""
    tickers = [
        'AAPL','MSFT','NVDA','AMZN','META','GOOGL','GOOG','TSLA','AVGO','COST',
        'NFLX','AMD','ADBE','QCOM','TMUS','TXN','AMAT','ISRG','INTU','AMGN',
        'BKNG','MU','LRCX','PANW','KLAC','MRVL','CDNS','SNPS','REGN','GILD',
        'ADI','ASML','MELI','CTAS','CRWD','TEAM','MNST','FTNT','PCAR','ORLY',
        'WDAY','DASH','CPRT','NXPI','ROST','PAYX','AEP','DXCM','FANG','EXC',
        'IDXX','KHC','GEHC','ODFL','FAST','CTSH','BIIB','EA','CSGP','ZS',
        'VRSK','ANSS','ON','ILMN','DDOG','SIRI','GFS','TTWO','DLTR','WBD',
        'ALGN','EBAY','SMCI','MTCH','RIVN','ENPH','LCID','NWSA','NWS','FOXA',
        'FOX','WBA','CEG','XEL','AZN','PDD','BIDU','JD','VRTX','ABNB',
        'APP','PLTR','ARM','MSTR','HOOD','COIN',
    ]
    print(f'하드코딩 목록: {len(tickers)}개')
    return tickers


# ── 월봉 MA10 스캔 ────────────────────────────────────────────
def scan_ndx100(tickers: list) -> list:
    results = []
    print(f'\n스캔 시작: {len(tickers)}개 종목')
    print('=' * 60)
    print('월봉 데이터 다운로드 중...')

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
        print(f'다운로드 오류: {e}')
        return []

    print('다운로드 완료. 분석 중...')

    for ticker in tickers:
        try:
            if ticker not in raw.columns.get_level_values(0):
                continue
            sub = raw[ticker][['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
            if len(sub) < MA_PERIOD + 2:
                continue
            # 원서 원칙(2026-09-26 사용자 결정): 매수 = 월말 종가로 확정된 돌파(후킹 캔들 p.256) 또는
            # 10이평 지지 반등(p.340) — book_patterns.buy_signal. 이전 판의 "괴리율 5%/30% 구간" 분류는 원서에 없어 제거.
            d = bkp.prepare(sub.ffill())
            n = len(d) - 1
            close = float(d['Close'].iat[n])
            ma10  = float(d['MA'].iat[n])
            pct   = (close - ma10) / ma10 * 100
            above = close > ma10
            if not above:
                continue  # MA10 아래 → 매수 대상 아님(보유 중이면 매도)

            sig   = bkp.buy_signal(d, n)
            fresh = sig == '돌파'
            decline3 = (float(d['Close'].iat[n]) < float(d['Close'].iat[n - 1]) <
                        float(d['Close'].iat[n - 2]))
            if sig == '돌파':
                signal, priority = '★★ 돌파(후킹 p.256)', 1
            elif sig == '지지':
                signal, priority = '★ 10이평 지지 반등(p.340)', 2
            else:
                signal, priority = '● 추세 진행 중(보유 유지, 신규 매수 아님)', 3

            results.append({
                'ticker': ticker, 'close': round(close, 2),
                'ma10': round(ma10, 2), 'pct': round(pct, 1),
                'signal': signal, 'priority': priority,
                'fresh': fresh, 'decline3': decline3,
                'box': bool(sig) and bkp.in_box(d, n),   # 박스권 안(p.309) — 후순위 표시
            })
        except Exception:
            continue

    print(f'\n스캔 완료: {len(results)}개 신호')
    return results


# ── 결과 출력 ─────────────────────────────────────────────────
def print_results(results: list):
    if not results:
        print('신호 없음')
        return
    df = pd.DataFrame(results).sort_values(['priority', 'box', 'pct'])
    print(f'\n{"="*60}')
    print(f'  NASDAQ 100 월봉 MA10 스캔 결과')
    print(f'{"="*60}')
    for sig, group in df.groupby('signal', sort=False):
        if group.empty:
            continue
        print(f'\n[ {sig} ] — {len(group)}종목')
        print(f'  {"티커":<8} {"현재가":>8} {"MA10":>8} {"괴리율":>7}  {"주의"}')
        print(f'  {"-"*48}')
        for _, r in group.iterrows():
            warn = ('⚠️연속하락' if r['decline3'] else '') + (' 📦박스권' if r.get('box') else '')
            print(f'  {r["ticker"]:<8} ${r["close"]:>7.2f}  ${r["ma10"]:>7.2f}  {r["pct"]:>+6.1f}%  {warn}')


# ── CSV 저장 ──────────────────────────────────────────────────
def save_csv(results: list):
    if not results:
        return
    path = os.path.join(BASE, 'ndx100_scan_result.csv')
    pd.DataFrame(results).sort_values(['priority', 'box', 'pct']).to_csv(
        path, index=False, encoding='utf-8-sig')
    print(f'\nCSV 저장: {path}')


# ── Slack 전송 ────────────────────────────────────────────────
def send_slack(results: list):
    if not WEBHOOK or not results:
        return
    df    = pd.DataFrame(results).sort_values(['priority', 'box', 'pct'])
    today = datetime.today().strftime('%Y.%m.%d')
    fresh = df[df['priority'] == 1]
    dip   = df[df['priority'] == 2].head(10)
    trend = df[df['priority'] == 3].head(10)

    def fmt(r):
        warn = (' ⚠️' if r['decline3'] else '') + (' 📦박스권(상단 돌파 전)' if r.get('box') else '')
        return f'`{r["ticker"]}`  ${r["close"]:.2f}  *{r["pct"]:+.1f}%*{warn}'

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": f"NASDAQ 100 월봉 MA10 스캔  {today}"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f'진입 신호: *{len(df[df["priority"]<=2])}종목* '
                           f'(돌파 {len(fresh)} / 10이평 지지 {len(df[df["priority"]==2])})\n'
                           f'_전체 목록: ndx100_scan_result.csv_')}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": "원서 원칙: 매수 = 월말 종가로 확정된 돌파(후킹)·10이평 지지 반등 / 매도 = 월말 종가 10이평 이탈"}]},
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
                       "text": {"type": "mrkdwn",
                                "text": f"*★★ 돌파(후킹 캔들, 원서 p.256) — {len(fresh)}종목*\n{lines}"}})
        blocks.append({"type": "divider"})
    if not dip.empty:
        lines = '\n'.join(fmt(r) for _, r in dip.iterrows())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*★ 10이평 지지 반등 (원서 p.340) — 상위 10종목*\n{lines}"}})
        blocks.append({"type": "divider"})
    if not trend.empty:
        lines = '\n'.join(fmt(r) for _, r in trend.iterrows())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*● 추세 진행 중 (보유 유지, 신규 매수 아님) — 상위 10종목*\n{lines}"}})

    # ── 실행 타임라인 ──
    action_rows = pd.concat([fresh, dip]).sort_values(['box', 'pct']).head(5)
    if not action_rows.empty:
        header = f'{"시간":<9} {"계좌":<10} 행동'
        sep    = '─' * 55
        tl     = []
        for _, r in action_rows.iterrows():
            sig  = '돌파' if r['fresh'] else '10이평 지지'
            warn = (' ⚠️연속하락' if r['decline3'] else '') + (' 📦박스권' if r['box'] else '')
            tl.append(f'{"22:30~":<9} {"미래에셋":<10} 🟡 {r["ticker"]} ${r["close"]:.2f}  원서 매수 신호 ({sig} {r["pct"]:+.1f}%){warn}')
        table = f'```\n{header}\n{sep}\n' + '\n'.join(tl) + '\n```'
        blocks.append({"type": "divider"})
        blocks.append({"type": "header",
                       "text": {"type": "plain_text", "text": "📋 매수 신호 타임라인 (미국 시장 22:30~)"}})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": table}})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "_원서 매수 신호 종목(월말 확정). 여러 종목이면 통합 스캔의 우선순위(1~3군)·차트를 보고 고를 것._"}})

    payload = json.dumps({'text': f'NASDAQ100 스캔 {today}', 'blocks': blocks},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print(f'Slack 전송: {"성공" if res.read().decode()=="ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


# ── 메인 ─────────────────────────────────────────────────────
def save_signals(results: list):
    """원서 매수 신호(돌파·10이평 지지) 종목을 ndx100_signals.json 으로 저장"""
    signals = [
        {'ticker': r['ticker'], 'name': r['ticker'],
         'pct': r['pct'], 'priority': r['priority']}
        for r in results if r['priority'] <= 2
    ]
    path = os.path.join(BASE, 'ndx100_signals.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'date': datetime.today().strftime('%Y-%m-%d'), 'signals': signals},
                  f, ensure_ascii=False, indent=2)
    print(f'신호 저장: {len(signals)}종목 → ndx100_signals.json')


if __name__ == '__main__':
    # 2026-09-26: 워크플로우가 28~31일 매일 도는데 말일 확인이 없어 한 달에 최대 4번,
    # 그것도 미완성 월봉으로 발송하고 있었음 — 스케줄 실행은 말일 미국장 마감 후에만 진행.
    import market_time as mt
    if not mt.should_run_monthly_scan():
        print('말일 미국장 마감 후가 아니라 스킵 (market_time.should_run_monthly_scan)')
        sys.exit(0)
    print(f'\nNASDAQ 100 월봉 MA10 스캔  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'기준: MA{MA_PERIOD} | 원서 매수 신호 = 월말 확정 돌파(후킹)·10이평 지지\n')

    tickers = get_ndx100_tickers()
    if not tickers:
        sys.exit(1)

    results = scan_ndx100(tickers)
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
