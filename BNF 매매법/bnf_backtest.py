# -*- coding: utf-8 -*-
"""
BNF 역추세 전략 — 과거 6개월 시뮬레이션
진입 조건: 급락(-8%) + 이격도(-20%) + RSI(30↓) + MACD 0선 상향돌파
청산: 손절 -5% / 목표 +10% / 최대 20거래일 보유
"""
import sys, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

try:
    from pykrx import stock as krx
except ImportError:
    print("pykrx 미설치: pip install pykrx")
    sys.exit(1)

# 상위 폴더의 bnf_strategy.py 에서 지표 함수 가져오기
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bnf_strategy import BNFConfig, add_indicators, entry_signal

# ── 설정 ─────────────────────────────────────────────────────
STOP_PCT   = -5.0    # 손절 %
TARGET_PCT = 10.0    # 목표 %
MAX_DAYS   = 20      # 최대 보유 거래일
LOOKBACK_MONTHS = 18  # 시뮬레이션 기간 (1년 6개월)

cfg = BNFConfig(
    rsi_oversold=35.0,           # 30 → 35 (약간 완화)
    disparity_largecap=-15.0,    # -20 → -15 (약간 완화)
    drop_threshold=-6.0,         # -8 → -6 (약간 완화)
)

UNIVERSE = [
    ('005930', '삼성전자'),    ('000660', 'SK하이닉스'),
    ('207940', '삼성바이오'),  ('005380', '현대차'),
    ('000270', '기아'),        ('005490', 'POSCO홀딩스'),
    ('105560', 'KB금융'),      ('068270', '셀트리온'),
    ('055550', '신한지주'),    ('012330', '현대모비스'),
    ('006400', '삼성SDI'),     ('051910', 'LG화학'),
    ('086790', '하나금융'),    ('035420', 'NAVER'),
    ('066570', 'LG전자'),      ('033780', 'KT&G'),
    ('017670', 'SK텔레콤'),    ('010130', '고려아연'),
    ('003550', 'LG'),          ('030200', 'KT'),
    ('012450', '한화에어로'),  ('009540', '한국조선해양'),
    ('034020', '두산에너빌'), ('047810', '한국항공우주'),
    ('009150', '삼성전기'),    ('086280', '현대글로비스'),
    ('034220', 'LG디스플레이'),('036570', '엔씨소프트'),
    ('003670', '포스코퓨처엠'),('247540', '에코프로비엠'),
    ('086520', '에코프로'),    ('035720', '카카오'),
    ('267250', 'HD현대'),      ('329180', 'HD현대중공업'),
    ('042660', '한화오션'),    ('079550', 'LIG넥스원'),
    ('064350', '현대로템'),    ('003490', '대한항공'),
    ('009830', '한화솔루션'), ('032640', 'LG유플러스'),
    ('024110', '기업은행'),    ('128940', '한미약품'),
    ('298040', '효성중공업'), ('021240', '코웨이'),
    ('035250', '강원랜드'),    ('004170', '신세계'),
]


def get_ohlcv(ticker: str, from_d: str, to_d: str) -> pd.DataFrame:
    df = krx.get_market_ohlcv_by_date(from_d, to_d, ticker)
    if df.empty:
        return df
    df = df.rename(columns={'시가':'open','고가':'high','저가':'low','종가':'close','거래량':'volume'})
    return df.dropna()


def run_backtest():
    today  = datetime.today()
    cutoff = today - timedelta(days=LOOKBACK_MONTHS * 30)
    from_d = (today - timedelta(days=LOOKBACK_MONTHS * 30 + 120)).strftime('%Y%m%d')
    to_d   = today.strftime('%Y%m%d')

    print(f'\nBNF 역추세 시뮬레이션')
    print(f'기간: 최근 {LOOKBACK_MONTHS}개월 ({cutoff.strftime("%Y-%m-%d")} ~ {today.strftime("%Y-%m-%d")})')
    print(f'손절 {STOP_PCT}% / 목표 +{TARGET_PCT}% / 최대 {MAX_DAYS}일')
    print(f'대상: {len(UNIVERSE)}종목\n')

    all_trades = []

    for ticker, name in UNIVERSE:
        try:
            df = get_ohlcv(ticker, from_d, to_d)
            if len(df) < 60:
                continue

            df = add_indicators(df, cfg)
            df = df.dropna()

            for i in range(30, len(df) - 1):
                if df.index[i] < pd.Timestamp(cutoff):
                    continue

                ok, _ = entry_signal(df, i, cfg, regime='neutral', cap='large')
                if not ok:
                    continue

                entry_price = float(df.iloc[i + 1]['open']) if i + 1 < len(df) else float(df.iloc[i]['close'])
                stop  = entry_price * (1 + STOP_PCT / 100)
                target = entry_price * (1 + TARGET_PCT / 100)

                result = 'hold'
                exit_pnl = 0.0
                future = df.iloc[i + 1: i + 1 + MAX_DAYS]

                for j in range(len(future)):
                    row = future.iloc[j]
                    if row['low'] <= stop:
                        result  = 'stop'
                        exit_pnl = STOP_PCT
                        break
                    if row['high'] >= target:
                        result  = 'target'
                        exit_pnl = TARGET_PCT
                        break

                if result == 'hold':
                    last_close = float(future.iloc[-1]['close']) if not future.empty else entry_price
                    exit_pnl   = (last_close - entry_price) / entry_price * 100

                all_trades.append({
                    'date'    : df.index[i].strftime('%Y-%m-%d'),
                    'ticker'  : ticker,
                    'name'    : name,
                    'entry'   : int(entry_price),
                    'stop'    : int(stop),
                    'target'  : int(target),
                    'result'  : result,
                    'pnl'     : round(exit_pnl, 1),
                })

        except Exception:
            continue

    # ── 결과 분석 ──────────────────────────────────────────
    if not all_trades:
        print('시뮬레이션 기간 내 신호 없음')
        return

    ds     = pd.DataFrame(all_trades)
    closed = ds[ds['result'] != 'hold']
    tgt    = closed[closed['result'] == 'target']
    stp    = closed[closed['result'] == 'stop']
    hold   = ds[ds['result'] == 'hold']
    wr     = len(tgt) / len(closed) * 100 if len(closed) else 0
    rr     = TARGET_PCT / abs(STOP_PCT)        # 손익비
    ev     = (wr / 100 * TARGET_PCT) - ((100 - wr) / 100 * abs(STOP_PCT))

    print('=' * 60)
    print('  BNF 시뮬레이션 결과')
    print('=' * 60)
    print(f'  총 신호   : {len(ds)}건')
    print(f'  완료      : {len(closed)}건  |  진행중: {len(hold)}건')
    print(f'  목표 달성 : {len(tgt)}건 ({wr:.1f}%)')
    print(f'  손절      : {len(stp)}건 ({100-wr:.1f}%)')
    print(f'  손익비    : 1:{rr:.1f}')
    print(f'  기대수익(EV): {ev:+.2f}%/거래')
    print()

    # 테스타와 비교
    print('  [테스타 vs BNF 비교]')
    print(f'  {"전략":<10} {"신호수":>6} {"승률":>7} {"EV/거래":>10}')
    print('  ' + '-' * 38)
    print(f'  {"BNF":<10} {len(ds):>6}건  {wr:>6.1f}%  {ev:>+9.2f}%')
    print(f'  {"테스타(참고)":<10} {"38":>6}건  {"46.2":>6}%  {"+8.70":>9}%')
    print()

    # 상세 내역
    print(f'  {"날짜":<12} {"종목":<14} {"결과":<8} {"손익":>7}')
    print('  ' + '-' * 45)
    for _, r in ds.sort_values('date').iterrows():
        icon = '✅' if r['result'] == 'target' else ('❌' if r['result'] == 'stop' else '⏳')
        print(f'  {r["date"]:<12} {r["name"]:<14} {icon}{r["result"]:<7} {r["pnl"]:>+6.1f}%')


if __name__ == '__main__':
    run_backtest()
