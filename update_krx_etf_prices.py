# -*- coding: utf-8 -*-
"""
구글시트 "포트폴리오" 탭의 GOOGLEFINANCE 수식이 못 가져오는 국내 ETF 단가를
야후파이낸스로 대신 채워넣는다.

배경: GOOGLEFINANCE()는 KRX의 신형 영숫자 종목코드(예: 0023A0)를 아예
인식 못 하고, 일부 신규 상장 ETF(428510, 447770 등)도 색인이 안 돼 있어
"현재가"가 #N/A로 뜬다. 이 문제 종목들은 이미 "국내상장ETF 종목코드
(수기등록필요)" 탭에 등록돼 있었으나(2025-02-02) 실제 수기 입력이 이뤄지지
않고 있었다. 이 스크립트는 그 탭의 목록을 그대로 읽어(하드코딩 안 함 —
탭에 종목이 추가되면 자동으로 같이 처리됨) 야후파이낸스로 가격을 조회하고,
"포트폴리오" 탭 전체(퇴직연금/연금저축펀드2/IRP/미래에셋/마누라/농협계좌
6개 계좌 섹션, 같은 종목이 여러 계좌에 중복 보유돼 있으면 전부)에서 종목
코드가 일치하는 모든 행의 "현재가"(H열)를 갱신한다.

⚠️ 이 스크립트는 시트에 실제로 쓰기(update)를 한다 — slack_alert.py의
read_portfolio()는 읽기 전용이라 이것과 무관하며, 이 스크립트만 별도로
쓰기 스코프(GSHEET_SCOPES)를 요청한다. 서비스 계정이 이미 이 시트에
"편집자"로 공유돼 있어 추가 조치 없이 바로 동작함(2026-09-12 확인).

실행: 매일 자동 (GitHub Actions) 또는 수동 실행.
"""
import sys, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import gspread
from google.oauth2.service_account import Credentials
import yfinance as yf

GSHEET_CREDS = os.environ.get(
    'GSHEET_CREDS_PATH',
    r'C:\Users\user\.claude\secret-footing-453908-u2-67fee5c1f2f0.json'
)
GSHEET_ID = '1Xmj6R332n1IvgA6fJ0c5YcOvO6gR_OeDHYmVuTTIzZE'
GSHEET_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']  # 읽기+쓰기

PORTFOLIO_TAB = '포트폴리오'
MANUAL_TAB = '국내상장ETF 종목코드(수기등록필요)'
CODE_COL = 4   # D열: 종목코드 (1-based)
PRICE_COL = 8  # H열: 현재가 (1-based)


def get_manual_tickers(sh) -> list[str]:
    """'수기등록필요' 탭에서 종목코드 목록을 가져온다 (하드코딩 안 함)."""
    ws = sh.worksheet(MANUAL_TAB)
    rows = ws.get_all_values()[2:]  # 1행: 기준일 타이틀, 2행: 컬럼 헤더("종목코드") — 둘 다 제외
    codes = []
    for r in rows:
        if len(r) > 1 and r[1].strip():
            code = r[1].strip()
            if 'E+' in code:
                continue  # '8.00E+00' 같은 지수표기 오염 행은 유효한 종목코드가 아니라 스킵
            codes.append(code)
    return codes


def fetch_price(code: str) -> float | None:
    try:
        df = yf.Ticker(f'{code}.KS').history(period='5d')
        if df.empty:
            return None
        return float(df['Close'].iloc[-1])
    except Exception:
        return None


def main():
    creds = Credentials.from_service_account_file(GSHEET_CREDS, scopes=GSHEET_SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GSHEET_ID)

    manual_tickers = get_manual_tickers(sh)
    print(f'수기등록 대상 {len(manual_tickers)}종목: {manual_tickers}')

    prices = {}
    for code in manual_tickers:
        p = fetch_price(code)
        prices[code] = p
        print(f'  {code}: {"조회 실패(yfinance도 못 가져옴)" if p is None else f"{p:,.0f}원"}')

    ws = sh.worksheet(PORTFOLIO_TAB)
    all_values = ws.get_all_values()

    updates = []
    for i, row in enumerate(all_values, start=1):  # 1-based 행 번호
        if len(row) < CODE_COL:
            continue
        code = row[CODE_COL - 1].strip()
        if code in prices and prices[code] is not None:
            cell = gspread.utils.rowcol_to_a1(i, PRICE_COL)
            # 텍스트("22,515원")로 쓰면 다른 열(평가손익/수익률 등)의 계산 수식이
            # 깨짐 — 다른 정상 셀들처럼 순수 숫자로 써야 기존 셀 서식이 "원"
            # 표시를 자동으로 붙여주면서 계산도 정상 작동함.
            updates.append({'range': cell, 'values': [[round(prices[code])]]})

    if updates:
        ws.batch_update(updates)
    print(f'\n총 {len(updates)}개 셀 업데이트 완료 (행: {[u["range"] for u in updates]})')


if __name__ == '__main__':
    main()
