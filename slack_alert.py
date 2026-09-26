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
    # 원서 7장 탑다운 순서(p.393~397): 미국 → 유럽 → 아시아·제조업국 → 자원부국 → 한국.
    # (2026-09-26 원서 기준 보강 — 이전 판은 유럽·자원국·튀르키예·중국이 빠져 있었음. 데이터 없는 지수는 '데이터부족' 표시)
    '🇺🇸 미국': {
        '나스닥': '^IXIC', 'S&P500': '^GSPC', '다우': '^DJI',
    },
    '🇪🇺 유럽': {
        '유로스톡스50': '^STOXX50E', '독일DAX': '^GDAXI', '영국FTSE': '^FTSE', '프랑스CAC': '^FCHI',
    },
    '🏭 아시아·제조업국': {
        '대만가권': '^TWII', '일본니케이': '^N225', '튀르키예BIST100': 'XU100.IS', '중국상해': '000001.SS',
    },
    '⛏️ 자원부국': {
        '브라질보베스파': '^BVSP', '호주ASX200': '^AXJO',
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
    """미국 기준 이번 달 마지막 평일이고 그날 장이 이미 마감됐는지.

    2026-09-26 수정: 예전엔 날짜만 봤는데, 월말 실행이 KST 16:10(미국 개장 전)이라
    말일 종가가 아니라 전날 종가로 월말 매도 결정을 내리고 있었다. 이제 마감 이후
    (UTC 20시~다음날 06시)에만 True — 판정 로직은 market_time.py 참고."""
    import market_time as mt
    return mt.is_monthend_after_close()


def fetch(ticker: str, period='3y', interval='1mo') -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open','High','Low','Close','Volume']].dropna()


def _check_nollim_buyable(df: pd.DataFrame, pct: float = None) -> dict:
    """눌림 거래량 정보 — 원서 5장 p.364 "눌림목 구간의 거래량은 상승구간 최대 거래량의 1/7~1/20 수준까지면
    금상첨화". 2026-09-26 원서 원칙 전환: 매수 여부는 book_patterns.buy_signal(돌파/지지)이 정하고, 이 함수는
    지지 신호의 질(거래량)을 표시만 한다. 이전 판의 "10이평 대비 0~+5%" 구간 조건(사용자 변형)은 제거.
    반환 키: buyable(= 거래량 1/7 이하 충족), vol_ratio, vol_frac, reason"""
    if len(df) < 14:
        return {'buyable': False, 'reason': '데이터 부족'}
    try:
        import book_patterns as bkp
        r = bkp.pullback_volume(bkp.prepare(df[['Open', 'High', 'Low', 'Close', 'Volume']]))
    except Exception as e:
        return {'buyable': False, 'reason': f'판정 오류: {e}'}
    if r is None:
        return {'buyable': False, 'reason': '눌림 아님(상승구간 최고가 진행 중)'}
    frac = f"1/{1 / r['ratio']:.0f}" if r['ratio'] > 0 else '0'
    return {
        'buyable': r['ideal'],
        'vol_ratio': r['ratio'],
        'vol_frac': frac,
        'reason': (f"눌림 거래량 {frac} ✅ (원서 p.364 금상첨화 1/7 이하)" if r['ideal'] else
                   f"눌림 거래량 {frac} (원서 기준 1/7보다 많음)"),
    }


def _book_sig(df: pd.DataFrame):
    """마지막 월봉의 원서 매수 신호('돌파'=후킹 p.256 / '지지'=10이평 지지 반등 p.340 / None).
    월말 알림에선 마지막 봉 = 확정된 이번 달, 주간 알림에선 진행 중인 달(잠정)."""
    try:
        import book_patterns as bkp
        bd = bkp.prepare(df[['Open', 'High', 'Low', 'Close', 'Volume']])
        return bkp.buy_signal(bd, len(bd) - 1)
    except Exception:
        return None


def _book_box(df: pd.DataFrame) -> bool:
    """박스권 안(상단 돌파 전, 원서 p.309) — 매수 신호의 후순위 표시용(book_patterns.in_box)."""
    try:
        import book_patterns as bkp
        bd = bkp.prepare(df[['Open', 'High', 'Low', 'Close', 'Volume']])
        return bkp.in_box(bd, len(bd) - 1)
    except Exception:
        return False


BOX_MARK = '  📦박스권 안(상단 돌파 전, p.309)'


def _is_decline3(df: pd.DataFrame) -> bool:
    """최근 3개월 연속 월봉 종가 하락 여부 (3개월 연속 하락 경고)"""
    if len(df) < 3:
        return False
    c = df['Close']
    return bool(float(c.iloc[-1]) < float(c.iloc[-2]) < float(c.iloc[-3]))


def _is_death_candle(df: pd.DataFrame) -> bool:
    """저승사자 캔들: 10이평이 뚫리는 지점에 매달리는 긴 장대음봉(원서 p.262, p.264).
    장대 기준은 원서 p.216 "몸통 길이가 전일 대비 5~7% 이상"(하한 5%), 연속 음봉은 합쳐서 본다(p.265 카카오
    "음봉 세 개를 합치면 저승사자 캔들"). (2026-09-26 교체 — 이전 판의 "몸통 4%"는 원서에 없는 값)"""
    if 'Open' not in df.columns or 'MA' not in df.columns or len(df) < 2:
        return False
    last = df.iloc[-1]
    if pd.isna(last['MA']) or float(last['Close']) >= float(last['MA']):
        return False
    try:
        import book_patterns as bkp
        bd = bkp.prepare(df[['Open', 'High', 'Low', 'Close', 'Volume']])
        return bool(bkp.is_big_bear(bd, len(bd) - 1))
    except Exception:
        return False


def _check_jangdae_zone(df: pd.DataFrame) -> dict:
    """장대양봉 4등분선(원서 p.219~223). 가장 최근 장대양봉(연속 양봉 합산)의 몸통 기준 현재 종가 위치.
    (2026-09-26 교체 — 이전 판은 "최근 12개월 중 가장 큰 장대양봉" 기준이었고 25% 아래를 "매도 검토"라 표시했음.
    원서는 장대양봉이 선 직후부터 사등분해서 본다. 이 매매법의 매도는 월봉 10이평 이탈뿐이므로 여기선 경고만 한다.)"""
    try:
        import book_patterns as bkp
        r = bkp.four_division(bkp.prepare(df[['Open', 'High', 'Low', 'Close', 'Volume']]))
    except Exception:
        return {}
    if not r:
        return {}
    warn = r['zone'] in ('매입원가 훼손', '절대자리 훼손')
    return {
        'zone': r['zone'], 'label': r['label'],
        'warn': warn, 'sell': False, 'energy0': r['zone'] == '절대자리 훼손',
        'q1': round(r['q1'], 2), 'q2': round(r['q2'], 2), 'q3': round(r['q3'], 2),
        'body_pct': r['body_pct'],
    }


def _vol_quality(vol_r: float) -> str:
    """거래량 표시(6개월 평균 대비). 원서 p.365 "후킹 캔들에는 거래량이 많이 수반되는 것이 좋다" — 수치 등급은
    원서에 없어 평균 이상/미만만 표시(2026-09-26, 이전 판의 2.0배 '강한신호'/1.5배 '양호' 등급 제거)."""
    return '평균 이상 ✅(p.365)' if vol_r >= 1.0 else '평균 미만'


def get_exchange_rate() -> dict:
    """원/달러 환율 현황 (주봉 MA10 기준)"""
    try:
        df = yf.Ticker('USDKRW=X').history(period='1y', interval='1wk', auto_adjust=True)
        df = df[['Close']].dropna()
        if len(df) < 12:
            return {}
        df['MA10'] = df['Close'].rolling(10).mean()
        df = df.dropna()
        rate    = float(df['Close'].iloc[-1])
        ma10    = float(df['MA10'].iloc[-1])
        pct     = (rate - ma10) / ma10 * 100
        rate_1m = float(df['Close'].iloc[-4]) if len(df) >= 4 else rate
        chg_1m  = (rate - rate_1m) / rate_1m * 100
        return {
            'rate':    round(rate, 1),
            'ma10':    round(ma10, 1),
            'pct':     round(pct, 1),
            'chg_1m':  round(chg_1m, 1),
            'strong':  rate > ma10,   # 달러 강세 여부
        }
    except Exception:
        return {}


def build_exchange_rate_section(fx: dict) -> str:
    """환율 한 줄 요약 텍스트"""
    if not fx:
        return ''
    arrow  = '↑' if fx['strong'] else '↓'
    trend  = '달러강세 (미국주식 원화환산 유리)' if fx['strong'] else '달러약세 (미국주식 원화환산 불리)'
    return (f'💱 원/달러 *{fx["rate"]:,.0f}원*  MA10 {fx["ma10"]:,.0f}원  '
            f'({fx["pct"]:+.1f}%)  1개월 {fx["chg_1m"]:+.1f}%  {arrow} {trend}')


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
    """(요약 라벨, 10이평 위 지수 개수, 전체 개수).
    원서 7장은 "장이 좋으면 적극적으로, 안 좋으면 신규 비중 축소·중단·인버스"(p.394)라고만 하고 수치 기준이 없다.
    (2026-09-26 교체 — 이전 판의 "상승비율 70%↑ 강세장 / 50~70% 혼조장 / 50%↓ 약세장"은 원서에 없는 수치였음.
    매매법_전체_구현명세.md G2) 판단은 사용자가 지수별 상태를 보고 한다."""
    total, bullish = 0, 0
    for markets in td.values():
        for data in markets.values():
            if 'error' not in data:
                total += 1
                if data['above']:
                    bullish += 1
    if total == 0:
        return ('⚪ 데이터없음', 0, 0)
    return (f'10이평 위 지수 {bullish}/{total}', bullish, total)


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

    # 원서 p.394: "장이 좋다면 적극적으로, 안 좋다면 개별주 신규 투자 비중을 줄이거나 아예 안 하면 된다. 혹은 인버스".
    # 수치 기준은 원서에 없다 — 이전 판의 70%/50% 권고문은 원서에 없는 수치라 제거(2026-09-26). 판단은 지수별 상태로.
    lines.append('_원서 p.394: 장이 좋으면 적극적으로, 안 좋으면 신규 비중 축소·중단 (수치 기준 없음 — 지수별 상태로 판단)_')

    return [_section('\n'.join(lines)), _divider()]


def _send_slack_raw(blocks: list, text: str, target: str) -> bool:
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


def send_slack(blocks: list, text: str = "주식 알림", url: str = None):
    """Slack Block Kit 메시지 전송 (url 미지정 시 기본 webhook 사용)

    Slack은 메시지당 블록 50개가 한도라, 종목 수가 많은 달엔 이 한도를
    넘어 전송 자체가 조용히 거부될 수 있음(HTTP 400, 예외로 잡혀서
    스크립트는 계속 진행 → 실패를 눈치채기 어려움). 50개 초과 시
    여러 메시지로 나눠 보내 정보 손실 없이 전달한다.
    """
    target = url or WEBHOOK_URL
    if not target or target.startswith('여기에'):
        print('[Slack 미설정] config.json의 slack_webhook_url을 입력해주세요.')
        return False

    LIMIT = 50
    if len(blocks) <= LIMIT:
        return _send_slack_raw(blocks, text, target)

    chunks = [blocks[i:i + LIMIT] for i in range(0, len(blocks), LIMIT)]
    print(f'[Slack] 블록 {len(blocks)}개 → {LIMIT}개 한도 초과, {len(chunks)}개 메시지로 분할 전송')
    all_ok = True
    for i, chunk in enumerate(chunks, 1):
        ok = _send_slack_raw(chunk, f'{text} ({i}/{len(chunks)})', target)
        all_ok = all_ok and ok
    return all_ok


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
            df = df[['Open','High','Low','Close','Volume']].dropna()

            df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
            latest, prev = df.iloc[-1], df.iloc[-2]

            close = float(latest['Close'])
            ma    = float(latest['MA'])
            pct   = (close - ma) / ma * 100
            above = close > ma
            fresh = bool(float(prev['Close']) < float(prev['MA']) and above)
            broke = bool(float(prev['Close']) > float(prev['MA']) and not above)

            # 원서 하락 패턴(book_patterns — 패턴_구현명세.md). 매도 기준은 그대로 MA10 이탈이고 이건 표시용:
            #   top_warn  = 아직 MA10 위인데 천장 구조(쌍봉/H&S/삼고점)가 이미 형성됨 (p.263 "형태가 보일 때부터 경계")
            #   break_pat = 이번 봉이 MA10 이탈이면 어떤 하락 패턴의 완성이었나 (겹쌍봉·대쌍봉은 장기 약세 경고)
            top_warn = break_pat = None
            try:
                import book_patterns as bkp
                bd = bkp.prepare(df[['Open', 'High', 'Low', 'Close', 'Volume']])
                n = len(bd) - 1
                if above:
                    tw = bkp.bearish_forming(bd)
                    top_warn = tw['pattern'] if tw else None
                elif bkp.is_breakdown(bd, n):
                    bb = bkp.bearish_at(bd, n)
                    names = ([bb['pattern']] if bb else []) + [c['pattern'] for c in bkp.bear_composite_at(bd, n)]
                    break_pat = '+'.join(names) or None
            except Exception as e:
                print(f'  [패턴판정오류] {h["name"]}: {e}')

            rows.append({
                **h,
                'top_warn': top_warn,
                'break_pat': break_pat,
                'close':    round(close, 2),
                'ma':       round(ma, 2),
                'pct':      round(pct, 1),
                'above':    above,
                'fresh':    fresh,
                'broke':    broke,
                'decline3': _is_decline3(df),
                'death':    _is_death_candle(df),
                'nollim':   _check_nollim_buyable(df, pct),
                'jangdae':  _check_jangdae_zone(df),
                'sig':      _book_sig(df),
                'box':      _book_box(df),
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

            # 눌림 거래량(원서 p.364) — 지지 신호의 질 표시용
            nollim = _check_nollim_buyable(df, pct)

            rows.append(dict(
                ticker=ticker, name=name, sector=sector,
                close=round(close, 2), ma=round(ma, 2),
                pct=round(pct, 1), above=above,
                fresh=fresh, broke=broke, vol_r=round(vol_r, 2),
                decline3=_is_decline3(df), death=_is_death_candle(df),
                nollim=nollim,
                jangdae=_check_jangdae_zone(df),
                sig=_book_sig(df),
                box=_book_box(df),
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
            if r.get('break_pat'):
                long_weak = any(k in r['break_pat'] for k in ('겹쌍봉', '대쌍봉'))
                death += (f'\n📉 원서 하락 패턴: *{r["break_pat"]}*'
                          + (' — 원서 p.285·289: 이후 수년간 약세가 대부분, 재매수 서두르지 말 것' if long_weak else ''))
            blocks.append(_section(
                f'`{r["name"]}` ({accs})\n'
                f'현재가 {r["close"]:,.0f}  /  MA10 {r["ma"]:,.0f}  /  *{r["pct"]:+.1f}%*\n'
                f'→ {"전량 매도 후 CD금리 대기" if is_monthly else "월말 종가 확인 후 결정"}'
                + death
            ))

    # 천장 패턴 형성 중 — 아직 MA10 위 (원서 p.263 "쌍봉 패턴은 형태가 보일 때부터 무조건 경계")
    top_warn = [r for r in port_rows if r.get('top_warn')]
    if top_warn:
        blocks.append(_section('*⚠️ 천장 패턴 형성 중 — 아직 MA10 위, 이탈하는 달 종가에 매도* (원서 p.263 "형태가 보일 때부터 경계")'))
        for r in top_warn:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(f'`{r["name"]}` ({accs})  {r["top_warn"]}  *MA10 {r["pct"]:+.1f}%*'))

    # 저승사자 캔들이 떴으나 위 broke에 안 잡힌 경우(이미 이전에 이탈)도 별도 경고
    death_only = [r for r in port_rows if r.get('death') and not r['broke']]
    if death_only:
        blocks.append(_section('*💀 저승사자 캔들 — MA10 아래 장대 음봉 (즉시 정리)*'))
        for r in death_only:
            accs = ', '.join(r['accounts'])
            blocks.append(_section(f'`{r["name"]}` ({accs})  *{r["pct"]:+.1f}%*  → 전량 매도'))

    # 추가매수 = 원서 매수 신호(2026-09-26 원서 원칙 전환 — 이전 판의 "+5% 이내 최적/+15% 초과 금지" 구간은 원서에 없어 제거)
    for key, title in (('돌파', '★ MA10 재돌파(후킹 캔들, p.256)'), ('지지', '🟢 10이평 지지 반등(p.340)')):
        sig_rows = [r for r in port_rows if r.get('sig') == key]
        if not sig_rows:
            continue
        label = f'{title} — 추가매수 가능(원서 매수 신호)' if is_monthly else f'{title} 진행 중 — 월말 종가 확정 후 검토'
        blocks.append(_section(f'*{label}*'))
        for r in sig_rows:
            accs = ', '.join(r['accounts'])
            vol = f"  {r['nollim'].get('reason', '')}" if key == '지지' and r.get('nollim') else ''
            box = BOX_MARK if r.get('box') else ''
            blocks.append(_section(f'`{r["name"]}` ({accs})  *{r["pct"]:+.1f}%*{vol}{box}'))

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
    holding = [r for r in port_rows if r['above'] and not r.get('sig')]
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
    fresh = [r for r in above if r.get('sig') == '돌파']
    if fresh:
        blocks.append(_section('*★ 이번 달 돌파(후킹) 진행 중* — 월말 종가 확정 전이라 매수 아님'))
        for r in fresh:
            vol_lbl = _vol_quality(r.get('vol_r', 0))
            blocks.append(_section(
                f'`{r["ticker"]}` *{r["name"]}*  |  {r["close"]:,.2f}  |  '
                f'10이평 대비 *+{r["pct"]}%*  |  거래량 {r["vol_r"]:.1f}x  {vol_lbl}'
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

    # 10이평 지지 테스트 중 — 이번 달 저가가 10이평에 닿았고 지금은 위 (월말 종가로 지지 확정 여부 결정)
    sup = [r for r in above if r.get('sig') == '지지']
    if sup:
        blocks.append(_section('*🟢 10이평 지지 테스트 중* — 월말 종가가 10이평 위면 원서 매수 신호(p.340)'))
        fields = [f'`{r["ticker"]}` {r["name"]}  *+{r["pct"]}%*  {r.get("nollim", {}).get("reason", "")}' for r in sup]
        blocks.append(_fields(fields[:10]))
        blocks.append(_divider())

    # 전체 현황 요약
    blocks.append(_section(
        f'*전체 현황*\n'
        f'• 10이평 위 (추세 진행): *{len(above)}개*\n'
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
    fx = get_exchange_rate()
    fx_line = '\n' + build_exchange_rate_section(fx) if fx else ''
    blocks = [
        _header(f'{ym} 월봉 매매 결정  {today_str}  |  {regime}'),
        _section(
            '*월봉 10이평선 종가 확정 — 이달 매매 결정 알림*\n'
            '>✅ 이 알림 기준으로 매수/매도를 결정하세요.'
            + fx_line
        ),
        _divider(),
    ]
    if td:
        blocks.extend(build_topdown_section(td))

    above = [r for r in rows if r['above']]
    below = [r for r in rows if not r['above']]

    # ── 매수 신호 (원서 원칙, 2026-09-26) ──
    # 매수 = 이번 달 종가로 확정된 돌파(후킹 캔들, p.256) 또는 10이평 지지 반등(p.340).
    # 이전 판의 "+5% 이내 지지권 / +5~15% 신규 진입 주의 / +15% 초과 매수 금지" 구간은 원서에 없어 제거.
    # 박스권 안(상단 돌파 전, p.309)은 제외하지 않고 목록 아래로 — backtest_box_range.py(2026-09-26 사용자 결정)
    fresh = sorted([r for r in above if r.get('sig') == '돌파'], key=lambda r: bool(r.get('box')))
    dip   = sorted([r for r in above if r.get('sig') == '지지'], key=lambda r: bool(r.get('box')))
    hold  = sorted([r for r in above if not r.get('sig')], key=lambda r: r['pct'])

    # (2026-09-26) 이전 판의 "글로벌 지수 50% 미만 → 약세장 주의" 배너는 원서에 없는 수치라 제거.
    # 원서 p.394는 수치 없이 "장이 안 좋으면 신규 비중 축소" — 탑다운 섹션의 지수별 상태를 보고 판단.

    if fresh:
        blocks.append(_section('*★ 매수 — 이번 달 10이평 돌파 확정 (후킹 캔들, 원서 p.256)*'))
        for r in fresh:
            vol_lbl = _vol_quality(r.get('vol_r', 0))
            blocks.append(_fields([
                f'*종목:* `{r["ticker"]}` {r["name"]}',
                f'*섹터:* {r["sector"]}',
                f'*현재가:* {r["close"]:,.2f}',
                f'*10이평:* {r["ma"]:,.2f}',
                f'*대비:* +{r["pct"]}%',
                f'*거래량:* {r["vol_r"]:.1f}x  {vol_lbl}' + (BOX_MARK if r.get('box') else ''),
            ]))
        blocks.append(_divider())

    if dip:
        blocks.append(_section('*🟢 매수 — 10이평 지지 반등 확정 (원서 p.340)*\n'
                               '>이번 달 저가가 10이평에 닿고 종가는 위에서 마감. 눌림 거래량이 상승구간 최대의 1/7 이하면 금상첨화(p.364)'))
        for r in dip:
            nl = r.get('nollim', {})
            blocks.append(_fields([
                f'*종목:* `{r["ticker"]}` {r["name"]}',
                f'*섹터:* {r["sector"]}',
                f'*현재가:* {r["close"]:,.2f}',
                f'*MA10:* {r["ma"]:,.2f}  (*+{r["pct"]}%*)',
                f'*{nl.get("reason", "눌림 거래량 정보 없음")}*' + (BOX_MARK if r.get('box') else ''),
            ]))
        blocks.append(_divider())

    if hold:
        blocks.append(_section('*● 추세 진행 중 — 보유 유지 / 신규 매수는 10이평 지지 때*'))
        fields = []
        for r in hold:
            line = f'`{r["ticker"]}` {r["name"]}  +{r["pct"]}%'
            if r.get('decline3'):
                line += ' ⚠️3개월연속하락'
            jz = r.get('jangdae', {})
            if jz.get('warn'):
                line += f'\n  └ 장대양봉 4등분: *{jz["label"]}* (몸통 {jz["body_pct"]}%)'
            fields.append(line)
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
        f'• 매수(돌파): *{len(fresh)}종목*\n'
        f'• 매수(10이평 지지): *{len(dip)}종목*\n'
        f'• 홀딩 유지: *{len(hold)}종목*\n'
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
    buy_us   = sorted([r for r in rows if r.get('above') and r.get('sig') in ('돌파', '지지')],
                      key=lambda r: bool(r.get('box')))   # 박스권 안(p.309)은 뒤로
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
        sig = '돌파' if r.get('sig') == '돌파' else '10이평 지지'
        timeline.append(('22:30~', '미래에셋',
            f"🟢 {r['ticker']} {r['name']} 매수  ({sig} {r['pct']:+.1f}%)" + (' 📦박스권' if r.get('box') else '')))

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
    """원서 매수 신호(돌파·지지) 종목을 us_signals.json 으로 저장"""
    signals = [
        {'ticker': r['ticker'], 'name': r['name'], 'pct': r['pct'], 'signal': r.get('sig')}
        for r in rows
        if r['above'] and r.get('sig') in ('돌파', '지지')
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

    # ── 어떤 알림을 보낼지 먼저 결정 (2026-09-26) ─────────────────
    # 워크플로우가 금요일 아침(UTC 07시, 주간용)과 28~31일 저녁(UTC 22시, 월말용)에 돈다.
    # 주간 = 금요일 아침 실행에서만, 월말 = 말일 미국장 마감 후 실행에서만 보낸다.
    # 보낼 게 없는 실행(28~31일 중 말일이 아닌 날 저녁)은 스캔·신호기록 전에 바로 끝낸다
    # — 안 그러면 매번 tracker에 같은 신호가 중복 기록된다.
    import market_time as mt
    now = mt.utc_now()
    after_close = mt.is_after_us_close(now)
    is_friday   = mt.us_ref_date(now).weekday() == 4
    is_monthend = mt.is_monthend_after_close(now)
    do_monthly  = mode == 'monthly' or (mode == 'auto' and is_monthend)
    do_weekly   = mode == 'weekly' or (mode == 'auto' and is_friday and not after_close and not do_monthly)
    if mode == 'auto' and not (do_monthly or do_weekly):
        print(f'보낼 알림 없음 (미국기준 {mt.us_ref_date(now)}, 마감후={after_close}, '
              f'금요일={is_friday}, 월말={is_monthend}) — 종료')
        return

    # ── 탑다운 분석 (성승현 1원칙) ──────────────────────────────
    print('탑다운 글로벌 지수 분석 중...')
    td = topdown_analysis()
    regime, bullish, total = topdown_regime(td)
    ratio = int(bullish / total * 100) if total else 0
    print(f'탑다운 완료: {regime} ({bullish}/{total}, {ratio}%)')

    rows = scan_all()
    print(f'개별종목 스캔 완료: {len(rows)}종목')
    _save_us_signals(rows)

    # 트래커 연동 — 매수/매도 신호 자동 기록.
    # 2026-09-26 원서 원칙 전환: 매수 기록 = 월말 확정 원서 신호(돌파=후킹 p.256 / 지지 p.340)만.
    # 주간(진행 중인 달) 실행에선 매수 신호를 기록하지 않는다.
    try:
        import signal_tracker as tracker
        for r in rows:
            if do_monthly and r.get('sig') in ('돌파', '지지'):
                tracker.record_signal(r['ticker'], r['name'], f"월봉MA10 {r['sig']}(원서)",
                                      r['close'], r['ma'])
            elif r.get('broke'):
                tracker.record_sell_signal(r['ticker'], r['name'], '월봉MA10',
                                           r['close'], 'MA10이탈')
    except Exception as e:
        print(f'[트래커] {e}')
    etf_rows = scan_etfs()
    print(f'테마ETF 스캔 완료: {len(etf_rows)}개')

    print('포트폴리오 읽는 중...')
    holdings  = read_portfolio()
    port_rows = scan_portfolio(holdings)
    print(f'보유종목 스캔 완료: {len(port_rows)}종목')

    # 포트폴리오 채널 분리: 연금(DC/IRP/연금) vs 일반계좌
    port_pension = [r for r in port_rows if _is_pension(r.get('accounts', [r.get('account', '')]))]
    port_stocks  = [r for r in port_rows if not _is_pension(r.get('accounts', [r.get('account', '')]))]

    if mode == 'test':
        print('\n[테스트] 개별종목 주간 알림')
        _print_blocks(build_weekly_alert(rows, port_rows=port_stocks, td=td))
        print('\n[테스트] 연금계좌 ETF 주간 알림')
        _print_blocks(build_weekly_alert([], etf_rows=etf_rows, port_rows=port_pension, td=td))
        return

    sent = False

    # 월말 알림 우선 (주간보다 중요)
    if do_monthly:
        ym = mt.us_ref_date(now).strftime('%Y.%m')   # 실행이 UTC 자정을 넘겨도 마감한 달로 표기

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

    if do_weekly and not sent:
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
