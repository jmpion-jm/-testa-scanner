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
GSHEET_CREDS  = os.environ.get(
    'GSHEET_CREDS_PATH',
    r'C:\Users\user\.claude\secret-footing-453908-u2-67fee5c1f2f0.json'
)
GSHEET_ID     = '1Xmj6R332n1IvgA6fJ0c5YcOvO6gR_OeDHYmVuTTIzZE'
GSHEET_SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly',
                 'https://www.googleapis.com/auth/drive.readonly']
# 채권·현금은 MA 분석 제외 (B&H 유지)
EXCLUDE_ASSETS = {'채권', '현금', '예수금'}

# ── 탑다운 글로벌 지수 (성승현 매매법 1원칙) ──────────────────
GLOBAL_MARKETS = {
    '🇺🇸 미국': {
        '나스닥': '^IXIC', 'S&P500': '^GSPC', '다우': '^DJI',
    },
    '🏭 제조업국가': {
        '독일DAX': '^GDAXI', '일본니케이': '^N225', '대만가권': '^TWII',
    },
    '🇰🇷 한국': {
        '코스피': '^KS11', '코스닥': '^KQ11',
    },
}

# ── 설정 로드 ────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

with open(CONFIG_PATH, encoding='utf-8') as f:
    CFG = json.load(f)

WEBHOOK_URL         = CFG['slack_webhook_url']
WEBHOOK_URL_PENSION = CFG.get('slack_webhook_url_pension', '')
STOCKS       = CFG['stocks']          # {ticker: [name, sector]}
THEME_ETFS   = CFG['theme_etfs']      # {ticker.KS: [name, theme]}
SAFE_HAVEN   = CFG['safe_haven_etf']  # 357870.KS
SAFE_NAME    = CFG['safe_haven_name'] # TIGER CD금리
MA_PERIOD    = CFG['ma_period']       # 10
CRASH_PCT    = CFG['alert_thresholds']['crash_alert_pct']   # -15
ZONE_PCT     = CFG['alert_thresholds']['breakout_zone_pct'] # 5

PENSION_KEYWORDS = ['DC', 'IRP', '연금', '퇴직']

def _is_pension(accounts: list) -> bool:
    return any(any(k in acc for k in PENSION_KEYWORDS) for acc in accounts)


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


def _check_nollim_buyable(df: pd.DataFrame, pct: float) -> dict:
    """성승현 눌림목 추매 조건 판별
    조건①: 현재가가 MA10 +5% 이내 (눌림목 구간)
    조건②: 최근 12개월 내 월봉 거래량 3배↑ 급등월 존재 (돌반지 1차 신호)
    조건③: 현재 눌림목 구간 거래량이 낮음 (세력 이탈 없음)
    세 조건 모두 충족 시 → 추매 신호
    """
    if len(df) < 14 or pct > 5 or pct < 0:
        return {'buyable': False, 'reason': '눌림목 구간 아님'}

    avg_vol     = float(df['Volume'].iloc[-13:-1].mean()) or 1
    surge_max   = float(df['Volume'].iloc[-13:-1].max())
    surge_ratio = surge_max / avg_vol

    # 최근 12개월 내 3배 이상 거래량 급등월 존재 여부
    has_surge = surge_ratio >= 3.0

    # 현재월 거래량 비율 (눌림목에선 낮아야 세력 이탈 없음)
    curr_vol_r = float(df['Volume'].iloc[-1]) / avg_vol

    # 눌림목 거래량 기준: 평균 1.5배 미만 = 세력 여전히 보유
    pullback_quiet = curr_vol_r < 1.5

    buyable = has_surge and pullback_quiet
    return {
        'buyable':       buyable,
        'surge_ratio':   round(surge_ratio, 1),
        'curr_vol_r':    round(curr_vol_r, 1),
        'has_surge':     has_surge,
        'pullback_quiet': pullback_quiet,
        'reason': (
            '✅ 추매 조건 충족' if buyable else
            '❌ 거래량 급등 이력 없음' if not has_surge else
            '⚠️ 눌림목 거래량 과다 (세력 이탈 의심)'
        ),
    }


def _is_decline3(df: pd.DataFrame) -> bool:
    """최근 3개월 연속 월봉 종가 하락 여부 (3개월 연속 하락 경고)"""
    if len(df) < 3:
        return False
    c = df['Close']
    return bool(float(c.iloc[-1]) < float(c.iloc[-2]) < float(c.iloc[-3]))


def _is_death_candle(df: pd.DataFrame) -> bool:
    """저승사자 캔들: MA10 이탈 직후 출현한 장대 음봉.
    - 이번달 종가가 MA10 아래(이탈)
    - 이번달 음봉(시가 > 종가)이며 몸통이 큰(>=8%) 장대봉
    """
    if 'Open' not in df.columns or 'MA' not in df.columns or len(df) < 2:
        return False
    last = df.iloc[-1]
    o, c, ma = float(last['Open']), float(last['Close']), float(last['MA'])
    if any(pd.isna(v) for v in (o, c, ma)) or o <= 0:
        return False
    below   = c < ma                       # MA10 이탈
    bearish = c < o                         # 음봉
    big     = (o - c) / o * 100 >= 8.0      # 장대(몸통 8% 이상)
    return bool(below and bearish and big)


def topdown_analysis() -> dict:
    """글로벌 지수 월봉 MA10 탑다운 분석 (성승현 1원칙)"""
    results = {}
    for region, markets in GLOBAL_MARKETS.items():
        results[region] = {}
        for name, ticker in markets.items():
            try:
                t  = yf.Ticker(ticker)
                df = t.history(period='2y', interval='1mo', auto_adjust=True)
                df.index = df.index.tz_localize(None) if df.index.tz else df.index
                df = df[['Close']].dropna()
                if len(df) < MA_PERIOD + 2:
                    results[region][name] = {'error': '데이터부족'}
                    continue
                df['MA10'] = df['Close'].rolling(MA_PERIOD).mean()
                df = df.dropna()
                latest = df.iloc[-1]
                close  = float(latest['Close'])
                ma10   = float(latest['MA10'])
                results[region][name] = {
                    'above': close > ma10,
                    'pct':   round((close - ma10) / ma10 * 100, 1),
                }
            except Exception as e:
                results[region][name] = {'error': str(e)}
    return results


def topdown_regime(td: dict) -> tuple:
    """(레짐 라벨, 상승 개수, 전체 개수) 반환"""
    total, bullish = 0, 0
    for markets in td.values():
        for data in markets.values():
            if 'error' not in data:
                total += 1
                if data['above']:
                    bullish += 1
    if total == 0:
        return ('⚪ 데이터없음', 0, 0)
    ratio = bullish / total * 100
    if ratio >= 70:
        label = '🟢 강세장'
    elif ratio >= 50:
        label = '🟡 혼조장'
    else:
        label = '🔴 약세장'
    return (label, bullish, total)


def build_topdown_section(td: dict) -> list:
    """탑다운 분석 결과 Slack 블록 생성"""
    regime, bullish, total = topdown_regime(td)
    ratio  = int(bullish / total * 100) if total else 0
    lines  = [f'*📊 탑다운 시장 분석 — {regime} ({bullish}/{total}, {ratio}%)*']

    for region, markets in td.items():
        parts = []
        for name, data in markets.items():
            if 'error' in data:
                parts.append(f'{name}:⚠️')
            else:
                icon    = '✅' if data['above'] else '❌'
                pct_str = f"+{data['pct']:.1f}%" if data['pct'] >= 0 else f"{data['pct']:.1f}%"
                parts.append(f'{icon}{name}({pct_str})')
        lines.append(f'{region}: ' + '  '.join(parts))

    if ratio >= 70:
        lines.append('→ 적극 매수 가능')
    elif ratio >= 50:
        lines.append('→ 선별적 접근, 신규 진입 신중')
    else:
        lines.append('⚠️ *신규 매수 자제 — 현금 비중 확대 권고*')

    return [_section('\n'.join(lines)), _divider()]


def send_slack(blocks: list, text: str = "주식 알림", url: str = None):
    """Slack Block Kit 메시지 전송 (url 미지정 시 기본 webhook 사용)"""
    target = url or WEBHOOK_URL
    if not target or target.startswith('여기에'):
        print('[Slack 미설정] config.json의 slack_webhook_url을 입력해주세요.')
        return False

    payload = json.dumps({'text': text, 'blocks': blocks}).encode('utf-8')
    req = urllib.request.Request(
        target,
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
            df = df[['Open','High','Low','Close']].dropna()

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
                'close':    round(close, 2),
                'ma':       round(ma, 2),
                'pct':      round(pct, 1),
                'above':    above,
                'fresh':    fresh,
                'broke':    broke,
                'decline3': _is_decline3(df),
                'death':    _is_death_candle(df),
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
            df = df[['Open','Close','Volume']].dropna()
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
                decline3=_is_decline3(df), death=_is_death_candle(df),
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

            # 성승현 눌림목 추매 조건 판별
            nollim = _check_nollim_buyable(df, pct)

            rows.append(dict(
                ticker=ticker, name=name, sector=sector,
                close=round(close, 2), ma=round(ma, 2),
                pct=round(pct, 1), above=above,
                fresh=fresh, broke=broke, vol_r=round(vol_r, 2),
                decline3=_is_decline3(df), death=_is_death_candle(df),
                nollim=nollim,
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
            # 저승사자 캔들: 이탈 직후 장대 음봉 = 매도 이미 늦음, 무조건 청산
            death = '\n💀 *저승사자 캔들* (이탈 직후 장대 음봉) — 지체 없이 전량 매도' if r.get('death') else ''
            blocks.append(_section(
                f'`{r["name"]}` ({accs})\n'
                f'현재가 {r["close"]:,.0f}  /  MA10 {r["ma"]:,.0f}  /  *{r["pct"]:+.1f}%*\n'
                f'→ {"전량 매도 후 CD금리 대기" if is_monthly else "월말 종가 확인 후 결정"}'
                + death
            ))

    # 저승사자 캔들이 떴으나 위 broke에 안 잡힌 경우(이미 이전에 이탈)도 별도 경고
    death_only = [r for r in port_rows if r.get('death') and not r['broke']]
    if death_only:
        blocks.append(_section('*💀 저승사자 캔들 — MA10 아래 장대 음봉 (즉시 정리)*'))
        for r in death_only:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(f'`{r["name"]}` ({accs})  *{r["pct"]:+.1f}%*  → 전량 매도'))

    # 신규 돌파 — 지지권(+5% 이내)만 추가매수 적극 검토 / 추세권(+5~15%) 보유 / 고점권(+15%초과) 금지
    fresh_dip   = [r for r in port_rows if r['fresh'] and r['pct'] <= ZONE_PCT]
    fresh_trend = [r for r in port_rows if r['fresh'] and ZONE_PCT < r['pct'] <= 15]
    fresh_high  = [r for r in port_rows if r['fresh'] and r['pct'] > 15]

    if fresh_dip:
        label = '★ 추가매수 검토 — MA10 재돌파 (지지권 +5% 이내, 최적 진입)' if is_monthly else '★ MA10 돌파 (월말 확정 후 검토)'
        blocks.append(_section(f'*{label}*'))
        for r in fresh_dip:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(f'`{r["name"]}` ({accs})  *{r["pct"]:+.1f}%*'))

    if fresh_trend:
        blocks.append(_section('*● 추세권 홀딩 — MA10 돌파 +5~15%, 보유 유지 (신규 진입 주의)*'))
        for r in fresh_trend:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(f'`{r["name"]}` ({accs})  *{r["pct"]:+.1f}%*  → 보유 유지'))

    if fresh_high:
        blocks.append(_section('*△ 고점권 홀딩 — MA10 돌파했으나 +15% 초과, 신규 매수 금지*'))
        for r in fresh_high:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(f'`{r["name"]}` ({accs})  *{r["pct"]:+.1f}%*  → 보유 유지만'))

    # 이탈 중 (이미 아래)
    below = [r for r in port_rows if not r['above'] and not r['broke'] and not r.get('death')]
    if below:
        blocks.append(_section('*❌ MA10 아래 — 보유 중 주의*'))
        fields = []
        for r in below:
            warn = ' ⚠️3개월연속하락' if r.get('decline3') else ''
            fields.append(f'`{r["name"]}`  *{r["pct"]:+.1f}%*{warn}  ({", ".join(r["accounts"])})')
        blocks.append(_fields(fields[:10]))

    # 정상 홀딩
    holding = [r for r in port_rows if r['above'] and not r['fresh']]
    if holding:
        blocks.append(_section('*● 홀딩 유지 — MA10 위*'))
        fields = []
        for r in holding:
            emoji = '▲' if r['pct'] > 15 else ('→' if r['pct'] > 0 else '▽')
            warn  = ' ⚠️3개월연속하락' if r.get('decline3') else ''
            fields.append(f'{emoji} `{r["name"]}`  *{r["pct"]:+.1f}%*{warn}')
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
def build_weekly_alert(rows: list, etf_rows: list = None, port_rows: list = None,
                       td: dict = None) -> list:
    today_str = datetime.today().strftime('%Y.%m.%d')
    regime    = topdown_regime(td)[0] if td else '⚪ 미확인'
    blocks = [
        _header(f'주간 모니터링  {today_str} (금)  |  {regime}'),
        _section(
            '*월봉 10이평선 기준 주간 점검*\n'
            '>⚠️ 이 알림은 관찰용입니다. 실제 매매는 *월말 알림* 기준으로만 하세요.'
        ),
        _divider(),
    ]
    if td:
        blocks.extend(build_topdown_section(td))

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

    # 눌림목 — 거래량 조건별 분류
    dips = [r for r in above if not r['fresh'] and 0 < r['pct'] <= ZONE_PCT]
    nollim_buy   = [r for r in dips if r.get('nollim', {}).get('buyable')]
    nollim_watch = [r for r in dips if not r.get('nollim', {}).get('buyable')]

    if nollim_buy:
        blocks.append(_section(
            f'*🟢 눌림목 추매 후보* — 거래량 조건 충족 (월말 종가 확인 후 진입)\n'
            f'>과거 월봉 3배↑ + 현재 눌림 거래량 낮음 = 성승현 돌반지 조건'
        ))
        fields = [
            f'`{r["ticker"]}` {r["name"]}  *+{r["pct"]}%*  '
            f'(과거급등 {r.get("nollim",{}).get("surge_ratio",0):.1f}배)'
            for r in nollim_buy
        ]
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    if nollim_watch:
        blocks.append(_section(f'*▲ 눌림목 관찰* — 거래량 조건 미충족 (대기)'))
        fields = [f'`{r["ticker"]}` {r["name"]}  *+{r["pct"]}%*' for r in nollim_watch]
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
def build_monthly_alert(rows: list, etf_rows: list = None, port_rows: list = None,
                        td: dict = None) -> list:
    today_str = datetime.today().strftime('%Y.%m.%d')
    ym = datetime.today().strftime('%Y년 %m월')
    regime, bullish, total = topdown_regime(td) if td else ('⚪ 미확인', 0, 0)
    ratio  = int(bullish / total * 100) if total else 0
    blocks = [
        _header(f'{ym} 월봉 매매 결정  {today_str}  |  {regime}'),
        _section(
            '*월봉 10이평선 종가 확정 — 이달 매매 결정 알림*\n'
            '>✅ 이 알림 기준으로 매수/매도를 결정하세요.'
        ),
        _divider(),
    ]
    if td:
        blocks.extend(build_topdown_section(td))

    above = [r for r in rows if r['above']]
    below = [r for r in rows if not r['above']]

    # ── 매수 신호 ──
    fresh  = [r for r in above if r['fresh']]
    dip    = [r for r in above if not r['fresh'] and r['pct'] <= ZONE_PCT]
    trend  = [r for r in above if not r['fresh'] and ZONE_PCT < r['pct'] <= 15]
    high   = [r for r in above if r['pct'] > 15]

    # 약세장 경고 배너
    if ratio < 50 and (fresh or dip):
        blocks.append(_section(
            '⚠️ *약세장 주의* — 글로벌 지수 50% 미만 상승추세\n'
            '신규 매수 신호가 있어도 소량 진입 또는 관망 권고'
        ))

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
        # 거래량 조건 충족 = 추매 신호 / 미충족 = 관찰
        nollim_buy = [r for r in dip if r.get('nollim', {}).get('buyable')]
        nollim_watch = [r for r in dip if not r.get('nollim', {}).get('buyable')]

        if nollim_buy:
            blocks.append(_section(
                f'*🟢 눌림목 추매 신호 — MA10 +{ZONE_PCT}% 이내 + 거래량 조건 충족*\n'
                f'>성승현 돌반지: 과거 거래량 3배↑ 확인 + 현재 눌림목 거래량 낮음'
            ))
            for r in nollim_buy:
                nl = r.get('nollim', {})
                blocks.append(_fields([
                    f'*종목:* `{r["ticker"]}` {r["name"]}',
                    f'*섹터:* {r["sector"]}',
                    f'*현재가:* {r["close"]:,.2f}',
                    f'*MA10:* {r["ma"]:,.2f}  (*+{r["pct"]}%*)',
                    f'*과거 최대 거래량:* {nl.get("surge_ratio",0):.1f}배↑',
                    f'*현재 눌림목 거래량:* {nl.get("curr_vol_r",0):.1f}배 (조용)',
                ]))
            blocks.append(_divider())

        if nollim_watch:
            blocks.append(_section(f'*▲ 눌림목 관찰 — 거래량 조건 미충족 (대기)*'))
            for r in nollim_watch:
                nl = r.get('nollim', {})
                blocks.append(_section(
                    f'`{r["ticker"]}` *{r["name"]}*  +{r["pct"]}%  |  {nl.get("reason","")}'
                ))
            blocks.append(_divider())

    if trend:
        blocks.append(_section('*● 추세권 — 보유 유지 (+5~15%, 신규 진입 주의)*'))
        fields = [f'`{r["ticker"]}` {r["name"]}  +{r["pct"]}%'
                  + (' ⚠️3개월연속하락' if r.get('decline3') else '') for r in trend]
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    if high:
        blocks.append(_section('*△ 보유 홀딩 / 신규 매수 금지 — 고점권 (+15% 초과)*'))
        fields = [f'`{r["ticker"]}` {r["name"]}  *+{r["pct"]}%*'
                  + (' ⚠️3개월연속하락' if r.get('decline3') else '') for r in high]
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    # ── 매도 신호 ──
    broke = [r for r in below if r['broke']]
    others_below = [r for r in below if not r['broke']]

    if broke:
        blocks.append(_section('*🚨 즉시 매도 — 이번달 10이평 하향 이탈*'))
        for r in broke:
            action = '즉시 전량 매도 (예외 없음)'
            if r.get('death'):
                action = '💀 저승사자 캔들 — 지체 없이 전량 매도 (이미 늦음)'
            blocks.append(_fields([
                f'*종목:* `{r["ticker"]}` {r["name"]}',
                f'*섹터:* {r["sector"]}',
                f'*현재가:* {r["close"]:,.2f}',
                f'*10이평:* {r["ma"]:,.2f}',
                f'*대비:* {r["pct"]}%',
                f'*조치:* {action}',
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

    # 실행 체크리스트 (월말에만)
    blocks.extend(build_action_checklist(rows, etf_rows or [], port_rows or []))

    return blocks


# ── 월말 실행 타임라인 ────────────────────────────────────────
def build_action_checklist(rows: list, etf_rows: list, port_rows: list) -> list:
    """시간·계좌·행동 3열 타임라인 자동 생성"""

    def is_korean(ticker: str) -> bool:
        return bool(ticker) and (ticker.isdigit() or ticker.endswith('.KS'))

    # 분류
    sell_kr  = [r for r in port_rows if r.get('broke') and is_korean(r['ticker'])]
    sell_us  = [r for r in port_rows if r.get('broke') and not is_korean(r['ticker'])]
    sell_etf = [r for r in etf_rows  if r.get('broke')]
    buy_us   = [r for r in rows      if r.get('above') and r.get('pct', 99) <= 10
                and (r.get('fresh') or r.get('pct', 99) <= 5)]
    buy_etf  = [r for r in etf_rows  if r.get('fresh')]

    # Testa 신호 읽기 (당일 저장 파일)
    testa_sigs = []
    try:
        sig_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testa_signals.json')
        with open(sig_path, encoding='utf-8') as f:
            testa_sigs = json.load(f).get('signals', [])
    except Exception:
        pass

    if not any([sell_kr, sell_us, sell_etf, buy_us, buy_etf, testa_sigs]):
        return []

    # 타임라인 행 구성 (시간, 계좌, 행동)
    timeline = []

    # 한국 시장 09:00
    for r in sell_kr:
        accs = ', '.join(r.get('accounts', [r.get('account', '농협')]))
        timeline.append(('09:00', accs, f"🔴 {r['name']} 전량 매도  (MA10 이탈)"))

    # 테스타 신호 09:10
    if testa_sigs:
        names = ' + '.join(s['name'] for s in testa_sigs)
        timeline.append(('09:10', '자동알림', f"🔔 {names} 시가 + 수량 확인"))
        for s in testa_sigs:
            qty  = s.get('qty', '?')
            stop = f"{s.get('stop', 0):,}원"
            timeline.append(('09:10~', '농협',
                f"🟢 {s['name']} {qty}주 매수  손절 {stop} 지정매도 등록"))

    # 미국 시장 22:30 — 매도 먼저
    for r in sell_us:
        accs = ', '.join(r.get('accounts', [r.get('account', '미래에셋')]))
        timeline.append(('22:30', accs, f"🔴 {r['ticker']} {r['name']} 전량 매도  (MA10 이탈)"))

    # 미국 시장 22:30~ — 매수
    for r in buy_us[:3]:
        sig = '신규돌파' if r.get('fresh') else '지지권'
        timeline.append(('22:30~', '미래에셋',
            f"🟢 {r['ticker']} {r['name']} 매수  ({sig} {r['pct']:+.1f}%)"))

    # 연금계좌 (시간 무관)
    for r in sell_etf:
        timeline.append(('연금계좌', 'DC/IRP/연금', f"🔴 {r['name']} → CD금리 교체"))
    for r in buy_etf:
        timeline.append(('연금계좌', 'DC/IRP/연금', f"🟢 {r['name']} 매수  (신규돌파)"))

    # 타임라인 테이블 포맷
    header = f'{"시간":<9} {"계좌":<12} 행동'
    sep    = '─' * 60
    body   = '\n'.join(f'{t:<9} {a:<12} {act}' for t, a, act in timeline)
    table  = f'```\n{header}\n{sep}\n{body}\n```'

    return [
        _divider(),
        _header('📋 이달 실행 타임라인'),
        _section(table),
        _section('_⚠️ 매도 먼저 → 매수. 손절가는 진입 즉시 지정매도 등록_'),
    ]


# ── 미국 신호 저장 (영웅문 관심종목 등록용) ───────────────────
def _save_us_signals(rows: list):
    """신규돌파 + 지지권 종목을 us_signals.json 으로 저장"""
    signals = [
        {'ticker': r['ticker'], 'name': r['name'], 'pct': r['pct']}
        for r in rows
        if r['above'] and (r['fresh'] or r['pct'] <= ZONE_PCT)
    ]
    path = os.path.join(BASE_DIR, 'us_signals.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'date': date.today().strftime('%Y-%m-%d'), 'signals': signals},
                  f, ensure_ascii=False, indent=2)
    if signals:
        print(f'미국 신호 저장: {len(signals)}종목 → us_signals.json')


# ── 실행 진입점 ─────────────────────────────────────────────
def run(mode: str = 'auto'):
    """
    mode: 'auto'    → 오늘 날짜 기준으로 자동 판단
          'weekly'  → 강제 주간 알림
          'monthly' → 강제 월말 알림
          'test'    → 두 알림 모두 콘솔 출력 (Slack 미전송)
    """
    print(f'\n스캔 시작... ({datetime.now().strftime("%Y-%m-%d %H:%M")})')

    # ── 탑다운 분석 (성승현 1원칙) ──────────────────────────────
    print('탑다운 글로벌 지수 분석 중...')
    td = topdown_analysis()
    regime, bullish, total = topdown_regime(td)
    ratio = int(bullish / total * 100) if total else 0
    print(f'탑다운 완료: {regime} ({bullish}/{total}, {ratio}%)')

    rows = scan_all()
    print(f'개별종목 스캔 완료: {len(rows)}종목')
    _save_us_signals(rows)
    etf_rows = scan_etfs()
    print(f'테마ETF 스캔 완료: {len(etf_rows)}개')

    print('포트폴리오 읽는 중...')
    holdings  = read_portfolio()
    port_rows = scan_portfolio(holdings)
    print(f'보유종목 스캔 완료: {len(port_rows)}종목')

    # 포트폴리오 채널 분리: 연금(DC/IRP/연금) vs 일반계좌
    port_pension = [r for r in port_rows if _is_pension(r.get('accounts', [r.get('account', '')]))]
    port_stocks  = [r for r in port_rows if not _is_pension(r.get('accounts', [r.get('account', '')]))]

    today = date.today()
    is_friday   = today.weekday() == 4
    is_monthend = is_last_trading_day()

    if mode == 'test':
        print('\n[테스트] 개별종목 주간 알림')
        _print_blocks(build_weekly_alert(rows, port_rows=port_stocks, td=td))
        print('\n[테스트] 연금계좌 ETF 주간 알림')
        _print_blocks(build_weekly_alert([], etf_rows=etf_rows, port_rows=port_pension, td=td))
        return

    sent = False

    # 월말 알림 우선 (주간보다 중요)
    if mode == 'monthly' or (mode == 'auto' and is_monthend):
        ym = datetime.today().strftime('%Y.%m')

        # ① 김학주교수 채널 — 개별종목
        blocks = build_monthly_alert(rows, port_rows=port_stocks, td=td)
        ok = send_slack(blocks, text=f'[{ym} 월말] 개별종목 매매 결정', url=WEBHOOK_URL)
        print(f'개별종목 월말 알림 전송: {"완료" if ok else "실패"}')

        # ② 연금계좌 채널 — ETF
        if WEBHOOK_URL_PENSION:
            blocks_p = build_monthly_alert([], etf_rows=etf_rows, port_rows=port_pension, td=td)
            ok2 = send_slack(blocks_p, text=f'[{ym} 월말] 연금계좌 ETF 점검', url=WEBHOOK_URL_PENSION)
            print(f'연금계좌 월말 알림 전송: {"완료" if ok2 else "실패"}')
        sent = True

    if mode == 'weekly' or (mode == 'auto' and is_friday and not sent):
        # ① 김학주교수 채널 — 개별종목
        blocks = build_weekly_alert(rows, port_rows=port_stocks, td=td)
        ok = send_slack(blocks, text='[주간] 개별종목 모니터링', url=WEBHOOK_URL)
        print(f'개별종목 주간 알림 전송: {"완료" if ok else "실패"}')

        # ② 연금계좌 채널 — ETF
        if WEBHOOK_URL_PENSION:
            blocks_p = build_weekly_alert([], etf_rows=etf_rows, port_rows=port_pension, td=td)
            ok2 = send_slack(blocks_p, text='[주간] 연금계좌 ETF 모니터링', url=WEBHOOK_URL_PENSION)
            print(f'연금계좌 주간 알림 전송: {"완료" if ok2 else "실패"}')


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
