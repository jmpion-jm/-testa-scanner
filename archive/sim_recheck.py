# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import yfinance as yf
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

MA = 10
COMMISSION = 0.001

US_STOCKS = {
    'LHX':  ('L3해리스테크',       '방산_watchlist'),
    'RTX':  ('RTX',               '방산_watchlist'),
    'CRS':  ('카펜터테크',         '방산_watchlist'),
    'ATI':  ('ATI',               '방산_watchlist'),
    'NVDA': ('엔비디아',           'AI_watchlist'),
    'AVGO': ('브로드컴',           'AI_watchlist'),
    'AMD':  ('AMD',               'AI_watchlist'),
    'ALAB': ('아스테라랩스',       'AI_watchlist'),
    'NTAP': ('넷앱',              'AI_watchlist'),
    'MRVL': ('마벨테크',           '반도체_watchlist'),
    'APH':  ('암페놀',             '반도체_watchlist'),
    'COHR': ('코히런트',           '반도체_watchlist'),
    'LITE': ('루멘텀',             '반도체_watchlist'),
    'GLW':  ('코닝',              '반도체_watchlist'),
    'TDY':  ('텔레다인',           '반도체_watchlist'),
    'VRT':  ('버티브',             '전력_watchlist'),
    'BWXT': ('BWX테크',           '원자로_watchlist'),
    'CRWD': ('크라우드스트라이크',  '보안_watchlist'),
    'ASTS': ('AST스페이스모바일',  'watchonly'),
    'IONQ': ('아이온큐',           'watchonly'),
    'AAOI': ('어플라이드옵토',     '제외종목'),
    'NVTS': ('나비타스세미',       '제외종목'),
    'ON':   ('온세미',             '제외종목'),
    'PLUG': ('플러그파워',         '제외종목'),
}

def fetch_monthly(ticker, years=15):
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

def backtest(ticker):
    df = fetch_monthly(ticker)
    if len(df) < MA + 2:
        return None
    df['MA'] = df['Close'].rolling(MA).mean()
    df['above'] = df['Close'] > df['MA']
    df['cross_up']   = df['above'] & ~df['above'].shift(1).fillna(False)
    df['cross_down']  = ~df['above'] & df['above'].shift(1).fillna(False)

    cash, shares = 1.0, 0.0
    trades = []
    entry_p, entry_d = None, None

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
            months = (date - entry_d).days / 30
            trades.append({'ret': (price / entry_p - 1) * 100, 'months': months})
            shares = 0.0

    final = float(df['Close'].iloc[-1])
    if shares > 0:
        cash = shares * final * (1 - COMMISSION)

    years_actual = len(df) / 12
    cagr = (cash ** (1 / years_actual) - 1) * 100 if years_actual > 0 and cash > 0 else -99
    total_ret = (cash - 1.0) * 100
    win = sum(1 for t in trades if t['ret'] > 0)
    win_rate = win / len(trades) * 100 if trades else 0
    avg_ret = sum(t['ret'] for t in trades) / len(trades) if trades else 0

    return {
        'cagr': cagr,
        'total_ret': total_ret,
        'trades': len(trades),
        'win_rate': win_rate,
        'avg_ret': avg_ret,
        'months': len(df),
    }

print("=" * 75)
print(f"{'구분':16s} {'티커':6s} {'종목명':16s} {'CAGR':>7s} {'총수익':>8s} {'매매':>4s} {'승률':>5s} {'판정':>6s}")
print("=" * 75)

results = []
for ticker, (name, sector) in US_STOCKS.items():
    r = backtest(ticker)
    if r:
        if r['cagr'] > 8 and r['trades'] >= 2:
            verdict = '✅ 적합'
        elif r['cagr'] > 0:
            verdict = '⚠️ 보통'
        else:
            verdict = '❌ 부적합'
        print(f"{sector:16s} {ticker:6s} {name:16s} {r['cagr']:+6.1f}% {r['total_ret']:+7.0f}% {r['trades']:4d}회 {r['win_rate']:4.0f}% {verdict}")
        results.append({'ticker': ticker, 'name': name, 'sector': sector, **r})
    else:
        print(f"{sector:16s} {ticker:6s} {name:16s} {'데이터부족':>30s}")

print()
print("=== 섹터별 평균 CAGR ===")
df_r = pd.DataFrame(results)
for sec, grp in df_r.groupby('sector'):
    print(f"  {sec:20s}: 평균 CAGR {grp['cagr'].mean():+.1f}%")
