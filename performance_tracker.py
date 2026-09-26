# -*- coding: utf-8 -*-
"""
원서 매매법 전진 검증(forward test) — 실제 보유 종목과 매달 나오는 추천 종목을 같은 규칙으로 기록하고,
월말마다 청산·수익률을 갱신해 "추천대로 했으면" vs "실제로 한 것"의 성과를 쌓는다.
(2026-09-26 사용자 요청: "이 종목 + 향후 추천종목을 기준해서 기록하고 검증해줘")

규칙(매매법_전체_구현명세.md H4 — 원서 원칙):
  추천: 김학주 관심종목(config stocks)에서 월말 확정 원서 매수 신호(돌파=후킹 p.256 / 10이평 지지 p.340)가 난 종목을
        그 달 월말 종가에 샀다고 보고(달러), 이후 처음으로 월말 종가가 10이평 아래인 달 종가에 청산.
        이미 추적 중인(보유중) 종목에 다시 신호가 나면 새 줄을 만들지 않는다. 2026-08월말 신호부터 시작.
  실제: 구글시트 '포트폴리오' 일반계좌 미국 종목. 진입가 = 평균매입가(원), 평가 = 현재가×원/달러(원) — 환율 포함 실제 수익.
        시트에서 수량이 0이 되면 청산(매도가는 '매매기록(자동)'의 추정 매도가, 없으면 그날 시세).
        월말 종가가 10이평 아래인데 계속 보유 중이면 비고에 "규칙상 매도 대상" 표시.
  DJT(트럼프미디어)는 사용자 요청으로 제외.

⚠️ 공개 저장소라 기록은 전부 구글시트 탭 '검증기록(자동)'에 저장(커밋하지 않음). 처리한 월은 P1 셀에 둔다.
실행: python performance_tracker.py            (갱신 → 시트 기록 → 월이 바뀌었으면 슬랙 요약)
      python performance_tracker.py --dry-run  (시트·슬랙에 쓰지 않고 결과만 출력)
"""
import sys, argparse, json, urllib.request
from datetime import datetime

import pandas as pd
import yfinance as yf
import gspread

import book_patterns as bkp
import trade_detector as td   # 시트 인증·보유 파싱·시세·확정월 계산 공용

TAB = '검증기록(자동)'
HEADER = ['구분', '티커', '종목명', '진입월', '진입가', '통화', '진입근거', '상태', '청산월', '청산가',
          '수익률', '보유개월', '최근 월말 10이평 대비', '비고', '갱신일']
START_MONTH = '2026-08'
ACTUAL_ACCOUNTS = {'일반계좌'}
EXCLUDE = {'DJT'}
STOCKS = td.CFG.get('stocks', {})


def kname(t, fallback=''):
    return STOCKS.get(t, [fallback or t])[0]


def fx_now():
    s = yf.Ticker('USDKRW=X').history(period='5d')['Close'].dropna()
    return float(s.iat[-1])


def monthly_d(t):
    _, df = td.monthly(t)
    if df is None or len(df) < 13:
        return None
    return bkp.prepare(df)


def month_idx(d, ym):
    for i, ix in enumerate(d.index):
        if ix.strftime('%Y-%m') == ym:
            return i
    return None


def load(sh):
    try:
        ws = sh.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(TAB, rows=1000, cols=len(HEADER) + 1)
        ws.update(values=[HEADER + ['처리월:']], range_name='A1')
    vals = ws.get_all_values()
    head = vals[0] if vals else HEADER + ['처리월:']
    done = head[15].replace('처리월:', '').strip() if len(head) > 15 else ''
    rows = [dict(zip(HEADER, r + [''] * (len(HEADER) - len(r)))) for r in vals[1:] if r and r[0]]
    return ws, rows, done


def update_open_reco(r, today):
    """추천 줄: 진입월 다음 달부터 확정월까지 월말 종가 < 10이평인 첫 달에 청산."""
    d = monthly_d(r['티커'])
    if d is None:
        r['비고'] = '시세 없음'
        return
    k = td.completed_index(d)
    e = month_idx(d, r['진입월'])
    entry = float(r['진입가'])
    for j in range(e + 1, k + 1) if e is not None else []:
        if d['Close'].iat[j] < d['MA'].iat[j]:
            px = float(d['Close'].iat[j])
            r.update({'상태': '청산', '청산월': d.index[j].strftime('%Y-%m'), '청산가': round(px, 2),
                      '수익률': f'{px / entry - 1:+.1%}', '보유개월': j - e,
                      '최근 월말 10이평 대비': f"{d['Close'].iat[j] / d['MA'].iat[j] - 1:+.1%}", '비고': '월말 10이평 이탈', '갱신일': today})
            return
    now = float(d['Close'].iat[-1])
    r.update({'수익률': f'{now / entry - 1:+.1%}(평가)', '보유개월': (k - e) if e is not None else '',
              '최근 월말 10이평 대비': f"{d['Close'].iat[k] / d['MA'].iat[k] - 1:+.1%}", '갱신일': today})


def new_recos(rows, month, today):
    """확정월(month)의 원서 매수 신호 → 새 추천 줄(이미 보유중인 추천 종목은 제외)."""
    open_t = {r['티커'] for r in rows if r['구분'] == '추천' and r['상태'] == '보유중'}
    seen = {(r['티커'], r['진입월']) for r in rows if r['구분'] == '추천'}
    out = []
    for t in STOCKS:
        if t in EXCLUDE or t in open_t:
            continue
        d = monthly_d(t)
        if d is None:
            continue
        j = month_idx(d, month)
        if j is None or (t, month) in seen:
            continue
        sig = bkp.buy_signal(d, j)
        if not sig:
            continue
        box = ' · 📦박스권' if bkp.in_box(d, j) else ''
        out.append({'구분': '추천', '티커': t, '종목명': kname(t), '진입월': month, '진입가': round(float(d['Close'].iat[j]), 2),
                    '통화': 'USD', '진입근거': f'{sig}{box}', '상태': '보유중', '청산월': '', '청산가': '', '수익률': '',
                    '보유개월': 0, '최근 월말 10이평 대비': f"{d['Close'].iat[j] / d['MA'].iat[j] - 1:+.1%}", '비고': '', '갱신일': today})
    return out


def last_sell_price(sh, code):
    try:
        vals = sh.worksheet(td.LOG_TAB).get_all_values()[1:]
    except gspread.WorksheetNotFound:
        return None
    for r in reversed(vals):
        if len(r) > 6 and r[2] == code and '매도' in r[4] and r[6]:
            return td._num(r[6])
    return None


def sync_actual(sh, rows, hold, fx, today, month, first):
    """실제 줄: 보유 시트와 맞춘다(신규 보유 추가, 수량 0이면 청산, 보유 중이면 원화 평가·규칙 점검)."""
    held = {c: v for (a, c), v in hold.items() if a in ACTUAL_ACCOUNTS and c.isalpha() and c not in EXCLUDE}
    open_rows = {r['티커']: r for r in rows if r['구분'] == '실제' and r['상태'] == '보유중'}
    for c, v in held.items():
        if c not in open_rows:
            r = {'구분': '실제', '티커': c, '종목명': kname(c, v['name']), '진입월': f'{month} 이전(기존 보유)' if first else today[:7],
                 '진입가': round(v['avg']), '통화': 'KRW', '진입근거': '매매기록(자동) 판정 참고', '상태': '보유중',
                 '청산월': '', '청산가': '', '수익률': '', '보유개월': '', '최근 월말 10이평 대비': '', '비고': '', '갱신일': today}
            rows.append(r)
            open_rows[c] = r
    for c, r in open_rows.items():
        d = monthly_d(c)
        px = float(d['Close'].iat[-1]) * fx if d is not None else None
        if c not in held:
            sp = last_sell_price(sh, c) or px
            r.update({'상태': '청산', '청산월': today[:7], '청산가': round(sp) if sp else '',
                      '수익률': f'{sp / float(r["진입가"]) - 1:+.1%}' if sp else '', '비고': '시트 수량 0 → 매도', '갱신일': today})
            continue
        r['진입가'] = round(held[c]['avg'])   # 추가매수로 평균가가 바뀌면 따라간다
        if d is None:
            continue
        k = td.completed_index(d)
        below = d['Close'].iat[k] < d['MA'].iat[k]
        r.update({'수익률': f'{px / float(r["진입가"]) - 1:+.1%}(평가, 원화)',
                  '최근 월말 10이평 대비': f"{d['Close'].iat[k] / d['MA'].iat[k] - 1:+.1%}",
                  '비고': f"⚠️ {d.index[k].strftime('%Y-%m')}말 10이평 이탈 — 규칙상 매도 대상" if below else '',
                  '갱신일': today})


def pct(s):
    try:
        return float(str(s).split('(')[0].replace('%', '').replace('+', '')) / 100
    except ValueError:
        return None


def summary(rows) -> str:
    out = []
    for kind in ('추천', '실제'):
        rs = [r for r in rows if r['구분'] == kind]
        closed = [pct(r['수익률']) for r in rs if r['상태'] == '청산' and pct(r['수익률']) is not None]
        opened = [pct(r['수익률']) for r in rs if r['상태'] == '보유중' and pct(r['수익률']) is not None]
        line = f'*{kind}* — 보유중 {len(opened)}개'
        if opened:
            line += f' (평가 평균 {sum(opened) / len(opened):+.1%})'
        if closed:
            win = sum(1 for x in closed if x > 0) / len(closed)
            line += f' / 청산 {len(closed)}건 승률 {win:.0%} 평균 {sum(closed) / len(closed):+.1%}'
        out.append(line)
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    today = datetime.now(td.KST).strftime('%Y-%m-%d')
    sh = gspread.authorize(td.Credentials.from_service_account_file(td.GSHEET_CREDS, scopes=td.SCOPES)).open_by_key(td.GSHEET_ID)
    ws, rows, done = load(sh)

    ref = monthly_d('SPY')
    month = ref.index[td.completed_index(ref)].strftime('%Y-%m')   # 월말 종가가 확정된 최신 달
    new_month = month != done
    fx = fx_now()

    first_actual = not any(r['구분'] == '실제' for r in rows)
    if new_month:
        for r in rows:
            if r['구분'] == '추천' and r['상태'] == '보유중':
                update_open_reco(r, today)
        if month >= START_MONTH:
            fresh = new_recos(rows, month, today)
            for r in fresh:
                update_open_reco(r, today)   # 신호 후 지금까지 평가 수익률
            rows += fresh
    sync_actual(sh, rows, td.read_holdings(sh), fx, today, month, first_actual)

    print(f'확정월 {month} (이전 처리 {done or "없음"}) — 총 {len(rows)}줄')
    for r in rows:
        print('  ', [r[h] for h in HEADER[:14]])
    print(summary(rows))
    if args.dry_run:
        return
    data = [HEADER + [f'처리월:{month}']] + [[r[h] for h in HEADER] for r in rows]
    ws.clear()
    ws.update(values=data, range_name='A1', value_input_option='RAW')
    if new_month:
        new_n = sum(1 for r in rows if r['구분'] == '추천' and r['진입월'] == month)
        closed_now = [r for r in rows if r['청산월'] == month or (r['구분'] == '실제' and r['청산월'] == today[:7] and r['갱신일'] == today)]
        lines = [f'*📊 원서 매매법 검증 기록 — {month}말 기준*', summary(rows),
                 f'이번 달 새 추천 {new_n}종목' + (f' · 청산 {len(closed_now)}건: ' + ', '.join(
                     f"{r['종목명']}({r['티커']}) {r['수익률']}" for r in closed_now) if closed_now else ''),
                 '_구글시트 "검증기록(자동)" 탭 — 추천은 원서 규칙 그대로(달러), 실제는 평균매입가 기준 원화_']
        td.slack('\n'.join(lines))
    print('시트 갱신 완료' + (' · 슬랙 요약 전송' if new_month else ''))


if __name__ == '__main__':
    main()
