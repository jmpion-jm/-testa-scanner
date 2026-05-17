# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import warnings
warnings.filterwarnings('ignore')

# 한글 폰트 설정
font_candidates = ['Malgun Gothic', 'NanumGothic', 'AppleGothic', 'DejaVu Sans']
selected_font = 'DejaVu Sans'
for font in font_candidates:
    matches = [f for f in fm.findSystemFonts() if font.replace(' ', '').lower() in f.replace(' ', '').lower()]
    if matches:
        selected_font = font
        break
plt.rcParams['font.family'] = selected_font
plt.rcParams['axes.unicode_minus'] = False

ETFS = {
    'QQQ':  '나스닥100 (QQQ)',
    'SPY':  'S&P500 (SPY)',
    'GLD':  '금 (GLD)',
    'TLT':  '미국장기국채 (TLT)',
    'VNQ':  '부동산 리츠 (VNQ)',
    'ARKK': '혁신기술 (ARKK)',
}

MA_PERIOD = 10
INITIAL   = 10_000_000  # 1천만원
COMMISSION = 0.001


def fetch_monthly(ticker: str) -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period='20y', interval='1mo', auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    return df


def backtest(df: pd.DataFrame, initial: float = INITIAL):
    df = df.copy()
    df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
    df.dropna(inplace=True)

    cash = initial
    shares = 0.0
    in_market = False
    trades = []
    equity = []

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        price = float(curr['Close'])
        ma    = float(curr['MA'])
        prev_price = float(prev['Close'])
        prev_ma    = float(prev['MA'])

        # 매수 신호: 이평 상향 돌파
        if not in_market and prev_price < prev_ma and price > ma:
            buy_price = price * (1 + COMMISSION)
            shares = cash / buy_price
            cash = 0.0
            in_market = True
            trades.append(('BUY', curr.name, price))

        # 매도 신호: 이평 하향 이탈
        elif in_market and prev_price > prev_ma and price < ma:
            sell_price = price * (1 - COMMISSION)
            cash = shares * sell_price
            shares = 0.0
            in_market = False
            trades.append(('SELL', curr.name, price))

        total = cash + shares * price
        equity.append({'date': curr.name, 'equity': total})

    # 미청산
    if in_market:
        final_price = float(df.iloc[-1]['Close']) * (1 - COMMISSION)
        cash = shares * final_price
        shares = 0.0

    equity_df = pd.DataFrame(equity).set_index('date')
    total_return = (cash - initial) / initial * 100

    # MDD
    eq = equity_df['equity']
    roll_max = eq.cummax()
    drawdown = (eq - roll_max) / roll_max * 100
    mdd = float(drawdown.min())

    # B&H
    start_price = float(df.iloc[0]['Close'])
    end_price   = float(df.iloc[-1]['Close'])
    bh_return   = (end_price - start_price) / start_price * 100

    # 승률
    buy_trades  = [t for t in trades if t[0] == 'BUY']
    sell_trades = [t for t in trades if t[0] == 'SELL']
    wins = 0
    for i in range(min(len(buy_trades), len(sell_trades))):
        if sell_trades[i][2] > buy_trades[i][2]:
            wins += 1
    n_closed = min(len(buy_trades), len(sell_trades))
    win_rate = wins / n_closed * 100 if n_closed else 0

    return {
        'final_cash': cash,
        'total_return': total_return,
        'bh_return': bh_return,
        'mdd': mdd,
        'n_trades': len(buy_trades),
        'win_rate': win_rate,
        'equity_df': equity_df,
        'df': df,
    }


def main():
    print('ETF 월봉 10이평선 시뮬레이션 (20년)')
    print('=' * 65)

    results = {}
    for ticker, name in ETFS.items():
        try:
            print(f'  {name} 데이터 로드 중...', end=' ', flush=True)
            df = fetch_monthly(ticker)
            if len(df) < MA_PERIOD + 5:
                print('데이터 부족')
                continue
            r = backtest(df)
            results[ticker] = {'name': name, **r}
            beat = 'O' if r['total_return'] > r['bh_return'] else 'X'
            print(f"전략 {r['total_return']:+.1f}%  B&H {r['bh_return']:+.1f}%  MDD {r['mdd']:.1f}%  승률 {r['win_rate']:.0f}%  [B&H초과:{beat}]")
        except Exception as e:
            print(f'오류: {e}')

    if not results:
        print('결과 없음')
        return

    # 결과 테이블 출력
    print()
    print('=' * 65)
    print(f"{'ETF':<6}  {'전략수익률':>10}  {'B&H수익률':>10}  {'MDD':>8}  {'거래횟수':>6}  {'승률':>6}  {'B&H초과'}")
    print('-' * 65)
    for ticker, r in results.items():
        beat = 'O' if r['total_return'] > r['bh_return'] else 'X'
        print(f"{ticker:<6}  {r['total_return']:>+9.1f}%  {r['bh_return']:>+9.1f}%  {r['mdd']:>7.1f}%  {r['n_trades']:>6}회  {r['win_rate']:>5.0f}%  {beat}")
    print('=' * 65)

    # 차트
    n = len(results)
    cols = 2
    rows_c = (n + 1) // cols
    fig, axes = plt.subplots(rows_c, cols, figsize=(14, rows_c * 4))
    axes = axes.flatten() if n > 1 else [axes]

    for idx, (ticker, r) in enumerate(results.items()):
        ax = axes[idx]
        eq = r['equity_df']['equity']
        bh_start = float(r['df'].iloc[0]['Close'])
        bh_values = r['df']['Close'] / bh_start * INITIAL
        bh_values = bh_values[bh_values.index.isin(eq.index)]

        ax.plot(eq.index, eq.values, label='전략 (10MA)', color='steelblue', linewidth=1.5)
        ax.plot(bh_values.index, bh_values.values, label='B&H', color='gray', linewidth=1, linestyle='--')
        ax.axhline(INITIAL, color='black', linewidth=0.5, linestyle=':')

        beat = 'O' if r['total_return'] > r['bh_return'] else 'X'
        title = f"{r['name']}\n전략{r['total_return']:+.0f}%  B&H{r['bh_return']:+.0f}%  [초과:{beat}]"
        ax.set_title(title, fontsize=9)
        ax.legend(fontsize=8)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x/1e6:.1f}M'))
        ax.grid(True, alpha=0.3)

    for idx in range(len(results), len(axes)):
        axes[idx].set_visible(False)

    fig.suptitle('ETF 월봉 10이평선 전략 vs B&H (20년)', fontsize=13, fontweight='bold')
    plt.tight_layout()

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ETF_시뮬레이션.png')
    plt.savefig(out_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'\n차트 저장: {out_path}')

    # 결론
    print()
    print('[결론]')
    beat_count = sum(1 for r in results.values() if r['total_return'] > r['bh_return'])
    print(f'  총 {len(results)}개 ETF 중 {beat_count}개가 B&H 초과 수익')
    print()
    for ticker, r in results.items():
        if r['total_return'] > r['bh_return']:
            print(f'  O  {r["name"]}: 전략이 B&H보다 {r["total_return"] - r["bh_return"]:+.1f}%p 우수')
        else:
            print(f'  X  {r["name"]}: B&H가 전략보다 {r["bh_return"] - r["total_return"]:+.1f}%p 우수')

    print()
    print('[해석]')
    print('  - QQQ/SPY 같은 강세장 지수 ETF: B&H가 보통 우세 (추세 매우 강함)')
    print('  - GLD/TLT 같은 방어/채권 ETF: 10MA 전략이 MDD 절감에 유리')
    print('  - ARKK: 변동성 크므로 10MA로 리스크 관리 효과')
    print('  - 결론: ETF는 개별주보다 B&H 우위가 강하나, MDD 관리 목적으로 10MA 활용 가능')


if __name__ == '__main__':
    main()
