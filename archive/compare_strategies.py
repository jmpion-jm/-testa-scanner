# -*- coding: utf-8 -*-
"""
매매법 비교 시뮬레이션 — 실제 보유 종목 대상
==============================================
전략 4가지 비교:
  1. B&H        : 그냥 보유 (기준선)
  2. MA10 단순  : 10이평 돌파 매수 / 이탈 즉시 매도
  3. MA12 성승현: 12이평 기준 동일
  4. A안 신버전 : 10이평 + 휩쏘방지 + 진입제한 + 추격금지

판정: 총수익률 / MDD / 효율지수(수익÷|MDD|) 기준
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

MIN_MONTHS = 24   # 최소 데이터 기간
TRAIL_STOP = 20.0  # 고점 추적 손절: 고점 대비 -20%
CONFIRM_MO = 2     # 확인 매도: 2개월 연속 이탈 시 매도

# ── 실제 보유 종목 (채권·CD금리 제외, 주식형만) ──────────────
TARGETS = [
    # 미국 개별주 (데이터 풍부)
    ('CRWD',     'CrowdStrike',        'US'),
    ('ASTS',     'AST스페이스모바일',  'US'),
    # 한국 테마 ETF
    ('466950.KS', 'TIGER 글로벌AI액티브',      'KR'),
    ('491010.KS', 'TIGER 글로벌AI인프라',      'KR'),
    ('465580.KS', 'ACE 미국빅테크TOP7',        'KR'),
    ('364980.KS', 'TIGER 2차전지TOP10',        'KR'),
    ('381170.KS', 'TIGER 미국테크TOP10',       'KR'),
    ('428510.KS', 'KODEX 차이나AI테크',        'KR'),
    ('411060.KS', 'ACE KRX금현물',             'KR'),
    ('457480.KS', 'ACE 테슬라밸류체인',        'KR'),
    ('466920.KS', 'SOL 조선TOP3플러스',        'KR'),
    ('0023A0.KS', 'SOL 미국양자컴퓨팅TOP10',  'KR'),
    ('0051G0.KS', 'SOL 미국원자력SMR',         'KR'),
    ('381180.KS', 'TIGER 필라델피아반도체',    'KR'),
    ('360750.KS', 'TIGER 미국S&P500',          'KR'),
]

# A안 신버전 파라미터 (최적화 결과)
A_SELL_MIN   = -2.0   # 이탈 후 -2% 이내는 관망 (휩쏘방지)
A_BUY_MAX    = 10.0   # 돌파 시 +10% 이내만 진입
A_CHASE_D0   = 20.0   # 이격 +20% 초과 시 추격금지
A_CHASE_MO   = 3      # 3개월 연속 상승 시 추격금지


# ── 데이터 수집 ───────────────────────────────────────────────
def fetch(ticker: str) -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period='max', interval='1mo', auto_adjust=True)
    if df.empty:
        return pd.DataFrame()
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df = df[['Close']].dropna()
    # 현재 진행 중인 월 제거 (미확정)
    now = pd.Timestamp.now()
    if not df.empty and df.index[-1].month == now.month and df.index[-1].year == now.year:
        df = df.iloc[:-1]
    return df


# ── 백테스트 엔진 ─────────────────────────────────────────────
def run_bnh(close: pd.Series) -> dict:
    ret = (close.iloc[-1] / close.iloc[0] - 1) * 100
    ps  = close / close.iloc[0]
    mdd = _mdd(ps)
    return {'ret': ret, 'mdd': mdd, 'n': 1, 'wr': 100.0}


def run_ma(close: pd.Series, ma_period: int,
           sell_min: float = None,
           buy_max: float = None,
           chase_d0: float = None,
           chase_mo: int = None) -> dict:
    """
    공통 백테스트 엔진
    sell_min  : None이면 즉시매도, 값 있으면 그 이하일 때만 매도 (휩쏘방지)
    buy_max   : None이면 진입제한 없음, 값 있으면 돌파 후 그 이내만 매수
    chase_d0  : 이격도 추격금지 기준
    chase_mo  : 연속상승월 추격금지 기준
    """
    ma = close.rolling(ma_period, min_periods=ma_period).mean()

    cash, shares = 1.0, 0.0
    in_pos = False
    entry_price = 0.0
    trades = []
    port = []

    for i in range(len(close)):
        c  = float(close.iloc[i])
        m  = ma.iloc[i]
        if pd.isna(m):
            port.append(cash + shares * c)
            continue
        m   = float(m)
        d0  = (c - m) / m * 100
        prev_above = (float(close.iloc[i-1]) > float(ma.iloc[i-1])) if i > 0 and not pd.isna(ma.iloc[i-1]) else False
        curr_above = c > m
        cross_up   = curr_above and not prev_above

        # 연속 상승 월 계산
        cons_up = 0
        if chase_mo:
            for j in range(i, 0, -1):
                if float(close.iloc[j]) > float(close.iloc[j-1]):
                    cons_up += 1
                else:
                    break

        # 신호 결정
        if not curr_above:
            if sell_min is not None:
                signal = 'sell' if d0 <= sell_min else 'hold'
            else:
                signal = 'sell'
        else:
            # 이평 위
            if cross_up:
                # 신규 돌파
                if buy_max is not None and d0 > buy_max:
                    signal = 'block'   # 진입 조건 미충족
                else:
                    signal = 'buy'
            elif in_pos:
                signal = 'hold'
            else:
                # 이평 위지만 신규 돌파 아님 → 추격금지 체크
                blocked = False
                if chase_d0 and d0 >= chase_d0:
                    blocked = True
                if chase_mo and cons_up >= chase_mo:
                    blocked = True
                signal = 'block' if blocked else 'hold'

        # 포지션 실행
        if not in_pos:
            if signal == 'buy':
                in_pos = True
                entry_price = c
                shares = cash / c
                cash = 0.0
        else:
            if signal == 'sell':
                in_pos = False
                pnl = (c - entry_price) / entry_price * 100
                trades.append(pnl)
                cash = shares * c
                shares = 0.0

        port.append(cash + shares * c)

    # 미청산 마감
    if in_pos:
        c   = float(close.iloc[-1])
        pnl = (c - entry_price) / entry_price * 100
        trades.append(pnl)
        port[-1] = shares * c

    ps  = pd.Series(port)
    ret = (ps.iloc[-1] - 1.0) * 100
    mdd = _mdd(ps)
    n   = len(trades)
    wr  = sum(1 for t in trades if t > 0) / n * 100 if n else 0
    return {'ret': ret, 'mdd': mdd, 'n': n, 'wr': wr}


def run_confirm_sell(close: pd.Series, ma_period: int, confirm: int = 2) -> dict:
    """N개월 연속 이탈 시만 매도 — 휩쏘 대폭 감소"""
    ma = close.rolling(ma_period, min_periods=ma_period).mean()
    cash, shares = 1.0, 0.0
    in_pos = False
    entry_price = 0.0
    below_streak = 0
    trades = []
    port = []

    for i in range(len(close)):
        c = float(close.iloc[i])
        m = ma.iloc[i]
        if pd.isna(m):
            port.append(cash + shares * c)
            continue
        m = float(m)
        prev_above = (float(close.iloc[i-1]) > float(ma.iloc[i-1])) if i > 0 and not pd.isna(ma.iloc[i-1]) else False
        curr_above = c > m
        cross_up = curr_above and not prev_above

        if curr_above:
            below_streak = 0
        else:
            below_streak += 1

        if not in_pos:
            if cross_up:
                in_pos = True
                entry_price = c
                shares = cash / c
                cash = 0.0
        else:
            if below_streak >= confirm:
                in_pos = False
                pnl = (c - entry_price) / entry_price * 100
                trades.append(pnl)
                cash = shares * c
                shares = 0.0
                below_streak = 0

        port.append(cash + shares * c)

    if in_pos:
        c = float(close.iloc[-1])
        trades.append((c - entry_price) / entry_price * 100)
        port[-1] = shares * c

    ps = pd.Series(port)
    ret = (ps.iloc[-1] - 1.0) * 100
    n = len(trades)
    wr = sum(1 for t in trades if t > 0) / n * 100 if n else 0
    return {'ret': ret, 'mdd': _mdd(ps), 'n': n, 'wr': wr}


def run_trail_stop(close: pd.Series, ma_period: int, trail_pct: float = 20.0) -> dict:
    """고점 대비 -trail_pct% 이탈 시 매도 — B&H에 가장 가까운 구조"""
    ma = close.rolling(ma_period, min_periods=ma_period).mean()
    cash, shares = 1.0, 0.0
    in_pos = False
    entry_price = 0.0
    peak = 0.0
    trades = []
    port = []

    for i in range(len(close)):
        c = float(close.iloc[i])
        m = ma.iloc[i]
        if pd.isna(m):
            port.append(cash + shares * c)
            continue
        m = float(m)
        prev_above = (float(close.iloc[i-1]) > float(ma.iloc[i-1])) if i > 0 and not pd.isna(ma.iloc[i-1]) else False
        curr_above = c > m
        cross_up = curr_above and not prev_above

        if not in_pos:
            if cross_up:
                in_pos = True
                entry_price = c
                peak = c
                shares = cash / c
                cash = 0.0
        else:
            if c > peak:
                peak = c
            drop_from_peak = (c - peak) / peak * 100
            if drop_from_peak <= -trail_pct:
                in_pos = False
                pnl = (c - entry_price) / entry_price * 100
                trades.append(pnl)
                cash = shares * c
                shares = 0.0
                peak = 0.0

        port.append(cash + shares * c)

    if in_pos:
        c = float(close.iloc[-1])
        trades.append((c - entry_price) / entry_price * 100)
        port[-1] = shares * c

    ps = pd.Series(port)
    ret = (ps.iloc[-1] - 1.0) * 100
    n = len(trades)
    wr = sum(1 for t in trades if t > 0) / n * 100 if n else 0
    return {'ret': ret, 'mdd': _mdd(ps), 'n': n, 'wr': wr}


def _mdd(ps: pd.Series) -> float:
    rmx = ps.expanding().max()
    return float(((ps - rmx) / rmx * 100).min())


def eff(r: dict) -> float:
    return r['ret'] / abs(r['mdd']) if r['mdd'] != 0 else 0


# ── 메인 ─────────────────────────────────────────────────────
def main():
    print('=' * 90)
    print('  매매법 비교 시뮬레이션 — 실제 보유 종목 대상')
    print('  전략: ① B&H  ② MA10 단순  ③ MA12 성승현  ④ MA20 느슨한  ⑤ A안 신버전  ⑥ 2개월확인매도  ⑦ 고점추적손절')
    print('=' * 90)

    results = []
    skipped = []

    for ticker, name, market in TARGETS:
        df = fetch(ticker)
        if df.empty or len(df) < MIN_MONTHS:
            skipped.append((name, ticker, len(df) if not df.empty else 0))
            continue

        close  = df['Close']
        months = len(close)
        start  = df.index[0].strftime('%Y.%m')
        end    = df.index[-1].strftime('%Y.%m')

        bnh   = run_bnh(close)
        ma10  = run_ma(close, 10)
        ma12  = run_ma(close, 12)
        ma20  = run_ma(close, 20)
        avan  = run_ma(close, 10,
                       sell_min=A_SELL_MIN,
                       buy_max=A_BUY_MAX,
                       chase_d0=A_CHASE_D0,
                       chase_mo=A_CHASE_MO)
        conf  = run_confirm_sell(close, 10, confirm=CONFIRM_MO)
        trail = run_trail_stop(close, 10, trail_pct=TRAIL_STOP)

        results.append({
            'ticker': ticker, 'name': name, 'market': market,
            'months': months, 'period': f'{start}~{end}',
            'bnh': bnh, 'ma10': ma10, 'ma12': ma12, 'ma20': ma20,
            'avan': avan, 'conf': conf, 'trail': trail,
        })

        print(f'\n  [{name}] ({ticker})  {start}~{end}  {months}개월')
        print(f"  {'전략':<16} {'수익률':>9} {'MDD':>8} {'효율':>7} {'거래':>5} {'승률':>6}")
        print(f"  {'-'*58}")
        for label, r in [
            ('B&H',           bnh),
            ('MA10 단순',     ma10),
            ('MA12 성승현',   ma12),
            ('MA20 느슨한',   ma20),
            ('A안 신버전',    avan),
            ('2개월확인매도', conf),
            ('고점추적손절',  trail),
        ]:
            e = eff(r)
            print(f"  {label:<16} {r['ret']:>+8.1f}%  {r['mdd']:>7.1f}%  {e:>6.2f}  {r['n']:>4}회  {r['wr']:>5.1f}%")

    # ── 종합 순위표 ───────────────────────────────────────────
    if results:
        print('\n' + '=' * 90)
        print('  【 종합 평균 비교 】')
        print('=' * 90)

        strats = [
            ('B&H',           lambda r: r['bnh']),
            ('MA10 단순',     lambda r: r['ma10']),
            ('MA12 성승현',   lambda r: r['ma12']),
            ('MA20 느슨한',   lambda r: r['ma20']),
            ('A안 신버전',    lambda r: r['avan']),
            ('2개월확인매도', lambda r: r['conf']),
            ('고점추적손절',  lambda r: r['trail']),
        ]

        summary = []
        for label, fn in strats:
            rets  = [fn(r)['ret']  for r in results]
            mdds  = [fn(r)['mdd']  for r in results]
            effs  = [eff(fn(r))    for r in results]
            wins  = [fn(r)['ret'] > fn(results[i])['ret']
                     for i, r in enumerate(results)
                     if label != 'B&H']  # B&H 대비 승리 종목 수

            # B&H 대비 우위 종목 수
            bnh_beats = sum(1 for r in results if fn(r)['ret'] > r['bnh']['ret'])

            summary.append({
                'label'    : label,
                'avg_ret'  : np.mean(rets),
                'avg_mdd'  : np.mean(mdds),
                'avg_eff'  : np.mean(effs),
                'bnh_beats': bnh_beats,
                'n'        : len(results),
            })

        summary.sort(key=lambda x: x['avg_eff'], reverse=True)

        print(f"  {'전략':<12} {'평균수익':>9} {'평균MDD':>9} {'평균효율':>8} {'B&H초과종목':>11}")
        print(f"  {'-'*56}")
        for i, s in enumerate(summary):
            rank = '🥇' if i == 0 else ('🥈' if i == 1 else ('🥉' if i == 2 else '  '))
            print(f"  {rank} {s['label']:<10} {s['avg_ret']:>+8.1f}%  {s['avg_mdd']:>8.1f}%  "
                  f"{s['avg_eff']:>7.2f}  {s['bnh_beats']:>4}/{s['n']}종목")

        winner = summary[0]['label']
        print(f"\n  ✅ 최종 채택 전략: 【 {winner} 】  (효율지수 1위)")
        print(f"     → 이 전략만 slack_alert.py에 반영, 나머지는 archive/ 이동")

    if skipped:
        print(f'\n  ⚠️  데이터 부족으로 제외된 종목 ({MIN_MONTHS}개월 미만):')
        for name, ticker, m in skipped:
            print(f'     {name} ({ticker}): {m}개월')

    print('\n' + '=' * 90)


if __name__ == '__main__':
    main()
