"""
미국/한국 MA10 신호 종목 엑셀 정리
시트 구성: 나스닥100 / S&P500 / 김학주교수종목 / 테스타스캔
"""
import os, sys, json, warnings
import pandas as pd
from datetime import date, datetime
warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(os.path.expanduser('~'), 'Desktop', '미국종목_MA10신호.xlsx')
TODAY = date.today().strftime('%Y-%m-%d')

sheets = {}   # { 시트명: DataFrame }

# ── 공통 신호 분류 함수 ───────────────────────────────────────
def classify(pct, fresh, zone=5.0, limit=15.0):
    if fresh and pct <= zone:   return '★★ 신규돌파+지지권'
    if fresh and pct <= limit:  return '★ 신규돌파'
    if not fresh and pct <= zone: return '▲ 지지권'
    if pct <= limit:            return '● 추세권'
    return '△ 고점권'


# ── 1. 김학주교수 추천 종목 ───────────────────────────────────
print('[1/4] 김학주교수 추천 종목 스캔 중...')
rows_kj = []
try:
    from slack_alert import scan_all, ZONE_PCT
    for r in scan_all():
        if not r['above']: continue
        sig = classify(r['pct'], r['fresh'], ZONE_PCT)
        rows_kj.append({
            '티커': r['ticker'], '종목명': r['name'],
            '현재가': r['close'], 'MA10': r['ma'],
            'MA10대비(%)': r['pct'], '신호': sig, '섹터': r.get('sector',''),
        })
    print(f'  → {len(rows_kj)}종목')
except Exception as e:
    print(f'  오류: {e}')
if rows_kj:
    sheets['김학주교수종목'] = pd.DataFrame(rows_kj).sort_values(['신호','MA10대비(%)'])


# ── 2. NASDAQ 100 ─────────────────────────────────────────────
print('[2/4] NASDAQ 100 스캔 중...')
rows_ndx = []
try:
    from nasdaq100_scan import get_ndx100_tickers, scan_ndx100
    ndx_rows = scan_ndx100(get_ndx100_tickers())
    sig_map = {1:'★★ 신규돌파', 2:'▲ 지지권', 3:'● 추세권', 4:'△ 고점권'}
    for r in ndx_rows:
        rows_ndx.append({
            '티커': r['ticker'], '종목명': r['ticker'],
            '현재가': r['close'], 'MA10': r['ma10'],
            'MA10대비(%)': r['pct'], '신호': sig_map.get(r['priority'],''),
            '연속하락': '⚠️' if r.get('decline3') else '',
        })
    print(f'  → {len(rows_ndx)}종목')
except Exception as e:
    print(f'  오류: {e}')
if rows_ndx:
    sheets['나스닥100'] = pd.DataFrame(rows_ndx).sort_values(['신호','MA10대비(%)'])


# ── 3. S&P 500 ────────────────────────────────────────────────
print('[3/4] S&P 500 스캔 중... (3~5분 소요)')
rows_sp = []
try:
    from sp500_scan import get_sp500_tickers, scan_sp500
    sp_rows = scan_sp500(get_sp500_tickers())
    for r in sp_rows:
        rows_sp.append({
            '티커': r['ticker'], '종목명': r['ticker'],
            '현재가': r['close'], 'MA10': r['ma10'],
            'MA10대비(%)': r['pct'], '신호': sig_map.get(r['priority'],''),
            '연속하락': '⚠️' if r.get('decline3') else '',
        })
    print(f'  → {len(rows_sp)}종목')
except Exception as e:
    print(f'  오류: {e}')
if rows_sp:
    sheets['S&P500'] = pd.DataFrame(rows_sp).sort_values(['신호','MA10대비(%)'])


# ── 4. 테스타 스캔 (KOSPI 한국 종목 — 저장된 신호 파일 읽기) ──
print('[4/4] 테스타 신호 읽는 중...')
rows_testa = []
try:
    sig_path = os.path.join(BASE, 'testa_signals.json')
    data = json.load(open(sig_path, encoding='utf-8'))
    for s in data.get('signals', []):
        rows_testa.append({
            '종목코드': s['ticker'], '종목명': s['name'],
            '현재가': s.get('entry',''), '손절가': s.get('stop',''),
            '목표가': s.get('target',''), '리스크(%)': s.get('risk_pct',''),
            '신호일': data.get('date',''), '신호': '★ 테스타 매수신호',
        })
    print(f'  → {len(rows_testa)}종목 (기준일: {data.get("date","")})')
except Exception as e:
    print(f'  오류: {e}')
if rows_testa:
    sheets['테스타스캔'] = pd.DataFrame(rows_testa)
else:
    sheets['테스타스캔'] = pd.DataFrame([{
        '종목코드':'', '종목명':'신호 없음', '현재가':'', '손절가':'',
        '목표가':'', '리스크(%)':'', '신호일':'', '신호':''}])


# ── 엑셀 저장 ─────────────────────────────────────────────────
if not sheets:
    print('저장할 데이터 없음')
    sys.exit(0)

with pd.ExcelWriter(OUT, engine='openpyxl') as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        for col in ws.columns:
            col_w = max((len(str(c.value or '')) for c in col), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(col_w + 3, 30)

total = sum(len(df) for df in sheets.values())
print(f'\n저장 완료 → {OUT}')
print(f'시트: {list(sheets.keys())}')
print(f'총 {total}종목')
