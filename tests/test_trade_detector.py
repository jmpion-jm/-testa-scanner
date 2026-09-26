# -*- coding: utf-8 -*-
"""
trade_detector.py 로직 검증 (네트워크·시트 없이) — 시트 파싱, 보유수량 변화 감지, 매수 단가 역산,
주식분할 판별, DJT 제외, 지수·금액 행 오인 방지.
"""
import os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.chdir(BASE)
import trade_detector as td

failures = []


def check(name, cond, detail=''):
    print(f"  {'✅' if cond else '❌'} {name}" + ('' if cond else f' — {detail}'))
    if not cond:
        failures.append(name)


class FakeWS:
    def __init__(self, rows):
        self.rows = rows

    def get_all_values(self):
        return self.rows


class FakeSheet:
    def __init__(self, rows):
        self.ws = FakeWS(rows)

    def worksheet(self, name):
        return self.ws


ROWS = [
    ['', '코스피 지수', '코스닥 지수', '다우 지수', '나스닥 지수', 'S&P500 지수', '원/달러 환율'],
    ['', '7,080.9', '#N/A', '51,828.6', '27,068.7', '7,743.4', '1,354.24'],     # 지수 행 — 종목 아님
    ['', '비중', '79.86%', '0.00%', '0.53%', '0.00%', '1.0%'],                  # 비중 행 — 종목 아님
    ['', '계좌', '자산', '종목코드', '종목명', '보유수량', '평균매입가'],
    ['', 'DC', '주식', '357870', 'TIGER CD금리', '550', '54,343원'],
    ['', 'DC', '현금', '예수금', '예수금', '1', '729,127원'],                      # 현금 — 제외
    ['', '마누라', '주식', '360750', 'TIGER 미국S&P500', '65', '24,047원'],
    ['', '', '', '0023A0', 'SOL 미국배당', '43', '23,664원'],                      # 계좌 빈칸 → 위 행(마누라) 이어받음
    ['', '계좌', '자산', '종목코드', '종목명', '보유수량', '평균매입가'],
    ['', '일반계좌', '주식', 'GOOGL', 'Alphabet', '8', '482,185원'],
    ['', '일반계좌', '주식', 'CRWD', 'CrowdStrike', '54', '156,137원'],
    ['', '미국주식', '주식', 'DJT', 'Trump Media', '85', '42,236원'],             # 사용자 요청 제외
]


def test_parse():
    print('\n[시트 파싱]')
    h = td.read_holdings(FakeSheet(ROWS))
    check('종목 5개만 읽음(지수·비중·현금·DJT 제외)', len(h) == 5, f'실제={sorted(h)}')
    check('계좌 빈칸은 위 행 계좌 이어받음', ('마누라', '0023A0') in h)
    check('DJT 제외', not any(c == 'DJT' for _, c in h))
    check('숫자·원 표기 파싱', h[('일반계좌', 'GOOGL')] == {'name': 'Alphabet', 'qty': 8.0, 'avg': 482185.0})


def test_diff_and_rows():
    print('\n[변화 감지·단가]')
    prev = {('일반계좌', 'GOOGL'): {'name': 'Alphabet', 'qty': 6, 'avg': 470000},
            ('일반계좌', 'CRWD'): {'name': 'CrowdStrike', 'qty': 60, 'avg': 156137},
            ('일반계좌', 'APH'): {'name': 'Amphenol', 'qty': 10, 'avg': 200000},
            ('DC', '357870'): {'name': 'CD', 'qty': 550, 'avg': 54343}}
    cur = {('일반계좌', 'GOOGL'): {'name': 'Alphabet', 'qty': 8, 'avg': 482185},
           ('일반계좌', 'CRWD'): {'name': 'CrowdStrike', 'qty': 54, 'avg': 156137},
           ('일반계좌', 'APH'): {'name': 'Amphenol', 'qty': 20, 'avg': 100000},
           ('일반계좌', 'GLW'): {'name': 'Corning', 'qty': 5, 'avg': 210000},
           ('DC', '357870'): {'name': 'CD', 'qty': 550, 'avg': 54343}}
    ch = td.diff(prev, cur)
    check('변화 4건(DC 무변화 제외)', len(ch) == 4, f'실제={[c["code"] for c in ch]}')
    orig = td.judge
    td.judge = lambda c: ('✅ 규칙대로', '테스트', 300000.0)
    try:
        rows = {r[2]: r for r in td.build_rows(ch)}
    finally:
        td.judge = orig
    g = rows['GOOGL']
    check('추가매수 2주, 단가 역산 = (8×482185 − 6×470000)/2 = 518,740', g[4] == '추가매수' and g[5] == 2 and g[6] == 518740, f'실제={g}')
    check('신규 매수 GLW 5주 @210,000', rows['GLW'][4] == '매수' and rows['GLW'][6] == 210000, f'실제={rows["GLW"]}')
    c = rows['CRWD']
    check('부분매도 6주, 종가 추정 손익 = (300000−156137)×6', c[4] == '부분매도' and c[5] == 6 and c[9] == round((300000 - 156137) * 6), f'실제={c}')
    a = rows['APH']
    check('수량 2배·매입금액 동일 = 분할 추정(매매 아님)', a[4].startswith('수량조정') and a[10] == '기록만', f'실제={a}')


if __name__ == '__main__':
    print('=' * 60)
    print('  매매 자동 감지(trade_detector) 로직 테스트')
    print('=' * 60)
    test_parse()
    test_diff_and_rows()
    print('\n' + '=' * 60)
    if failures:
        print(f'  실패 {len(failures)}건: {", ".join(failures)}')
        sys.exit(1)
    print('  전체 통과')
