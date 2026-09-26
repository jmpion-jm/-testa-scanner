# -*- coding: utf-8 -*-
"""
매수·매도 자동 감지 — 구글시트 '포트폴리오' 탭의 보유수량 변화를 전날 기록과 비교해 매매를 찾아내고,
원서 규칙대로였는지 판정해 시트 '매매기록(자동)' 탭에 쌓고 슬랙으로 알린다. (2026-09-26 사용자 요청:
"내가 매수매도한 것을 너에게 자동적으로 알 수 있게 하는 방법" → 구글시트 방식)

목적: 1년쯤 쌓이면 "규칙대로 한 매매"와 "판단 개입 매매"의 성과를 숫자로 비교 — 운과 실력을 가르는 기록.

⚠️ 저장소가 공개(public)라서 보유 내역·매매 기록은 저장소에 커밋하지 않는다. 전날 기록(스냅샷)과 매매기록은
   전부 구글시트 안의 탭('_보유스냅샷', '매매기록(자동)')에 저장한다.

판정 (매매법_전체_구현명세.md H4 — 원서 원칙):
  매수: 직전 월말 확정 월봉에 원서 매수 신호(돌파=후킹 p.256 / 10이평 지지 p.340)가 있으면 ✅ 규칙대로
        최근 2개월 안에 신호가 있었고 아직 10이평 위면 △ 신호 후 늦은 매수 / 그 외 ⚠️ 판단 개입
  매도: 직전 월말 종가가 10이평 아래면 ✅ 규칙대로(10이평 이탈), 위에서 팔면 ⚠️ 판단 개입(부분매도 포함)
  판정 대상 계좌: 개별주 계좌(일반계좌·미국주식·한국주식). 연금(DC·IRP·연금저축)·장기 ETF 계좌는 기록만.
  DJT(트럼프미디어)는 사용자 요청으로 판단·기록 대상에서 제외.
단가: 매수 = 평균매입가 변화로 역산(원). 매도 = 시트에 남지 않으므로 감지일 종가로 추정(미국 주식은 × 원/달러).

실행: python trade_detector.py            (감지 → 시트 기록 → 슬랙)
      python trade_detector.py --dry-run  (시트·슬랙에 쓰지 않고 결과만 출력)
"""
import sys, os, io, re, json, urllib.request, argparse
from datetime import datetime, timezone, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

import book_patterns as bkp
import market_time as mt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE_DIR, 'config.json'), encoding='utf-8'))
GSHEET_ID = '1Xmj6R332n1IvgA6fJ0c5YcOvO6gR_OeDHYmVuTTIzZE'
GSHEET_CREDS = os.environ.get('GSHEET_CREDS_PATH', r'C:\Users\user\.claude\secret-footing-453908-u2-67fee5c1f2f0.json')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

SRC_TAB, SNAP_TAB, LOG_TAB = '포트폴리오', '_보유스냅샷', '매매기록(자동)'
RULE_ACCOUNTS = {'일반계좌', '미국주식', '한국주식'}   # 원서 개별주 규칙 판정 대상
EXCLUDE_CODES = {'DJT'}                                # 사용자 요청: 판단 대상 아님
SKIP_ASSETS = {'현금', '예수금'}
MAX_CHANGES = 8          # 한 번에 이보다 많이 바뀌면 시트 구조 변경으로 보고 기록하지 않음(오탐 방지)
SPLIT_COST_TOL = 0.02    # 수량이 늘었는데 매입금액 변화가 2% 이내면 주식분할·수량조정으로 봄
LOG_HEADER = ['감지일', '계좌', '종목코드', '종목명', '구분', '수량', '단가(원)', '단가근거', '금액(원)',
              '추정손익(원)', '규칙판정', '판정근거', '보유(전→후)']
KST = timezone(timedelta(hours=9))
CODE_RE = re.compile(r'^[A-Z0-9][A-Z0-9.\-]{0,9}$')


def _num(s):
    s = str(s).replace(',', '').replace('원', '').replace('$', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


def _int(x):
    return int(x) if float(x).is_integer() else x


def read_holdings(sh) -> dict:
    """포트폴리오 탭 → {(계좌, 종목코드): {name, qty, avg}}. 계좌 칸이 비면 위 행 계좌를 이어받는다."""
    rows = sh.worksheet(SRC_TAB).get_all_values()
    out, account = {}, ''
    for r in rows:
        if len(r) < 7:
            continue
        acc, asset, code, name = r[1].strip(), r[2].strip(), r[3].strip(), r[4].strip()
        if code == '종목코드':
            account = ''
            continue
        qty, avg = _num(r[5]), _num(r[6])
        if acc:
            account = acc
        if not code or qty is None or avg is None or asset in SKIP_ASSETS or code in EXCLUDE_CODES:
            continue
        if not CODE_RE.match(code) or not account:   # 지수·금액·비중 행(예: '51,828.6', '0.00%') 제외
            continue
        key = (account, code)
        if key in out:   # 같은 계좌·종목이 두 줄이면 합산
            q0, a0 = out[key]['qty'], out[key]['avg']
            tot = q0 + qty
            out[key] = {'name': name, 'qty': tot, 'avg': (q0 * a0 + qty * avg) / tot if tot else 0}
        else:
            out[key] = {'name': name, 'qty': qty, 'avg': avg}
    return out


def read_snapshot(sh):
    try:
        ws = sh.worksheet(SNAP_TAB)
    except gspread.WorksheetNotFound:
        return None
    rows = ws.get_all_values()[1:]
    return {(r[0], r[1]): {'name': r[2], 'qty': float(r[3]), 'avg': float(r[4])} for r in rows if len(r) >= 5 and r[0]}


def write_snapshot(sh, hold):
    try:
        ws = sh.worksheet(SNAP_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(SNAP_TAB, rows=300, cols=6)
    data = [['계좌', '종목코드', '종목명', '보유수량', '평균매입가', f'갱신 {datetime.now(KST):%Y-%m-%d %H:%M} — 자동 관리, 수정 금지']]
    data += [[a, c, v['name'], v['qty'], v['avg'], ''] for (a, c), v in sorted(hold.items())]
    ws.clear()
    ws.update(values=data, range_name='A1')


def diff(prev: dict, cur: dict) -> list:
    changes = []
    for key in sorted(set(prev) | set(cur)):
        p = prev.get(key, {'qty': 0, 'avg': 0, 'name': cur.get(key, {}).get('name', '')})
        c = cur.get(key, {'qty': 0, 'avg': p['avg'], 'name': p['name']})
        dq = c['qty'] - p['qty']
        if abs(dq) < 1e-9:
            continue
        changes.append({'account': key[0], 'code': key[1], 'name': c['name'] or p['name'],
                        'q0': p['qty'], 'q1': c['qty'], 'a0': p['avg'], 'a1': c['avg'], 'dq': dq})
    return changes


# ------------------------------------------------------------------ 시세·판정
def yf_ticker(code: str) -> list:
    """숫자로 시작하는 6자리 코드(예: 005930, 0023A0) = 한국 → .KS 다음 .KQ. 그 외(GOOGL, MOG-A, 5802.T)는 그대로."""
    if len(code) == 6 and code[0].isdigit() and '.' not in code:
        return [code + '.KS', code + '.KQ']
    return [code]


def monthly(code):
    for t in yf_ticker(code):
        df = yf.Ticker(t).history(period='3y', interval='1mo', auto_adjust=True)
        if not df.empty:
            df.index = df.index.tz_localize(None)
            return t, df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    return None, None


def completed_index(d) -> int:
    """월말 종가가 확정된 마지막 월봉(진행 중인 달 제외, 원서 p.235~241)."""
    last = len(d) - 1
    if d.index[last].to_period('M') == pd.Period(mt.us_ref_date(), 'M') and not mt.is_monthend_after_close():
        return last - 1
    return last


def judge(ch) -> tuple:
    """(판정, 근거, 현재가(원, 추정) 또는 None)."""
    t, df = monthly(ch['code'])
    if df is None or len(df) < 13:
        return '판정불가', '시세 데이터 없음', None
    d = bkp.prepare(df)
    k = completed_index(d)
    month = d.index[k].strftime('%Y-%m')
    px = float(d['Close'].iat[-1])
    if not t.endswith(('.KS', '.KQ')):
        fx = yf.Ticker('USDKRW=X').history(period='5d')['Close'].dropna()
        px = px * float(fx.iat[-1]) if len(fx) else None
    if ch['account'] not in RULE_ACCOUNTS:
        return '대상 아님', '연금·장기 ETF 계좌 — 개별주 규칙 적용 안 함(CLAUDE.md)', px
    above = bool(d['Close'].iat[k] > d['MA'].iat[k])
    pct = (d['Close'].iat[k] / d['MA'].iat[k] - 1) * 100
    if ch['dq'] > 0:
        sig = bkp.buy_signal(d, k)
        box = ' · 📦박스권 안(p.309)' if sig and bkp.in_box(d, k) else ''
        if sig:
            return '✅ 규칙대로', f'{month}말 {sig} 신호(10이평 {pct:+.1f}%){box}', px
        recent = [(d.index[j].strftime('%Y-%m'), bkp.buy_signal(d, j)) for j in (k - 1, k - 2)]
        recent = [(m, s) for m, s in recent if s]
        if recent and above:
            m, s = recent[0]
            return '△ 늦은 매수', f'{m}말 {s} 신호 후 추세 진행 중 — 원서 자리보다 늦음(10이평 {pct:+.1f}%)', px
        if not above:
            return '⚠️ 판단 개입', f'{month}말 10이평 아래({pct:+.1f}%) 종목 매수 — 원서 B3 "깨진 종목은 사지 않는다"', px
        return '⚠️ 판단 개입', f'{month}말 원서 매수 신호 없음(추세 진행 중, 10이평 {pct:+.1f}%)', px
    if not above:
        return '✅ 규칙대로', f'{month}말 10이평 이탈({pct:+.1f}%) 매도', px
    part = '부분매도' if ch['q1'] > 0 else '전량매도'
    return '⚠️ 판단 개입', f'{month}말 10이평 위({pct:+.1f}%)에서 {part} — 원서 매도 신호 아님', px


def build_rows(changes) -> list:
    today = datetime.now(KST).strftime('%Y-%m-%d')
    out = []
    for ch in changes:
        verdict, why, px = judge(ch)
        if ch['dq'] > 0:
            added = ch['q1'] * ch['a1'] - ch['q0'] * ch['a0']
            if ch['q0'] > 0 and abs(added) <= SPLIT_COST_TOL * ch['q0'] * ch['a0']:
                kind, price, basis, amt, pl = '수량조정(분할 추정)', '', '매입금액 변화 없음', '', ''
                verdict, why = '기록만', '주식분할·수량 정정으로 추정 — 매매 아님'
            else:
                kind = '매수' if ch['q0'] == 0 else '추가매수'
                price = round(added / ch['dq'])
                basis, amt, pl = '평균매입가 변화로 역산', round(added), ''
        else:
            kind = '매도' if ch['q1'] == 0 else '부분매도'
            price = round(px) if px else ''
            basis = '감지일 종가 추정(시트에 매도가 없음)'
            amt = round(px * -ch['dq']) if px else ''
            pl = round((px - ch['a0']) * -ch['dq']) if px else ''
        out.append([today, ch['account'], ch['code'], ch['name'], kind, _int(abs(ch['dq'])), price, basis, amt, pl,
                    verdict, why, f"{ch['q0']:g}→{ch['q1']:g}"])
    return out


def append_log(sh, rows):
    try:
        ws = sh.worksheet(LOG_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(LOG_TAB, rows=1000, cols=len(LOG_HEADER))
        ws.update(values=[LOG_HEADER], range_name='A1')
    ws.append_rows(rows, value_input_option='USER_ENTERED')
    return ws


def tally(ws) -> str:
    vals = ws.get_all_values()[1:]
    cnt = {}
    for r in vals:
        if len(r) > 10 and r[10] not in ('대상 아님', '기록만', '판정불가'):
            cnt[r[10]] = cnt.get(r[10], 0) + 1
    return ' / '.join(f'{k} {v}건' for k, v in cnt.items())


def slack(text):
    url = CFG.get('slack_webhook_url', '')
    if not url:
        return
    req = urllib.request.Request(url, data=json.dumps({'text': text}).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f'슬랙 전송 실패: {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    sh = gspread.authorize(Credentials.from_service_account_file(GSHEET_CREDS, scopes=SCOPES)).open_by_key(GSHEET_ID)
    cur = read_holdings(sh)
    if len(cur) < 5:   # 시트 읽기 이상 — 전날 기록을 덮어쓰면 "전부 매도"로 오탐하므로 중단
        print(f'보유 종목이 {len(cur)}개만 읽힘 — 시트 구조 확인 필요, 아무것도 기록하지 않음')
        slack(f'⚠️ 매매 감지: 포트폴리오 시트에서 보유 종목이 {len(cur)}개만 읽혔습니다. 시트 구조를 확인해 주세요(기록 안 함).')
        sys.exit(1)
    prev = read_snapshot(sh)
    if prev is None:
        print(f'첫 실행 — 기준 스냅샷 {len(cur)}종목 저장(매매 감지는 다음 실행부터)')
        if not args.dry_run:
            write_snapshot(sh, cur)
        return

    changes = diff(prev, cur)
    print(f'보유 {len(cur)}종목, 변화 {len(changes)}건')
    if not changes:
        return
    if len(changes) > MAX_CHANGES:
        msg = (f'⚠️ 매매 감지: 보유수량 변화가 {len(changes)}건으로 너무 많습니다(시트 구조 변경 가능성). '
               f'기록하지 않았습니다 — 시트 확인 후 괜찮으면 알려주세요.')
        print(msg)
        if not args.dry_run:
            slack(msg)
        return

    rows = build_rows(changes)
    for r in rows:
        print('  ', r)
    if args.dry_run:
        return
    ws = append_log(sh, rows)
    write_snapshot(sh, cur)
    lines = ['*🧾 매매 감지 (구글시트 보유수량 변화)*']
    for r in rows:
        price = f" · {r[6]:,}원({'추정' if '추정' in r[7] else '역산'})" if r[6] != '' else ''
        pl = f" · 추정손익 {r[9]:+,}원" if r[9] != '' else ''
        lines.append(f"{r[10]}  `{r[2]}` {r[3]} ({r[1]}) {r[4]} {r[5]:g}주{price}{pl}\n    └ {r[11]}")
    t = tally(ws)
    if t:
        lines.append(f'\n_누적 판정: {t} — 1년 뒤 규칙대로 vs 개입 성과 비교용_')
    slack('\n'.join(lines))
    print('시트 기록·슬랙 전송 완료')


if __name__ == '__main__':
    main()
