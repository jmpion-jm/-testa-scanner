# -*- coding: utf-8 -*-
"""2026-09-30 월말 전 전수 점검에서 고친 것들이 되돌아가지 않게 잠그는 테스트 (push 시 CI).
- 매수 추천은 미국 종목만 / DJT 판단 제외 / 장중 손절 주문 문구 금지 / 서머타임 개장시각
- 검증 트래커: 첫 10이평 이탈월 청산, 워크플로우 병합 저장이 서로 기록을 지우지 않음
- 로컬 실행·월말 전 monthly 강제 실행은 슬랙을 보내지 않음"""
import os, sys, json, tempfile
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)

import slack_alert as sa
import signal_tracker as st

fails = []


def check(name, cond):
    print(('PASS ' if cond else 'FAIL ') + name)
    if not cond:
        fails.append(name)


# 1) 미국 종목 판별 — 일본(.T)·한국(.KS/.KQ/숫자) 제외
check('미국 종목 판별', sa._is_us_ticker('AAPL') and sa._is_us_ticker('MOG-A')
      and not sa._is_us_ticker('5802.T') and not sa._is_us_ticker('005930.KS')
      and not sa._is_us_ticker('123456.KQ') and not sa._is_us_ticker('005930'))

# 2) DJT 판단 제외
check('DJT 제외', 'DJT' in sa.EXCLUDED_HOLDINGS)

# 3) 장중 손절 주문 안내 금지(매도는 월말 종가로만)
src = open(os.path.join(BASE, 'slack_alert.py'), encoding='utf-8').read()
_cl = sa.build_action_checklist([{'ticker': 'AAA', 'name': 'A', 'above': True, 'sig': '돌파', 'pct': 1.0}], [], [])
_txt = json.dumps(_cl, ensure_ascii=False)
check('손절 지정매도 문구 없음(실제 알림 문구)', '진입 즉시 지정매도' not in _txt and '장중 손절(지정매도) 주문은 걸지 않는다' in _txt)

# 4) 미국장 개장 한국시각 — HH:MM 형식(서머타임 22:30 / 표준시 23:30)
check('개장시각 형식', sa._us_open_kst() in ('22:30', '23:30'))

# 5) 타임라인: 코스닥은 한국장, 매수 후보는 미국만·전부, 보유 중이면 추가매수 표시
rows = [{'ticker': 'AAA', 'name': 'A', 'above': True, 'sig': '지지', 'pct': 3.0, 'box': False},
        {'ticker': 'BBB', 'name': 'B', 'above': True, 'sig': '돌파', 'pct': 5.0, 'box': False},
        {'ticker': 'CCC', 'name': 'C', 'above': True, 'sig': '지지', 'pct': 1.0, 'box': False},
        {'ticker': 'DDD', 'name': 'D', 'above': True, 'sig': '지지', 'pct': 1.0, 'box': False},
        {'ticker': '5802.T', 'name': '스미토모', 'above': True, 'sig': '돌파', 'pct': 1.0, 'box': False}]
port = [{'ticker': '123456.KQ', 'name': '코스닥주', 'broke': True, 'accounts': ['농협']},
        {'ticker': 'CCC', 'name': 'CCC 씨', 'broke': False, 'accounts': ['미래에셋']}]
table = sa.build_action_checklist(rows, [], port)[2]['text']['text']
check('코스닥 이탈 = 09:00 한국장', '09:00' in table and '코스닥주' in table.split('09:00')[1].split('\n')[0])
check('매수 후보 미국만', '5802.T' not in table)
check('매수 후보 전부(앞 3개 자르기 없음)', all(t in table for t in ('AAA', 'BBB', 'CCC', 'DDD')))
check('돌파가 먼저', table.index('BBB') < table.index('AAA'))
check('보유 중 추가매수 표시', '추가매수 가능(보유 중)' in table.split('CCC')[1].split('\n')[0])

# 6) 필드 10개 초과 시 잘리지 않고 나눠 표시
check('필드 나눠 전부 표시', sum(len(b['fields']) for b in sa._fields_all([str(i) for i in range(23)])) == 23)

# 7) 첫 10이평 이탈월 청산 (진입 월 포함)
idx = pd.date_range('2025-01-01', periods=14, freq='MS')
close = [100] * 10 + [120, 90, 130, 80]          # 11번째(2025-11) 위, 12번째(2025-12) 아래
df = pd.DataFrame({'Close': close}, index=idx)
brk = st.first_ma10_break(df, pd.Period('2025-11', 'M'))
check('첫 이탈월 = 2025-12', brk is not None and str(brk[0]) == '2025-12' and brk[1] == 90)
check('진입월 기준(기록일-5일)', str(st._entry_month({'date': '2026-10-01'})) == '2026-09'
      and str(st._entry_month({'date': '2026-09-30'})) == '2026-09')

# 8) 병합 저장 — 겹쳐 돈 두 실행의 기록이 모두 남고 청산이 유지
d = tempfile.mkdtemp()
base = os.path.join(d, 'base.json')
sig = lambda i, s='open': {'id': i, 'ticker': i, 'name': i, 'strategy': '월봉MA10', 'date': '2026-09-30',
                            'entry_price': 1.0, 'status': s}
json.dump({'signals': [sig('OLD')]}, open(base, 'w', encoding='utf-8'))
a, b = os.path.join(d, 'a.json'), os.path.join(d, 'b.json')
json.dump({'signals': [sig('OLD'), sig('NEWA')]}, open(a, 'w', encoding='utf-8'))
json.dump({'signals': [sig('OLD', 'loss'), sig('NEWB')]}, open(b, 'w', encoding='utf-8'))
st.LOG_PATH = base
st.merge_into_log(b)
st.merge_into_log(a)
R = {s['id']: s for s in json.load(open(base, encoding='utf-8'))['signals']}
check('병합: 두 실행 기록 모두 보존', {'OLD', 'NEWA', 'NEWB'} <= set(R))
check('병합: 청산이 open으로 덮이지 않음', R['OLD']['status'] == 'loss')

# 9) 로컬 실행·월말 전 monthly 강제는 슬랙을 보내지 않음 (안전장치 코드가 run() 맨 앞에 있는지)
run_src = src.split('def run(')[1]
guard = run_src.index('[안전장치] 로컬 실행')
check('안전장치가 스캔·전송보다 앞', guard < run_src.index('scan_all()') and guard < run_src.index('send_slack('))
check('monthly 강제는 월말 확정 필요', "elif mode == 'monthly' and not is_monthend" in run_src)
check('트래커 기록은 진짜 월말만', "if is_monthend and mode != 'test'" in run_src)

# 10) ETF는 월봉 규칙 미적용(2026-09-30 결정) — 분류와 관찰용 알림(매매 지시 문구 없음)
check('ETF 분류', sa._is_etf({'ticker': '449450.KS', 'name': 'PLUS K방산'})
      and sa._is_etf({'ticker': '379800.KS', 'name': 'KODEX 미국S&P500'})
      and not sa._is_etf({'ticker': 'PLUS', 'name': 'PLUS 이플러스'})
      and not sa._is_etf({'ticker': '005930.KS', 'name': '삼성전자'}))
_p = [{'name': 'KODEX 미국S&P500', 'accounts': ['DC'], 'pct': -0.3, 'above': False, 'broke': True, 'fresh': False}]
_e = [{'name': 'TIGER 2차전지', 'theme': '2차전지', 'pct': 5.0, 'above': True, 'broke': False, 'fresh': True}]
_obs = json.dumps(sa.build_pension_observe_alert(_e, _p, '월말'), ensure_ascii=False)
check('ETF 관찰 알림에 매매 지시 없음', all(w not in _obs for w in ('즉시', '전량 매도', '교체', '재진입'))
      and '매매 지시 아님' in _obs)
import ast   # trade_detector를 import하면 stdout을 바꿔 이후 출력이 깨져서 소스에서 값만 읽는다
_td_src = open(os.path.join(BASE, 'trade_detector.py'), encoding='utf-8').read()
_td_brands = next(ast.literal_eval(n.value) for n in ast.parse(_td_src).body
                  if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'ETF_BRANDS')
check('매매감지: ETF 브랜드 목록이 알림과 같음', _td_brands == sa.ETF_BRANDS
      and "ETF — 월봉 규칙 적용 전" in _td_src)

print(f'\n{"전부 통과" if not fails else f"실패 {len(fails)}건: {fails}"}')
sys.exit(1 if fails else 0)
