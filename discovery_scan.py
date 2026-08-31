# -*- coding: utf-8 -*-
"""
이슈섹터 신흥 종목 발굴 스캔
- 매월 말 1회 실행
- AI/반도체/데이터센터/우주항공 등 이슈 섹터 후보 종목 조사
- NASDAQ 100 / S&P 500 미편입 종목 우선 발굴
- 월봉 MA10 상태 함께 표시
- 슬랙 전송

⚠️ 2026-08-31 경고: 이 스크립트는 월봉MA10만 확인하고 주봉MA10 눌림목
(진입 타이밍) 조건은 검증하지 않는다. "신규돌파/지지권" 표시는 매수 확정
신호가 아니다 — CLAUDE.md의 실제 매수조건(월봉+주봉 이중조건)을 정확히
구현한 us_weekly_scan.py 결과와 반드시 교차 확인할 것. GitHub Actions
자동 스케줄에는 연결되어 있지 않고 수동 실행(workflow_dispatch)만 가능하다.
"""
import sys, json, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import yfinance as yf
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CFG  = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
WEBHOOK   = CFG.get('slack_webhook_url_discovery', '')
MA_PERIOD = CFG.get('ma_period', 10)

# ── 이슈 섹터별 후보 종목 ─────────────────────────────────────
# NASDAQ 100 / S&P 500 미편입 또는 신규 상장 신흥 종목 위주
CANDIDATES = {
    # AI 반도체 / 메모리
    'SNDK':  ('샌디스크',        'AI메모리/HBF'),
    'MU':    ('마이크론',        'AI메모리/HBF'),
    'CBRS':  ('세레브라스',      'AI반도체/SRAM'),
    'SMCI':  ('슈퍼마이크로',    'AI서버'),
    'CIEN':  ('시에나',          'AI광통신'),
    'COHR':  ('코히런트',        'AI광통신'),
    'LITE':  ('루멘텀',          'AI광통신'),
    'AAOI':  ('어플라이드옵토',  'AI광통신'),
    # 데이터센터 / 전력
    'VRT':   ('버티브',          '데이터센터전력'),
    'GDDY':  ('고대디',          '데이터센터'),
    'EQIX':  ('에퀴닉스',        '데이터센터리츠'),
    'DLR':   ('디지털리얼티',    '데이터센터리츠'),
    'NRG':   ('NRG에너지',       '데이터센터전력'),
    'VST':   ('비스트라에너지',  '데이터센터전력'),
    # 우주항공 / 방산
    'RKLB':  ('로켓랩',          '우주발사체'),
    'ASTS':  ('AST스페이스',     '우주위성통신'),
    'RDW':   ('레드와이어',      '우주제조'),
    'MNTS':  ('모멘터스',        '우주운송'),
    'LUNR':  ('인투이티브머신', '달탐사'),
    'PL':    ('플래닛랩스',      '위성데이터'),
    # 양자컴퓨터
    'IONQ':  ('아이온큐',        '양자컴퓨터'),
    'RGTI':  ('리게티',          '양자컴퓨터'),
    'QUBT':  ('큐비트',          '양자컴퓨터'),
    'QBTS':  ('D-Wave',          '양자컴퓨터'),
    # 휴머노이드 / 로봇
    'TSLA':  ('테슬라',          '피지컬AI/로봇'),
    'FIGURE':('피규어AI',        '휴머노이드'),   # 비상장 모니터링용
    # 바이오 AI
    'TEM':   ('템퍼스AI',        '바이오AI'),
    'RXRX':  ('리커전파마',      '바이오AI'),
    'SEER':  ('시어바이오',      '바이오AI'),
    # 소형원자로 / 에너지
    'OKLO':  ('오클로',          '소형원자로'),
    'SMR':   ('뉴스케일파워',    '소형원자로'),
    'BWXT':  ('BWX테크',         '소형원자로'),
    'CCJ':   ('카메코',          '우라늄'),
}

# NASDAQ 100 구성종목 (편입 여부 판단용 — 최신 기준 주요 종목)
NDX100 = {
    'AAPL','MSFT','NVDA','AMZN','META','GOOGL','GOOG','TSLA','AVGO','COST',
    'NFLX','AMD','ADBE','QCOM','TMUS','TXN','AMAT','ISRG','INTU','AMGN',
    'BKNG','MU','LRCX','PANW','KLAC','MRVL','CDNS','SNPS','REGN','GILD',
    'ADI','ASML','MELI','CTAS','CRWD','TEAM','MNST','FTNT','PCAR','ORLY',
    'WDAY','DASH','CPRT','NXPI','ROST','PAYX','AEP','DXCM','FANG','EXC',
    'IDXX','KHC','GEHC','ODFL','FAST','CTSH','BIIB','EA','CSGP','ZS',
    'VRSK','ANSS','ON','ILMN','DDOG','GFS','TTWO','DLTR','WBD','ALGN',
    'EBAY','SMCI','RIVN','ENPH','SNDK','APP','PLTR','ARM','MSTR','HOOD','COIN',
}

SP500_MAJOR = {
    'AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA','BRK-B','AVGO','JPM',
    'JNJ','V','XOM','UNH','MA','PG','HD','COST','MRK','ABBV','CVX','LLY',
    'PEP','KO','ADBE','CRM','WMT','BAC','DIS','NFLX','AMD','QCOM','MU',
    'SNDK','VRT','OKLO','SMR','IONQ','RGTI','RKLB','ASTS','TEM',
}


def get_ma10_status(ticker: str) -> dict:
    try:
        df = yf.download(ticker, period='3y', interval='1mo',
                         auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            close = df['Close'].iloc[:, 0].dropna()
        else:
            close = df['Close'].dropna()
        if len(close) < MA_PERIOD + 2:
            return {}
        ma10  = close.rolling(MA_PERIOD).mean()
        curr_close = float(close.iloc[-1])
        curr_ma10  = float(ma10.iloc[-1])
        prev_close = float(close.iloc[-2])
        prev_ma10  = float(ma10.iloc[-2])
        pct   = (curr_close - curr_ma10) / curr_ma10 * 100
        above = curr_close > curr_ma10
        fresh = (prev_close < prev_ma10) and above
        # 52주 수익률
        if len(close) >= 13:
            ret52 = (curr_close - float(close.iloc[-13])) / float(close.iloc[-13]) * 100
        else:
            ret52 = 0.0
        return {
            'close': round(curr_close, 2),
            'ma10':  round(curr_ma10, 2),
            'pct':   round(pct, 1),
            'above': above,
            'fresh': fresh,
            'ret52': round(ret52, 1),
        }
    except Exception:
        return {}


def scan() -> list:
    results = []
    print(f'이슈섹터 발굴 스캔 시작: {len(CANDIDATES)}개 종목')
    for ticker, (name, sector) in CANDIDATES.items():
        s = get_ma10_status(ticker)
        if not s:
            continue
        in_ndx  = ticker in NDX100
        in_sp   = ticker in SP500_MAJOR
        index_tag = ('NDX100' if in_ndx else '') + ('SP500' if in_sp else '')
        if not index_tag:
            index_tag = '미편입'

        if s['fresh']:
            signal, priority = '★★ 신규돌파', 1
        elif s['above'] and s['pct'] <= 5:
            signal, priority = '★ 지지권', 2
        elif s['above'] and s['pct'] <= 30:
            signal, priority = '● 추세권', 3
        elif s['above']:
            signal, priority = '△ 고점권', 4
        else:
            signal, priority = '✗ MA10아래', 5

        results.append({
            'ticker':    ticker,
            'name':      name,
            'sector':    sector,
            'index':     index_tag,
            'signal':    signal,
            'priority':  priority,
            **s,
        })
        print(f'  {ticker:<6} {name:<12} {signal}  {s["pct"]:+.1f}%  52주:{s["ret52"]:+.0f}%')

    return sorted(results, key=lambda x: (x['priority'], x['pct']))


def send_slack(results: list):
    if not WEBHOOK or not results:
        return
    today  = datetime.today().strftime('%Y.%m.%d')
    df     = pd.DataFrame(results)
    fresh  = df[df['priority'] == 1]
    dip    = df[df['priority'] == 2]
    trend  = df[df['priority'] == 3]
    below  = df[df['priority'] == 5]

    def fmt(r):
        idx = f'`{r["index"]}`' if r['index'] != '미편입' else '🆕미편입'
        return (f'`{r["ticker"]}`  *{r["name"]}*  ${r["close"]:.1f}'
                f'  {r["pct"]:+.1f}%  52주{r["ret52"]:+.0f}%  {idx}  _{r["sector"]}_')

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"🔍 이슈섹터 신흥 종목 발굴  {today}"}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": (f'신규돌파 *{len(fresh)}* 종목 / 지지권 *{len(dip)}* 종목 / '
                           f'추세권 *{len(trend)}* 종목\n'
                           f'_🆕미편입 = NASDAQ100·S&P500 미포함 신흥 종목_')}},
        {"type": "context", "elements": [{"type": "mrkdwn",
         "text": "⚠️ *이 스캔은 월봉만 확인합니다 — 주봉MA10 눌림목(진입 타이밍)은 "
                 "검증 안 됨.* 실제 매수 판단 전 `us_weekly_scan.py` 결과와 반드시 "
                 "교차 확인하세요."}]},
        {"type": "divider"},
    ]

    for label, group in [('★★ 신규돌파', fresh), ('★ 지지권 (MA10 +5% 이내)', dip),
                         ('● 추세권 (MA10 +5~30%)', trend)]:
        if group.empty:
            continue
        lines = '\n'.join(fmt(r) for _, r in group.iterrows())
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*{label} — {len(group)}종목*\n{lines}"}})
        blocks.append({"type": "divider"})

    if not below.empty:
        lines = '\n'.join(
            f'`{r["ticker"]}`  {r["name"]}  ${r["close"]:.1f}  {r["pct"]:+.1f}%  _{r["sector"]}_'
            for _, r in below.iterrows()
        )
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*✗ MA10 아래 (관심 대기) — {len(below)}종목*\n{lines}"}})

    payload = json.dumps(
        {'text': f'이슈섹터 발굴 {today}', 'blocks': blocks},
        ensure_ascii=False
    ).encode('utf-8')
    req = urllib.request.Request(
        WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            print(f'Slack 전송: {"성공" if res.read().decode() == "ok" else "실패"}')
    except Exception as e:
        print(f'Slack 오류: {e}')


if __name__ == '__main__':
    print(f'\n이슈섹터 신흥 종목 발굴  {datetime.now().strftime("%Y-%m-%d %H:%M")}\n')
    results = scan()
    print(f'\n총 {len(results)}종목 분석 완료')
    send_slack(results)

    # 트래커 연동 — 신규돌파 자동 기록
    try:
        import signal_tracker as tracker
        for r in results:
            if r.get('priority') == 1:  # 신규돌파만
                tracker.record_signal(r['ticker'], r['name'], '이슈섹터(주봉미검증)',
                                      r['close'], r['ma10'])
    except Exception as e:
        print(f'[트래커] {e}')
