# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import FinanceDataReader as fdr
import yfinance as yf
import warnings
warnings.filterwarnings('ignore')

MA = 10
COMMISSION = 0.001

# 실제 보유 종목 (계좌별)
HOLDINGS = {
    'DC퇴직연금': [
        ('0023A0', 'SOL 양자컴퓨팅TOP10',    'KR'),
        ('466950', 'TIGER 글로벌AI액티브',    'KR'),
        ('491010', 'TIGER 글로벌AI인프라',    'KR'),
        ('465580', 'ACE 미국빅테크TOP7',      'KR'),
        ('364980', 'TIGER 2차전지TOP10',      'KR'),
        ('381170', 'TIGER 미국테크TOP10',     'KR'),
        ('428510', 'KODEX 차이나AI테크',      'KR'),
        ('0051G0', 'SOL 미국원자력SMR',       'KR'),
        ('411060', 'ACE KRX금현물',           'KR'),
        ('457480', 'ACE 테슬라밸류체인',       'KR'),
    ],
    '연금저축': [
        ('0023A0', 'SOL 양자컴퓨팅TOP10',    'KR'),
        ('144600', 'KODEX 은선물(H)',         'KR'),
        ('457480', 'ACE 테슬라밸류체인',       'KR'),
        ('466920', 'SOL 조선TOP3플러스',      'KR'),
        ('491010', 'TIGER 글로벌AI인프라',    'KR'),
    ],
    'IRP': [
        ('364980', 'TIGER 2차전지TOP10',      'KR'),
        ('381180', 'TIGER 필라델피아반도체',  'KR'),
        ('371460', 'TIGER 차이나전기차',      'KR'),
        ('381170', 'TIGER 미국테크TOP10',     'KR'),
        ('411060', 'ACE KRX금현물',           'KR'),
        ('457480', 'ACE 테슬라밸류체인',       'KR'),
    ],
    '일반계좌': [
        ('364980', 'TIGER 2차전지TOP10',      'KR'),
        ('466920', 'SOL 조선TOP3플러스',      'KR'),
        ('CRWD',   'CrowdStrike',             'US'),
    ],
}

def fetch_monthly_kr(code, years=10):
    start = (pd.Timestamp.now() - pd.DateOffset(years=years)).strftime('%Y-%m-%d')
    try:
        df = fdr.DataReader(code, start=start)
        if df is None or df.empty:
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        if 'Close' not in df.columns and '종가' in df.columns:
            df = df.rename(columns={'종가': 'Close'})
        if 'Close' not in df.columns:
            return pd.DataFrame()
        m = df[['Close']].resample('ME').last().dropna()
        now = pd.Timestamp.now()
        if not m.empty and m.index[-1].month == now.month and m.index[-1].year == now.year:
            m = m.iloc[:-1]
        return m
    except Exception as e:
        return pd.DataFrame()

def fetch_monthly_us(ticker, years=15):
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=str(years)+'y', interval='1mo', auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df.index = df.index.tz_localize(None) if df.index.tz else df.index
        df = df[['Close']].dropna()
        now = pd.Timestamp.now()
        if not df.empty and df.index[-1].month == now.month and df.index[-1].year == now.year:
            df = df.iloc[:-1]
        return df
    except Exception:
        return pd.DataFrame()

def backtest(code, name, market):
    df = fetch_monthly_kr(code) if market == 'KR' else fetch_monthly_us(code)
    if df.empty or len(df) < MA + 2:
        return None

    df['MA'] = df['Close'].rolling(MA).mean()
    df['above'] = df['Close'] > df['MA']
    df['cross_up']   = df['above'] & ~df['above'].shift(1).fillna(False)
    df['cross_down']  = ~df['above'] & df['above'].shift(1).fillna(False)

    cash, shares = 1.0, 0.0
    trades = []
    entry_p, entry_d = None, None
    months_data = len(df)

    for date, row in df.iterrows():
        if pd.isna(row['MA']):
            continue
        price = float(row['Close'])
        if row['cross_up'] and cash > 0:
            shares = cash * (1 - COMMISSION) / price
            entry_p, entry_d = price, date
            cash = 0.0
        elif row['cross_down'] and shares > 0:
            cash = shares * price * (1 - COMMISSION)
            trades.append({'ret': (price / entry_p - 1) * 100})
            shares = 0.0

    final = float(df['Close'].iloc[-1])
    if shares > 0:
        cash = shares * final * (1 - COMMISSION)

    years_actual = months_data / 12
    cagr = (cash ** (1 / years_actual) - 1) * 100 if years_actual > 0 and cash > 0 else -99
    total_ret = (cash - 1.0) * 100
    win = sum(1 for t in trades if t['ret'] > 0)
    win_rate = win / len(trades) * 100 if trades else 0

    return {
        'cagr': cagr,
        'total_ret': total_ret,
        'trades': len(trades),
        'win_rate': win_rate,
        'months': months_data,
    }

# 중복 제거하여 유니크 종목만 시뮬레이션
seen = {}
for account, items in HOLDINGS.items():
    for code, name, market in items:
        if code not in seen:
            seen[code] = (name, market)

print("=" * 72)
print(f"{'코드':8s} {'종목명':22s} {'데이터':>5s} {'CAGR':>7s} {'총수익':>8s} {'매매':>4s} {'승률':>5s} {'판정'}")
print("=" * 72)

sim_results = {}
for code, (name, market) in seen.items():
    r = backtest(code, name, market)
    sim_results[code] = r
    if r:
        months_str = f"{r['months']}개월"
        if r['cagr'] > 8 and r['trades'] >= 2:
            verdict = '✅ 적합'
        elif r['cagr'] > 0:
            verdict = '⚠️ 보통'
        else:
            verdict = '❌ 부적합'
        print(f"{code:8s} {name:22s} {months_str:>5s} {r['cagr']:+6.1f}% {r['total_ret']:+7.0f}% {r['trades']:4d}회 {r['win_rate']:4.0f}% {verdict}")
    else:
        df_check = fetch_monthly_kr(code) if market == 'KR' else fetch_monthly_us(code)
        months_str = f"{len(df_check)}개월" if not df_check.empty else "0개월"
        print(f"{code:8s} {name:22s} {months_str:>5s} {'데이터 부족 — 시뮬 불가':>35s}")

print()
print("=== 계좌별 평균 CAGR (데이터 충분한 종목 기준) ===")
for account, items in HOLDINGS.items():
    cagrs = []
    for code, name, market in items:
        r = sim_results.get(code)
        if r and r['months'] >= MA + 2:
            cagrs.append(r['cagr'])
    if cagrs:
        print(f"  {account:10s}: 평균 CAGR {sum(cagrs)/len(cagrs):+.1f}% ({len(cagrs)}종목)")
    else:
        print(f"  {account:10s}: 데이터 부족")
