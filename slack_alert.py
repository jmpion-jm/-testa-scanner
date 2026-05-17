# -*- coding: utf-8 -*-
"""
Slack 자동 알림 시스템
  - 매주 금요일  : 모니터링 알림 (관찰용, 매매 아님)
  - 매달 말일    : 매매 결정 알림 (실제 매매 기준)
"""
import sys, io, json, os, urllib.request, urllib.error
from datetime import datetime, date
import calendar

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEET_AVAILABLE = True
except ImportError:
    GSHEET_AVAILABLE = False

# ── 구글시트 설정 ─────────────────────────────────────────────
GSHEET_CREDS  = r'C:\Users\user\.claude\secret-footing-453908-u2-67fee5c1f2f0.json'
GSHEET_ID     = '1Xmj6R332n1IvgA6fJ0c5YcOvO6gR_OeDHYmVuTTIzZE'
GSHEET_SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly',
                 'https://www.googleapis.com/auth/drive.readonly']
# 채권·현금은 MA 분석 제외 (B&H 유지)
EXCLUDE_ASSETS = {'채권', '현금', '예수금'}

# ── 설정 로드 ────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

with open(CONFIG_PATH, encoding='utf-8') as f:
    CFG = json.load(f)

WEBHOOK_URL  = CFG['slack_webhook_url']
STOCKS       = CFG['stocks']          # {ticker: [name, sector]}
THEME_ETFS   = CFG['theme_etfs']      # {ticker.KS: [name, theme]}
SAFE_HAVEN   = CFG['safe_haven_etf']  # 357870.KS
SAFE_NAME    = CFG['safe_haven_name'] # TIGER CD금리
MA_PERIOD    = CFG['ma_period']       # 10
CRASH_PCT    = CFG['alert_thresholds']['crash_alert_pct']   # -15
ZONE_PCT     = CFG['alert_thresholds']['breakout_zone_pct'] # 5


# ── 유틸 ─────────────────────────────────────────────────────
def is_last_trading_day() -> bool:
    """오늘이 이번 달 마지막 거래일(금요일 또는 말일)에 해당하는지"""
    today = date.today()
    last_day = calendar.monthrange(today.year, today.month)[1]
    # 이번달 마지막 평일 계산
    for d in range(last_day, 0, -1):
        wd = date(today.year, today.month, d).weekday()
        if wd < 5:   # 월~금
            return today.day == d
    return False


def fetch(ticker: str, period='3y', interval='1mo') -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open','High','Low','Close','Volume']].dropna()


def send_slack(blocks: list, text: str = "주식 알림"):
    """Slack Block Kit 메시지 전송"""
    if WEBHOOK_URL.startswith('여기에'):
        print('[Slack 미설정] config.json의 slack_webhook_url을 입력해주세요.')
        print('[미리보기]', text)
        return False

    payload = json.dumps({'text': text, 'blocks': blocks}).encode('utf-8')
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            ok = res.read().decode() == 'ok'
            print(f'Slack 전송: {"성공" if ok else "실패"}')
            return ok
    except urllib.error.URLError as e:
        print(f'Slack 전송 오류: {e}')
        return False


# ── 구글시트 포트폴리오 읽기 ──────────────────────────────────
def _to_yf_ticker(code: str) -> str:
    """종목코드 → yfinance 티커 변환"""
    code = code.strip()
    if not code or code in ('예수금',):
        return ''
    # 숫자로만 이루어졌거나 숫자+영문(ETF코드) → 한국주식
    if code.replace('0','').replace('1','').replace('2','').replace('3','') \
            .replace('4','').replace('5','').replace('6','').replace('7','') \
            .replace('8','').replace('9','').replace('A','').replace('B','') \
            .replace('C','').replace('D','').replace('E','').replace('F','') \
            .replace('G','').replace('N','').replace('P','').replace('Q','') \
            .replace('S','').replace('T','').replace('X','') == '':
        # 영문만 남으면 US 주식
        letters = ''.join(c for c in code if c.isalpha())
        digits  = ''.join(c for c in code if c.isdigit())
        if digits and letters:
            return code + '.KS'   # 혼합(ETF코드)
        if digits:
            return code + '.KS'   # 순수 숫자
        return code               # 순수 영문 = US 주식
    return code


def read_portfolio() -> list:
    """구글시트 포트폴리오 탭에서 보유종목 읽기"""
    if not GSHEET_AVAILABLE:
        print('[구글시트 미설치] pip install gspread google-auth')
        return []
    try:
        creds = Credentials.from_service_account_file(GSHEET_CREDS, scopes=GSHEET_SCOPES)
        gc    = gspread.authorize(creds)
        sh    = gc.open_by_key(GSHEET_ID)
        ws    = sh.worksheet('포트폴리오')
        rows  = ws.get_all_values()
    except Exception as e:
        print(f'[구글시트 오류] {e}')
        return []

    holdings = []
    for row in rows:
        if len(row) < 5:
            continue
        account = row[1].strip()
        asset   = row[2].strip()
        code    = row[3].strip()
        name    = row[4].strip()

        if not code or not name or asset in EXCLUDE_ASSETS:
            continue
        if account in ('계좌', '퇴직연금', '개인계좌', '일반계좌 마누라',
                       'Total', '', '구분'):
            continue
        if name in ('종목명', '예수금', ''):
            continue
        # 한글·특수문자·원화표시 포함 시 잘못된 행 제거
        if any('가' <= c <= '힣' for c in code):
            continue
        if any(c in code for c in ('%', ',', '▼', '▲', '·', ' ')):
            continue
        if len(code) > 10:
            continue

        ticker = _to_yf_ticker(code)
        if not ticker:
            continue

        holdings.append({
            'account': account,
            'asset':   asset,
            'code':    code,
            'ticker':  ticker,
            'name':    name,
        })

    # 중복 제거 (같은 티커가 여러 계좌에 있을 수 있음)
    seen = {}
    for h in holdings:
        key = h['ticker']
        if key not in seen:
            seen[key] = h
            seen[key]['accounts'] = [h['account']]
        else:
            if h['account'] not in seen[key]['accounts']:
                seen[key]['accounts'].append(h['account'])

    return list(seen.values())


def scan_portfolio(holdings: list) -> list:
    """보유종목 MA10 상태 스캔"""
    rows = []
    for h in holdings:
        try:
            t  = yf.Ticker(h['ticker'])
            df = t.history(period='3y', interval='1mo', auto_adjust=True)
            if df.empty:
                df = t.history(period='max', interval='1mo', auto_adjust=True)
            if df.empty or len(df) < MA_PERIOD + 2:
                continue
            df.index = df.index.tz_localize(None) if df.index.tz else df.index
            df = df[['Close']].dropna()

            df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
            latest, prev = df.iloc[-1], df.iloc[-2]

            close = float(latest['Close'])
            ma    = float(latest['MA'])
            pct   = (close - ma) / ma * 100
            above = close > ma
            fresh = bool(float(prev['Close']) < float(prev['MA']) and above)
            broke = bool(float(prev['Close']) > float(prev['MA']) and not above)

            rows.append({
                **h,
                'close': round(close, 2),
                'ma':    round(ma, 2),
                'pct':   round(pct, 1),
                'above': above,
                'fresh': fresh,
                'broke': broke,
            })
        except Exception as e:
            print(f'  [스캔오류] {h["name"]} ({h["ticker"]}): {e}')
    return rows


# ── 스캔 ─────────────────────────────────────────────────────
def scan_etfs() -> list:
    """테마 ETF 월봉 10이평 스캔"""
    rows = []
    for ticker, (name, theme) in THEME_ETFS.items():
        try:
            t = yf.Ticker(ticker)
            df = t.history(period='3y', interval='1mo', auto_adjust=True)
            if df.empty:
                df = t.history(period='max', interval='1mo', auto_adjust=True)
            df.index = df.index.tz_localize(None) if df.index.tz else df.index
            df = df[['Close','Volume']].dropna()
            if len(df) < MA_PERIOD + 2:
                continue
            df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
            latest, prev = df.iloc[-1], df.iloc[-2]

            close = float(latest['Close'])
            ma    = float(latest['MA'])
            pct   = (close - ma) / ma * 100
            above = close > ma
            fresh = bool(float(prev['Close']) < float(prev['MA']) and above)
            broke = bool(float(prev['Close']) > float(prev['MA']) and not above)

            rows.append(dict(
                ticker=ticker, name=name, theme=theme,
                close=round(close, 0), ma=round(ma, 0),
                pct=round(pct, 1), above=above,
                fresh=fresh, broke=broke,
            ))
        except:
            pass
    return rows


def scan_all() -> list:
    rows = []
    for ticker, (name, sector) in STOCKS.items():
        try:
            df = fetch(ticker)
            if len(df) < MA_PERIOD + 2:
                continue
            df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
            latest, prev = df.iloc[-1], df.iloc[-2]

            close = float(latest['Close'])
            ma    = float(latest['MA'])
            pct   = (close - ma) / ma * 100
            above = close > ma
            fresh = bool(float(prev['Close']) < float(prev['MA']) and above)
            broke = bool(float(prev['Close']) > float(prev['MA']) and not above)

            vol_avg = float(df['Volume'].iloc[-6:].mean()) or 1
            vol_r   = float(latest['Volume']) / vol_avg

            rows.append(dict(
                ticker=ticker, name=name, sector=sector,
                close=round(close, 2), ma=round(ma, 2),
                pct=round(pct, 1), above=above,
                fresh=fresh, broke=broke, vol_r=round(vol_r, 2),
            ))
        except:
            pass
    return rows


# ── Block Kit 빌더 ───────────────────────────────────────────
def _divider():
    return {"type": "divider"}


def _header(text: str):
    return {"type": "header", "text": {"type": "plain_text", "text": text}}


def _section(text: str):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _fields(items: list):
    return {
        "type": "section",
        "fields": [{"type": "mrkdwn", "text": t} for t in items]
    }


# ── 0-1. 보유종목 포트폴리오 섹션 ────────────────────────────
def build_portfolio_section(port_rows: list, is_monthly: bool) -> list:
    if not port_rows:
        return []

    blocks = [
        _divider(),
        _header('내 포트폴리오 — 보유종목 MA10 점검'),
    ]

    # 이탈 경보 (가장 중요)
    broke = [r for r in port_rows if r['broke']]
    if broke:
        label = '🚨 즉시 매도 — MA10 이탈 확정' if is_monthly else '🚨 이탈 경보 (월말 종가 확정 후 결정)'
        blocks.append(_section(f'*{label}*'))
        for r in broke:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(
                f'`{r["name"]}` ({accs})\n'
                f'현재가 {r["close"]:,.0f}  /  MA10 {r["ma"]:,.0f}  /  *{r["pct"]:+.1f}%*\n'
                f'→ {"전량 매도 후 CD금리 대기" if is_monthly else "월말 종가 확인 후 결정"}'
            ))

    # 신규 돌파 (재진입 신호)
    fresh = [r for r in port_rows if r['fresh']]
    if fresh:
        label = '★ 추가매수 검토 — MA10 재돌파' if is_monthly else '★ MA10 돌파 (월말 확정 후 검토)'
        blocks.append(_section(f'*{label}*'))
        for r in fresh:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(
                f'`{r["name"]}` ({accs})  *{r["pct"]:+.1f}%*'
            ))

    # 이탈 중 (이미 아래)
    below = [r for r in port_rows if not r['above'] and not r['broke']]
    if below:
        blocks.append(_section('*❌ MA10 아래 — 보유 중 주의*'))
        fields = [f'`{r["name"]}`  *{r["pct"]:+.1f}%*  ({", ".join(r["accounts"])})' for r in below]
        blocks.append(_fields(fields[:10]))

    # 정상 홀딩
    holding = [r for r in port_rows if r['above'] and not r['fresh']]
    if holding:
        blocks.append(_section('*● 홀딩 유지 — MA10 위*'))
        fields = []
        for r in holding:
            emoji = '▲' if r['pct'] > 10 else ('→' if r['pct'] > 0 else '▽')
            fields.append(f'{emoji} `{r["name"]}`  *{r["pct"]:+.1f}%*')
        blocks.append(_fields(fields[:10]))

    # 요약
    above_cnt = sum(1 for r in port_rows if r['above'])
    below_cnt = len(port_rows) - above_cnt
    alert_cnt = len(broke)
    blocks.append(_section(
        f'*포트폴리오 요약* — MA10 위: *{above_cnt}개* | 아래: *{below_cnt}개*'
        + (f' | ⚠️ 이탈경보: *{alert_cnt}개*' if alert_cnt else '')
    ))

    return blocks


# ── 0. 테마 ETF 섹션 (주간/월말 공통) ───────────────────────
def build_etf_section(etf_rows: list, is_monthly: bool) -> list:
    """테마 ETF 10이평 현황 블록"""
    if not etf_rows:
        return []

    blocks = [
        _divider(),
        _header('DC/IRP/연금 — 테마 ETF 월봉 10이평 점검'),
        _section(f'*피난처:* `{SAFE_HAVEN}` {SAFE_NAME} (이탈 시 교체 대상)'),
    ]

    # 신규 돌파 → 재진입 신호
    fresh = [r for r in etf_rows if r['fresh']]
    if fresh:
        label = '★ 재진입 신호 — CD금리 → 테마ETF 교체' if is_monthly else '★ 신규 돌파 (월말 종가 확정 후 재진입 검토)'
        blocks.append(_section(f'*{label}*'))
        for r in fresh:
            blocks.append(_section(
                f'`{r["name"]}` [{r["theme"]}]  '
                f'{r["close"]:,.0f}원  /  10이평 {r["ma"]:,.0f}원  /  *+{r["pct"]}%*'
            ))

    # 신규 이탈 → CD금리 교체 신호
    broke = [r for r in etf_rows if r['broke']]
    if broke:
        label = '🚨 즉시 교체 — 테마ETF → CD금리' if is_monthly else '🚨 이탈 경보 (월말 종가 확정 후 CD금리 교체 결정)'
        blocks.append(_section(f'*{label}*'))
        for r in broke:
            blocks.append(_section(
                f'`{r["name"]}` [{r["theme"]}]  '
                f'{r["close"]:,.0f}원  /  10이평 {r["ma"]:,.0f}원  /  *{r["pct"]}%*'
            ))

    # 이평 위 (정상 홀딩)
    holding = [r for r in etf_rows if r['above'] and not r['fresh']]
    if holding:
        blocks.append(_section('*● 홀딩 유지 — 10이평 위*'))
        fields = [f'`{r["name"]}`  *+{r["pct"]}%*' for r in holding]
        blocks.append(_fields(fields[:10]))

    # 이평 아래 (CD금리 대기)
    waiting = [r for r in etf_rows if not r['above'] and not r['broke']]
    if waiting:
        blocks.append(_section('*❌ CD금리 유지 — 10이평 아래 (재진입 금지)*'))
        fields = [f'`{r["name"]}`  *{r["pct"]}%*' for r in waiting]
        blocks.append(_fields(fields[:10]))

    # 요약
    above_cnt = sum(1 for r in etf_rows if r['above'])
    below_cnt = len(etf_rows) - above_cnt
    blocks.append(_section(
        f'*ETF 요약* — 홀딩: *{above_cnt}개* | CD금리 대기: *{below_cnt}개*'
    ))

    return blocks


# ── 1. 매주 금요일 — 모니터링 알림 ──────────────────────────
def build_weekly_alert(rows: list, etf_rows: list = None, port_rows: list = None) -> list:
    today_str = datetime.today().strftime('%Y.%m.%d')
    blocks = [
        _header(f'주간 모니터링  {today_str} (금)'),
        _section(
            '*월봉 10이평선 기준 주간 점검*\n'
            '>⚠️ 이 알림은 관찰용입니다. 실제 매매는 *월말 알림* 기준으로만 하세요.'
        ),
        _divider(),
    ]

    above = [r for r in rows if r['above']]
    below = [r for r in rows if not r['above']]

    # 신규 돌파
    fresh = [r for r in above if r['fresh']]
    if fresh:
        blocks.append(_section('*★ 신규 돌파 종목* — 월말 확정 시 진입 검토'))
        for r in fresh:
            blocks.append(_section(
                f'`{r["ticker"]}` *{r["name"]}*  |  {r["close"]:,.2f}  |  '
                f'10이평 대비 *+{r["pct"]}%*  |  거래량 {r["vol_r"]:.1f}x'
            ))
        blocks.append(_divider())

    # 신규 이탈 (경보)
    broke = [r for r in below if r['broke']]
    if broke:
        blocks.append(_section('*🚨 신규 이탈 경보* — 월말 종가 확정 후 매도 결정'))
        for r in broke:
            blocks.append(_section(
                f'`{r["ticker"]}` *{r["name"]}*  |  {r["close"]:,.2f}  |  '
                f'10이평 대비 *{r["pct"]}%*'
            ))
        blocks.append(_divider())

    # 급락 경보 (이평 대비 -15% 이상)
    crash = [r for r in below if r['pct'] <= CRASH_PCT]
    if crash:
        blocks.append(_section(f'*🔴 급락 경보* (10이평 대비 {CRASH_PCT}% 이하)'))
        for r in crash:
            blocks.append(_section(
                f'`{r["ticker"]}` *{r["name"]}*  →  *{r["pct"]}%*'
            ))
        blocks.append(_divider())

    # 눌림목 후보 (이평 위 +5% 이내)
    dips = [r for r in above if not r['fresh'] and 0 < r['pct'] <= ZONE_PCT]
    if dips:
        blocks.append(_section(f'*▲ 눌림목 후보* (10이평 +{ZONE_PCT}% 이내) — 월말 확인 후 진입 고려'))
        fields = []
        for r in dips:
            fields.append(f'`{r["ticker"]}` {r["name"]}  *+{r["pct"]}%*')
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    # 전체 현황 요약
    blocks.append(_section(
        f'*전체 현황*\n'
        f'• 10이평 위 (매수가능): *{len(above)}개*\n'
        f'• 10이평 아래 (매수금지): *{len(below)}개*\n'
        f'• 총 {len(rows)}개 종목 스캔 완료'
    ))
    blocks.append(_section('_실제 매매 결정은 월말 종가 확정 후 월말 알림을 기준으로 하세요_'))

    if port_rows is not None:
        blocks.extend(build_portfolio_section(port_rows, is_monthly=False))

    if etf_rows is not None:
        blocks.extend(build_etf_section(etf_rows, is_monthly=False))

    return blocks


# ── 2. 매달 말일 — 매매 결정 알림 ───────────────────────────
def build_monthly_alert(rows: list, etf_rows: list = None, port_rows: list = None) -> list:
    today_str = datetime.today().strftime('%Y.%m.%d')
    ym = datetime.today().strftime('%Y년 %m월')
    blocks = [
        _header(f'{ym} 월봉 매매 결정  {today_str}'),
        _section(
            '*월봉 10이평선 종가 확정 — 이달 매매 결정 알림*\n'
            '>✅ 이 알림 기준으로 매수/매도를 결정하세요.'
        ),
        _divider(),
    ]

    above = [r for r in rows if r['above']]
    below = [r for r in rows if not r['above']]

    # ── 매수 신호 ──
    fresh  = [r for r in above if r['fresh']]
    dip    = [r for r in above if not r['fresh'] and r['pct'] <= ZONE_PCT]
    trend  = [r for r in above if not r['fresh'] and ZONE_PCT < r['pct'] <= 15]
    high   = [r for r in above if r['pct'] > 15]

    if fresh:
        blocks.append(_section('*★ 매수 진입 — 이번달 10이평 신규 돌파*'))
        for r in fresh:
            blocks.append(_fields([
                f'*종목:* `{r["ticker"]}` {r["name"]}',
                f'*섹터:* {r["sector"]}',
                f'*현재가:* {r["close"]:,.2f}',
                f'*10이평:* {r["ma"]:,.2f}',
                f'*대비:* +{r["pct"]}%',
                f'*거래량:* {r["vol_r"]:.1f}x',
            ]))
        blocks.append(_divider())

    if dip:
        blocks.append(_section(f'*▲ 매수 적극 고려 — 눌림목 (+{ZONE_PCT}% 이내)*'))
        for r in dip:
            blocks.append(_section(
                f'`{r["ticker"]}` *{r["name"]}*  |  '
                f'{r["close"]:,.2f} / 10이평 {r["ma"]:,.2f}  |  *+{r["pct"]}%*'
            ))
        blocks.append(_divider())

    if trend:
        blocks.append(_section('*● 보유 홀딩 — 추세 진행 중 (+5~15%)*'))
        fields = [f'`{r["ticker"]}` {r["name"]}  +{r["pct"]}%' for r in trend]
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    if high:
        blocks.append(_section('*△ 보유 홀딩 / 신규 매수 금지 — 고점권 (+15% 초과)*'))
        fields = [f'`{r["ticker"]}` {r["name"]}  *+{r["pct"]}%*' for r in high]
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    # ── 매도 신호 ──
    broke = [r for r in below if r['broke']]
    others_below = [r for r in below if not r['broke']]

    if broke:
        blocks.append(_section('*🚨 즉시 매도 — 이번달 10이평 하향 이탈*'))
        for r in broke:
            blocks.append(_fields([
                f'*종목:* `{r["ticker"]}` {r["name"]}',
                f'*섹터:* {r["sector"]}',
                f'*현재가:* {r["close"]:,.2f}',
                f'*10이평:* {r["ma"]:,.2f}',
                f'*대비:* {r["pct"]}%',
                f'*조치:* 즉시 매도 (예외 없음)',
            ]))
        blocks.append(_divider())

    if others_below:
        blocks.append(_section('*❌ 매수 금지 종목 — 10이평 아래 (관망)*'))
        fields = [f'`{r["ticker"]}` {r["name"]}  {r["pct"]}%' for r in others_below]
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    # 요약
    blocks.append(_section(
        f'*이달 결론*\n'
        f'• 신규 매수 진입: *{len(fresh)}종목*\n'
        f'• 눌림목 매수 고려: *{len(dip)}종목*\n'
        f'• 홀딩 유지: *{len(trend) + len(high)}종목*\n'
        f'• 즉시 매도: *{len(broke)}종목*\n'
        f'• 매수 금지: *{len(others_below)}종목*'
    ))
    blocks.append(_section(
        '_원칙: 10이평 위 → 홀딩 / 10이평 이탈 → 즉시 매도 / 감정적 판단 금지_'
    ))

    if port_rows is not None:
        blocks.extend(build_portfolio_section(port_rows, is_monthly=True))

    if etf_rows is not None:
        blocks.extend(build_etf_section(etf_rows, is_monthly=True))

    return blocks


# ── 실행 진입점 ─────────────────────────────────────────────
def run(mode: str = 'auto'):
    """
    mode: 'auto'    → 오늘 날짜 기준으로 자동 판단
          'weekly'  → 강제 주간 알림
          'monthly' → 강제 월말 알림
          'test'    → 두 알림 모두 콘솔 출력 (Slack 미전송)
    """
    print(f'\n스캔 시작... ({datetime.now().strftime("%Y-%m-%d %H:%M")})')
    rows = scan_all()
    print(f'개별종목 스캔 완료: {len(rows)}종목')
    etf_rows = scan_etfs()
    print(f'테마ETF 스캔 완료: {len(etf_rows)}개')

    print('포트폴리오 읽는 중...')
    holdings  = read_portfolio()
    port_rows = scan_portfolio(holdings)
    print(f'보유종목 스캔 완료: {len(port_rows)}종목')

    today = date.today()
    is_friday   = today.weekday() == 4
    is_monthend = is_last_trading_day()

    if mode == 'test':
        print('\n[테스트] 주간 알림 미리보기')
        _print_blocks(build_weekly_alert(rows, etf_rows, port_rows))
        print('\n[테스트] 월말 알림 미리보기')
        _print_blocks(build_monthly_alert(rows, etf_rows, port_rows))
        return

    sent = False

    # 월말 알림 우선 (주간보다 중요)
    if mode == 'monthly' or (mode == 'auto' and is_monthend):
        blocks = build_monthly_alert(rows, etf_rows, port_rows)
        ym = datetime.today().strftime('%Y.%m')
        ok = send_slack(blocks, text=f'[{ym} 월말] 매매 결정 알림')
        print(f'월말 매매 결정 알림 전송: {"완료" if ok else "실패"}')
        sent = True

    if mode == 'weekly' or (mode == 'auto' and is_friday and not sent):
        blocks = build_weekly_alert(rows, etf_rows, port_rows)
        ok = send_slack(blocks, text=f'[주간] 모니터링 알림')
        print(f'주간 모니터링 알림 전송: {"완료" if ok else "실패"}')


def _print_blocks(blocks: list):
    """콘솔 미리보기용"""
    for b in blocks:
        t = b.get('type', '')
        if t == 'header':
            print(f'\n=== {b["text"]["text"]} ===')
        elif t == 'section':
            txt = b.get('text', {}).get('text', '')
            fields = b.get('fields', [])
            if txt:
                print(txt.replace('*', '').replace('`', '').replace('_', ''))
            for fld in fields:
                print(' ', fld['text'].replace('*', ''))
        elif t == 'divider':
            print('-' * 60)


if __name__ == '__main__':
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else 'auto'
    run(mode)
