# -*- coding: utf-8 -*-
"""
BNF 역추세 전략 — 미국 S&P 500 일봉 시뮬레이션
진입 조건: 급락(-8%) + 이격도(-15%) + RSI(35↓) + MACD 0선 상향돌파
청산: 손절 -5% / 목표 +10% / 최대 20거래일
"""
import sys, os, warnings
sys.stdout.reconfigure(encoding='utf-8')
warnings.filterwarnings('ignore')

import pandas as pd
import numpy as np
import requests
from io import StringIO
from datetime import datetime, timedelta
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bnf_strategy import BNFConfig, entry_signal

# ── 설정 ─────────────────────────────────────────────────────
STOP_PCT   = -5.0
TARGET_PCT = 10.0
MAX_DAYS   = 20
LOOKBACK_MONTHS = 18

cfg = BNFConfig(
    rsi_oversold=35.0,
    disparity_largecap=-15.0,
    drop_threshold=-6.0,
)


def get_sp500_tickers() -> list:
    try:
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(url, headers=headers, timeout=15)
        df = pd.read_html(StringIO(resp.text))[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        print(f'S&P 500 티커 로드: {len(tickers)}개')
        return tickers
    except Exception as e:
        print(f'Wikipedia 오류: {e} → 핵심 종목만 사용')
        return [
            'AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA','AVGO','JPM','V',
            'MA','UNH','XOM','JNJ','PG','HD','MRK','ABBV','CVX','PEP',
            'COST','ADBE','CRM','AMD','NFLX','TMO','QCOM','LIN','ACN','TXN',
            'NEE','HON','RTX','SPGI','BA','GS','CAT','ISRG','AMGN','SYK',
            'BKNG','AXP','BLK','GILD','MDT','MU','C','WM','ZTS','SO',
        ]


def add_indicators_us(df: pd.DataFrame, cfg: BNFConfig) -> pd.DataFrame:
    """yfinance 데이터에 BNF 지표 추가 (컬럼명 소문자 변환)"""
    out = df.rename(columns={
        'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'
    }).copy()

    # 25일 EMA + 이격도
    out['ema25'] = out['close'].ewm(span=cfg.ema_period, adjust=False).mean()
    out['disparity'] = (out['close'] - out['ema25']) / out['ema25'] * 100

    # RSI (Wilder)
    delta = out['close'].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1/cfg.rsi_period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/cfg.rsi_period, adjust=False).mean()
    out['rsi'] = 100 - 100 / (1 + avg_g / avg_l.replace(0, np.nan))

    # MACD 히스토그램
    macd_line   = out['close'].ewm(span=cfg.macd_fast, adjust=False).mean() - \
                  out['close'].ewm(span=cfg.macd_slow, adjust=False).mean()
    signal_line = macd_line.ewm(span=cfg.macd_signal, adjust=False).mean()
    out['macd_hist'] = macd_line - signal_line

    # 5일 누적 수익률
    out['ret_n'] = out['close'].pct_change(cfg.drop_lookback) * 100
    out['range'] = out['high'] - out['low']

    return out


def run_us_backtest():
    today  = datetime.today()
    cutoff = today - timedelta(days=LOOKBACK_MONTHS * 30)
    start  = (today - timedelta(days=LOOKBACK_MONTHS * 30 + 120)).strftime('%Y-%m-%d')
    end    = today.strftime('%Y-%m-%d')

    print(f'\nBNF 역추세 미국주식 시뮬레이션')
    print(f'기간: {LOOKBACK_MONTHS}개월 ({cutoff.strftime("%Y-%m-%d")} ~ {today.strftime("%Y-%m-%d")})')
    print(f'손절 {STOP_PCT}% / 목표 +{TARGET_PCT}% / 최대 {MAX_DAYS}일')

    tickers = get_sp500_tickers()
    print(f'\n데이터 다운로드 중 ({len(tickers)}개)...')

    try:
        raw = yf.download(
            tickers, start=start, end=end,
            interval='1d', auto_adjust=True,
            group_by='ticker', progress=False, threads=True
        )
    except Exception as e:
        print(f'다운로드 오류: {e}')
        return

    print('분석 중...')
    all_trades = []

    for ticker in tickers:
        try:
            if ticker not in raw.columns.get_level_values(0):
                continue
            df_raw = raw[ticker].dropna()
            if len(df_raw) < 50:
                continue

            df = add_indicators_us(df_raw, cfg)
            df = df.dropna()

            for i in range(30, len(df) - 1):
                if df.index[i] < pd.Timestamp(cutoff):
                    continue

                ok, _ = entry_signal(df, i, cfg)
                if not ok:
                    continue

                entry_price = float(df.iloc[i+1]['open']) if i+1 < len(df) else float(df.iloc[i]['close'])
                stop_price  = entry_price * (1 + STOP_PCT / 100)
                tgt_price   = entry_price * (1 + TARGET_PCT / 100)

                result   = 'hold'
                exit_pnl = 0.0
                future   = df.iloc[i+1: i+1+MAX_DAYS]

                for j in range(len(future)):
                    row = future.iloc[j]
                    if row['low'] <= stop_price:
                        result, exit_pnl = 'stop', STOP_PCT
                        break
                    if row['high'] >= tgt_price:
                        result, exit_pnl = 'target', TARGET_PCT
                        break

                if result == 'hold':
                    last = float(future.iloc[-1]['close']) if not future.empty else entry_price
                    exit_pnl = (last - entry_price) / entry_price * 100

                all_trades.append({
                    'date'  : df.index[i].strftime('%Y-%m-%d'),
                    'ticker': ticker,
                    'result': result,
                    'pnl'   : round(exit_pnl, 1),
                    'disp'  : round(float(df.iloc[i]['disparity']), 1),
                    'rsi'   : round(float(df.iloc[i]['rsi']), 1),
                })

        except Exception:
            continue

    # ── 결과 분석 ─────────────────────────────────────────────
    if not all_trades:
        print('\n시뮬레이션 기간 내 신호 없음')
        print('→ 조건이 너무 엄격하거나 시장이 안정적이었습니다.')
        return

    ds     = pd.DataFrame(all_trades)
    closed = ds[ds['result'] != 'hold']
    tgt    = closed[closed['result'] == 'target']
    stp    = closed[closed['result'] == 'stop']
    wr     = len(tgt) / len(closed) * 100 if len(closed) else 0
    ev     = (wr/100 * TARGET_PCT) - ((100-wr)/100 * abs(STOP_PCT))

    print(f'\n{"="*60}')
    print(f'  BNF 미국주식 시뮬레이션 결과')
    print(f'{"="*60}')
    print(f'  총 신호   : {len(ds)}건  ({len(tickers)}종목 스캔)')
    print(f'  완료      : {len(closed)}건  |  보유중: {len(ds)-len(closed)}건')
    print(f'  목표 달성 : {len(tgt)}건 ({wr:.1f}%)')
    print(f'  손절      : {len(stp)}건 ({100-wr:.1f}%)')
    print(f'  기대수익(EV): {ev:+.2f}%/거래')
    print()
    print(f'  [전략 비교]')
    print(f'  {"전략":<14} {"신호":>5} {"승률":>7} {"EV":>9}')
    print('  ' + '-'*38)
    print(f'  {"BNF(미국)": <14} {len(ds):>5}건  {wr:>6.1f}%  {ev:>+8.2f}%')
    print(f'  {"테스타(한국)":<14} {"38":>5}건  {"46.2":>6}%  {"+8.70":>8}%')
    print()

    print(f'  최근 신호 20건:')
    print(f'  {"날짜":<12} {"티커":<8} {"이격":>7} {"RSI":>6} {"결과":<8} {"손익":>7}')
    print('  ' + '-'*52)
    for _, r in ds.sort_values('date').tail(20).iterrows():
        icon = '✅' if r['result']=='target' else ('❌' if r['result']=='stop' else '⏳')
        print(f'  {r["date"]:<12} {r["ticker"]:<8} {r["disp"]:>6.1f}%  {r["rsi"]:>5.1f}  '
              f'{icon}{r["result"]:<7} {r["pnl"]:>+6.1f}%')


if __name__ == '__main__':
    run_us_backtest()
