# -*- coding: utf-8 -*-
"""
투자 시스템 종합 검증 에이전트
────────────────────────────────────────────────────────
검증 항목:
  1. 슬랙 웹훅 URL 전체 작동 여부
  2. 미국 종목 티커 유효성 (yfinance)
  3. KOSPI 유니버스 티커 정확성 (pykrx)
  4. 테마 ETF 티커 정확성 (pykrx)
  5. 신호 파일 최신성 (testa_signals.json 등)
  6. 가격 데이터 이상 감지 (급락/데이터 없음)
  7. config.json 필수 항목 누락 여부

문제 발견 시 슬랙으로 즉시 경고
────────────────────────────────────────────────────────
실행: python verify_system.py
"""
import sys, io, os, json, warnings, urllib.request, urllib.error
from datetime import date, datetime, timedelta
import pandas as pd
import yfinance as yf
warnings.filterwarnings('ignore')

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE     = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(BASE, 'config.json')

# ── 설정 로드 ──────────────────────────────────────────────────
try:
    with open(CFG_PATH, encoding='utf-8') as f:
        CFG = json.load(f)
except Exception as e:
    print(f'[치명적] config.json 로드 실패: {e}')
    sys.exit(1)

ALERT_URL = CFG.get('slack_webhook_url', '')   # 경고는 김학주 채널로

errors   = []   # 오류 목록
warnings_list = []   # 경고 목록

def err(msg):
    errors.append(msg)
    print(f'  ❌ {msg}')

def warn(msg):
    warnings_list.append(msg)
    print(f'  ⚠️  {msg}')

def ok(msg):
    print(f'  ✅ {msg}')


# ══════════════════════════════════════════════════════════════
# 1. config.json 필수 항목 검증
# ══════════════════════════════════════════════════════════════
def check_config():
    print('\n[1] config.json 필수 항목 검증')
    required = [
        'slack_webhook_url', 'slack_webhook_url_testa',
        'slack_webhook_url_pension', 'stocks', 'theme_etfs',
        'safe_haven_etf', 'ma_period',
    ]
    for key in required:
        if not CFG.get(key):
            err(f'config.json에 "{key}" 항목 없음 또는 비어있음')
        else:
            ok(f'{key} 존재')

    # stocks가 비어있지 않은지
    if len(CFG.get('stocks', {})) < 10:
        err(f'stocks 종목 수 너무 적음: {len(CFG.get("stocks",{}))}개')
    else:
        ok(f'stocks {len(CFG["stocks"])}개')

    if len(CFG.get('theme_etfs', {})) < 3:
        err(f'theme_etfs 너무 적음: {len(CFG.get("theme_etfs",{}))}개')
    else:
        ok(f'theme_etfs {len(CFG["theme_etfs"])}개')


# ══════════════════════════════════════════════════════════════
# 2. 슬랙 웹훅 URL 작동 검증
# ══════════════════════════════════════════════════════════════
def check_slack_webhooks():
    print('\n[2] 슬랙 웹훅 URL 검증')

    # slack_webhook_url(ALERT_URL)은 이 검증 리포트 자체가 그 채널로 전송되므로
    # 별도 핑을 보내지 않는다 — 리포트 전송 성공 여부가 곧 이 웹훅의 검증이고,
    # 핑을 따로 보내면 리포트 직전에 "무시해도 되는 메시지"가 같은 채널에 찍혀서
    # 진짜 오류 리포트까지 노이즈로 오인되는 문제가 있었음.
    if not CFG.get('slack_webhook_url', ''):
        warn('slack_webhook_url 미설정')
    else:
        ok('slack_webhook_url 존재 (검증 리포트 전송 성공 여부로 확인됨)')

    webhook_keys = [
        'slack_webhook_url_testa',
        'slack_webhook_url_pension',
        'slack_webhook_url_sp500',
        'slack_webhook_url_ndx100',
    ]
    for key in webhook_keys:
        url = CFG.get(key, '')
        if not url:
            warn(f'{key} 미설정')
            continue
        try:
            payload = json.dumps({'text': f'🔧 [자동검증] {key} 연결 확인 핑 — 실제 알림 아님, 조치 불필요'}).encode('utf-8')
            req = urllib.request.Request(url, data=payload,
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=8) as res:
                result = res.read().decode()
                if result == 'ok':
                    ok(f'{key} 정상')
                else:
                    err(f'{key} 응답 이상: {result}')
        except Exception as e:
            err(f'{key} 연결 실패: {e}')


# ══════════════════════════════════════════════════════════════
# 3. 미국 종목 티커 유효성 검증 (yfinance)
# ══════════════════════════════════════════════════════════════
def check_us_tickers():
    print('\n[3] 미국 종목 티커 검증 (yfinance)')
    stocks = CFG.get('stocks', {})
    invalid = []
    no_data = []

    for ticker, info in stocks.items():
        name = info[0] if isinstance(info, list) else str(info)
        try:
            t  = yf.Ticker(ticker)
            df = t.history(period='5d', interval='1d')
            if df.empty:
                no_data.append(f'{ticker}({name})')
                warn(f'{ticker} ({name}): 가격 데이터 없음 — 상장폐지/티커 변경 의심')
            else:
                close = float(df['Close'].iloc[-1])
                if close <= 0:
                    err(f'{ticker} ({name}): 가격 0 이하')
                else:
                    ok(f'{ticker} ({name}): ${close:.2f}')
        except Exception as e:
            invalid.append(f'{ticker}({name})')
            err(f'{ticker} ({name}): 조회 실패 — {e}')

    if no_data:
        err(f'데이터 없는 미국 종목: {", ".join(no_data)}')


# ══════════════════════════════════════════════════════════════
# 4. KOSPI 유니버스 티커 검증 (pykrx)
# ══════════════════════════════════════════════════════════════
def check_kospi_tickers():
    print('\n[4] KOSPI 유니버스 티커 검증 (pykrx)')
    try:
        from pykrx import stock as krx
        from testa_scan import KOSPI_UNIVERSE
    except ImportError as e:
        warn(f'pykrx 또는 testa_scan 임포트 실패: {e}')
        return

    mismatches = []
    for ticker, name in KOSPI_UNIVERSE:
        try:
            actual = krx.get_market_ticker_name(ticker)
            if actual and actual != name:
                mismatches.append((ticker, name, actual))
                err(f'{ticker}: 코드상={name} / 실제={actual}')
        except Exception:
            pass

    if not mismatches:
        ok(f'KOSPI {len(KOSPI_UNIVERSE)}개 전체 정확')


# ══════════════════════════════════════════════════════════════
# 5. 테마 ETF 티커 검증 (pykrx)
# ══════════════════════════════════════════════════════════════
def check_etf_tickers():
    print('\n[5] 테마 ETF 티커 검증')
    try:
        from pykrx import stock as krx
    except ImportError:
        warn('pykrx 미설치')
        return

    theme_etfs = CFG.get('theme_etfs', {})
    errors_found = []
    for raw_ticker, info in theme_etfs.items():
        name = info[0] if isinstance(info, list) else str(info)
        ticker = raw_ticker.replace('.KS', '')
        try:
            df = yf.Ticker(raw_ticker).history(period='5d')
            if df.empty:
                errors_found.append(f'{raw_ticker}({name})')
                err(f'{raw_ticker} ({name}): 데이터 없음')
            else:
                ok(f'{raw_ticker} ({name}): {float(df["Close"].iloc[-1]):,.0f}원')
        except Exception as e:
            errors_found.append(f'{raw_ticker}')
            err(f'{raw_ticker}: 조회 실패 — {e}')

    if not errors_found:
        ok(f'테마 ETF {len(theme_etfs)}개 전체 정상')


# ══════════════════════════════════════════════════════════════
# 6. 신호 파일 최신성 검증
# ══════════════════════════════════════════════════════════════
def check_signal_files():
    print('\n[6] 신호 파일 최신성 검증')
    today = date.today()

    files = {
        'testa_signals.json': 4,    # 최대 4일 (주말 포함)
        'us_signals.json':    8,    # 최대 8일 (주간 알림)
    }

    for fname, max_days in files.items():
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            warn(f'{fname}: 파일 없음 (아직 한 번도 실행 안 됨)')
            continue
        try:
            data = json.load(open(path, encoding='utf-8'))
            sig_date = datetime.strptime(data.get('date','2000-01-01'), '%Y-%m-%d').date()
            days_ago = (today - sig_date).days
            if days_ago > max_days:
                err(f'{fname}: 마지막 업데이트 {days_ago}일 전 — 스캔이 실행되지 않았을 수 있음')
            else:
                ok(f'{fname}: {sig_date} ({days_ago}일 전)')
        except Exception as e:
            err(f'{fname}: 읽기 오류 — {e}')


# ══════════════════════════════════════════════════════════════
# 7. 가격 데이터 이상 감지 (급락/급등)
# ══════════════════════════════════════════════════════════════
def check_price_anomalies():
    print('\n[7] 가격 이상 감지 (월간 -30% 이상 급락)')
    stocks = CFG.get('stocks', {})
    anomalies = []

    tickers = list(stocks.keys())
    try:
        raw = yf.download(tickers, period='2mo', interval='1mo',
                          auto_adjust=True, group_by='ticker',
                          progress=False, threads=True)
    except Exception as e:
        warn(f'일괄 다운로드 실패: {e}')
        return

    for ticker, info in stocks.items():
        name = info[0] if isinstance(info, list) else str(info)
        try:
            if len(tickers) == 1:
                close = raw['Close'].dropna()
            else:
                if ticker not in raw.columns.get_level_values(0):
                    continue
                close = raw[ticker]['Close'].dropna()
            if len(close) < 2:
                continue
            chg = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
            if chg <= -30:
                anomalies.append(f'{ticker}({name}): {chg:+.1f}%')
                err(f'{ticker} ({name}): 전월 대비 {chg:+.1f}% — 이상 하락 확인 필요')
            elif chg <= -15:
                warn(f'{ticker} ({name}): 전월 대비 {chg:+.1f}% — 주의')
        except Exception:
            continue

    if not anomalies:
        ok(f'가격 이상 감지 없음 ({len(stocks)}개 종목)')


# ══════════════════════════════════════════════════════════════
# 8. GitHub Actions 실행 이력 검증
#    ("success"로 끝났어도 내부적으로 슬랙 전송이 조용히 실패할 수 있어서
#     생긴 사고 — 이 워크플로우들이 최근 실제로 failure를 내진 않았는지 확인)
# ══════════════════════════════════════════════════════════════
def check_workflow_runs():
    print('\n[8] GitHub Actions 최근 실행 이력 검증')
    repo = 'jmpion-jm/-testa-scanner'
    # 워크플로우별 예상 실행 주기(일). sp500/ndx100은 매월 28~31일에만 도는
    # 월간 스캔이라 8일 기준으로 보면 한 달 중 대부분 "미실행"으로 오탐났었음.
    workflows = {
        'slack_alert.yml':   8,
        'sp500_scan.yml':   35,
        'ndx100_scan.yml':  35,
        'testa_scan.yml':    8,
        'testa_morning.yml': 8,
    }
    token = os.environ.get('GITHUB_TOKEN', '')
    headers = {'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'

    for wf, window_days in workflows.items():
        cutoff = datetime.utcnow() - timedelta(days=window_days)
        url = f'https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs?per_page=10'
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as res:
                data = json.load(res)
        except Exception as e:
            warn(f'{wf}: 실행 이력 조회 실패 — {e}')
            continue

        runs = data.get('workflow_runs', [])
        recent = []
        for r in runs:
            created = datetime.strptime(r['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            if created < cutoff:
                continue
            recent.append(r)

        if not recent:
            warn(f'{wf}: 최근 {window_days}일간 실행 기록 없음 — 스케줄이 안 돌았을 수 있음')
            continue

        failed = [r for r in recent if r.get('conclusion') == 'failure']
        if failed:
            err(f'{wf}: 최근 {window_days}일간 {len(failed)}건 실패 (총 {len(recent)}건 중) — Actions 로그 확인 필요')
        else:
            ok(f'{wf}: 최근 {len(recent)}건 전부 성공')


# ══════════════════════════════════════════════════════════════
# 슬랙 검증 결과 전송
# ══════════════════════════════════════════════════════════════
def send_verification_report():
    today_str = datetime.today().strftime('%Y.%m.%d')
    total_issues = len(errors) + len(warnings_list)

    if total_issues == 0:
        status = '✅ 전체 정상'
        color_emoji = '🟢'
    elif errors:
        status = f'❌ 오류 {len(errors)}건 / 경고 {len(warnings_list)}건'
        color_emoji = '🔴'
    else:
        status = f'⚠️ 경고 {len(warnings_list)}건'
        color_emoji = '🟡'

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"{color_emoji} 시스템 검증 보고서  {today_str}"}},
        {"type": "section",
         "text": {"type": "mrkdwn", "text": f"*결과: {status}*"}},
    ]

    if errors:
        error_text = '\n'.join(f'• {e}' for e in errors[:15])
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*❌ 오류 ({len(errors)}건)*\n{error_text}"}})

    if warnings_list:
        warn_text = '\n'.join(f'• {w}' for w in warnings_list[:10])
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*⚠️ 경고 ({len(warnings_list)}건)*\n{warn_text}"}})

    if total_issues == 0:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "_모든 항목 정상 — 투자 시스템 신뢰 가능_"}})

    if not ALERT_URL:
        print('\n[슬랙 미설정] 결과를 콘솔로만 출력합니다.')
        return

    payload = json.dumps({'text': f'시스템 검증 {today_str}', 'blocks': blocks},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        ALERT_URL, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            sent_ok = res.read().decode() == 'ok'
            print(f'\n슬랙 전송: {"성공" if sent_ok else "실패"}')
            if not sent_ok:
                print('::error::검증 리포트 슬랙 전송 실패 — 응답이 ok가 아님')
                sys.exit(1)
    except Exception as e:
        print(f'\n슬랙 전송 오류: {e}')
        print('::error::검증 리포트 슬랙 전송 예외 발생')
        sys.exit(1)


# ══════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print(f'\n{"="*60}')
    print(f'  투자 시스템 종합 검증  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'{"="*60}')

    check_config()
    check_slack_webhooks()
    check_us_tickers()
    check_kospi_tickers()
    check_etf_tickers()
    check_signal_files()
    check_price_anomalies()
    check_workflow_runs()

    print(f'\n{"="*60}')
    print(f'  결과: 오류 {len(errors)}건 / 경고 {len(warnings_list)}건')
    print(f'{"="*60}')

    send_verification_report()

    # 실제 오류가 있으면 Actions 실행 자체를 failure로 표시 — 슬랙만 보지 않고
    # Actions 탭만 훑어봐도 "success인데 사실 문제 있음" 상태가 나오지 않게 함
    if errors:
        print(f'::error::검증 오류 {len(errors)}건 발견 — 위 목록 확인 필요')
        sys.exit(1)
