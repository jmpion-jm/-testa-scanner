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

# 한글 폰트
for font in ['Malgun Gothic', 'NanumGothic', 'AppleGothic']:
    if any(font.replace(' ','').lower() in f.replace(' ','').lower() for f in fm.findSystemFonts()):
        plt.rcParams['font.family'] = font
        break
plt.rcParams['axes.unicode_minus'] = False

# ── 보유 ETF 목록 ──────────────────────────────────────────────
ETFS = {
    # 테마형 (성장/공격)
    '0023A0.KS': ('SOL 양자컴퓨팅TOP10',     '테마'),
    '466950.KS': ('TIGER 글로벌AI액티브',     '테마'),
    '491010.KS': ('TIGER 글로벌AI인프라',     '테마'),
    '465580.KS': ('ACE 미국빅테크TOP7',       '테마'),
    '364980.KS': ('TIGER 2차전지TOP10',       '테마'),
    '381170.KS': ('TIGER 미국테크TOP10',      '테마'),
    '428510.KS': ('KODEX 차이나AI테크',       '테마'),
    '0051G0.KS': ('SOL 미국원자력SMR',        '테마'),
    '457480.KS': ('ACE 테슬라밸류체인',       '테마'),
    # 지수형 (안정성장)
    '379800.KS': ('KODEX 미국S&P500',         '지수'),
    # 현물
    '411060.KS': ('ACE KRX금현물',            '현물'),
    # 단기금리 (현금대용)
    '357870.KS': ('TIGER CD금리투자KIS',      '단기금리'),
    # 혼합/채권형 (안정)
    '0111P0.KS': ('나스닥100미국채혼합50',    '혼합채권'),
    '434060.KS': ('KODEX TDF2050액티브',      '혼합채권'),
    '0025N0.KS': ('TIGER TDF2045',            '혼합채권'),
    '484790.KS': ('KODEX 미국30년채권(H)',    '혼합채권'),
    '0040X0.KS': ('SOL 팔란티어채권커버드콜','혼합채권'),
    '447770.KS': ('TIGER 테슬라채권혼합',     '혼합채권'),
}

MA_PERIOD  = 10
INITIAL    = 10_000_000
COMMISSION = 0.001

CATEGORY_ORDER = ['테마', '지수', '현물', '단기금리', '혼합채권']
CATEGORY_COLOR = {
    '테마':    '#e74c3c',
    '지수':    '#2980b9',
    '현물':    '#f39c12',
    '단기금리':'#27ae60',
    '혼합채권':'#8e44ad',
}


def fetch_monthly(ticker: str) -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period='5y', interval='1mo', auto_adjust=True)
    if df.empty:
        df = t.history(period='max', interval='1mo', auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df = df[['Open','High','Low','Close','Volume']].dropna()
    return df


def backtest(df: pd.DataFrame, initial: float = INITIAL):
    df = df.copy()
    df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
    df_clean = df.dropna().copy()

    if len(df_clean) < 3:
        return None

    cash, shares = initial, 0.0
    in_market = False
    trades = []
    equity = []

    for i in range(1, len(df_clean)):
        prev = df_clean.iloc[i-1]
        curr = df_clean.iloc[i]
        price      = float(curr['Close'])
        ma         = float(curr['MA'])
        prev_price = float(prev['Close'])
        prev_ma    = float(prev['MA'])

        if not in_market and prev_price < prev_ma and price > ma:
            shares = cash / (price * (1 + COMMISSION))
            cash = 0.0
            in_market = True
            trades.append(('BUY', curr.name, price))
        elif in_market and prev_price > prev_ma and price < ma:
            cash = shares * price * (1 - COMMISSION)
            shares = 0.0
            in_market = False
            trades.append(('SELL', curr.name, price))

        equity.append({'date': curr.name, 'equity': cash + shares * price})

    if in_market:
        cash = shares * float(df_clean.iloc[-1]['Close']) * (1 - COMMISSION)

    if not equity:
        return None

    eq_df = pd.DataFrame(equity).set_index('date')
    total_return = (cash - initial) / initial * 100

    eq = eq_df['equity']
    mdd = float(((eq - eq.cummax()) / eq.cummax() * 100).min())

    p0 = float(df_clean.iloc[0]['Close'])
    p1 = float(df_clean.iloc[-1]['Close'])
    bh_return = (p1 - p0) / p0 * 100

    buys  = [t for t in trades if t[0]=='BUY']
    sells = [t for t in trades if t[0]=='SELL']
    wins  = sum(1 for i in range(min(len(buys), len(sells))) if sells[i][2] > buys[i][2])
    n     = min(len(buys), len(sells))
    win_rate = wins / n * 100 if n else 0

    start_date = df_clean.index[0].strftime('%Y.%m')
    end_date   = df_clean.index[-1].strftime('%Y.%m')
    months     = len(df_clean)

    return dict(
        total_return=total_return, bh_return=bh_return, mdd=mdd,
        n_trades=len(buys), win_rate=win_rate,
        equity_df=eq_df, df=df_clean,
        start=start_date, end=end_date, months=months,
    )


def main():
    print('한국 ETF 월봉 10이평선 시뮬레이션')
    print('=' * 72)

    results = {}
    for ticker, (name, cat) in ETFS.items():
        short = ticker.replace('.KS', '')
        try:
            df = fetch_monthly(ticker)
            if len(df) < 3:
                print(f'  [{cat}] {name} ({short}): 데이터 없음')
                continue
            r = backtest(df)
            if r is None:
                print(f'  [{cat}] {name} ({short}): 데이터 부족 (MA계산 불가)')
                continue
            results[ticker] = {'name': name, 'cat': cat, 'ticker_short': short, **r}
            ma_ok = '가능' if r['months'] > MA_PERIOD else '부족'
            beat  = 'O' if r['total_return'] > r['bh_return'] else 'X'
            print(f"  [{cat}] {name[:14]:<14} {r['start']}~{r['end']} "
                  f"({r['months']}개월/{ma_ok}) "
                  f"전략{r['total_return']:+.0f}% B&H{r['bh_return']:+.0f}% "
                  f"MDD{r['mdd']:.0f}% [{beat}]")
        except Exception as e:
            print(f'  [{cat}] {name} ({short}): 오류 - {e}')

    if not results:
        print('결과 없음')
        return

    # ── 결과 테이블 ──
    print()
    print('=' * 72)
    hdr = f"{'구분':<6} {'종목명':<18} {'기간':<14} {'전략':>8} {'B&H':>8} {'MDD':>7} {'거래':>4} {'초과'}"
    print(hdr)
    print('-' * 72)
    for cat in CATEGORY_ORDER:
        cat_items = [(k,v) for k,v in results.items() if v['cat']==cat]
        if not cat_items:
            continue
        for ticker, r in cat_items:
            beat = 'O' if r['total_return'] > r['bh_return'] else 'X'
            period = f"{r['start']}~{r['end']}"
            print(f"{cat:<6} {r['name'][:16]:<18} {period:<14} "
                  f"{r['total_return']:>+7.0f}% {r['bh_return']:>+7.0f}% "
                  f"{r['mdd']:>6.0f}% {r['n_trades']:>3}회  {beat}")
    print('=' * 72)

    # ── 통계 요약 ──
    beat_count = sum(1 for r in results.values() if r['total_return'] > r['bh_return'])
    avg_strat  = np.mean([r['total_return'] for r in results.values()])
    avg_bh     = np.mean([r['bh_return'] for r in results.values()])
    avg_mdd    = np.mean([r['mdd'] for r in results.values()])
    print(f'\n[요약] 총 {len(results)}개 ETF | 전략초과: {beat_count}개')
    print(f'  평균 전략수익: {avg_strat:+.1f}%  |  평균 B&H: {avg_bh:+.1f}%  |  평균 MDD: {avg_mdd:.1f}%')

    # ── 카테고리별 분석 ──
    print()
    print('[카테고리별 분석]')
    for cat in CATEGORY_ORDER:
        cat_items = [v for v in results.values() if v['cat']==cat]
        if not cat_items:
            continue
        a_s = np.mean([r['total_return'] for r in cat_items])
        a_b = np.mean([r['bh_return'] for r in cat_items])
        a_m = np.mean([r['mdd'] for r in cat_items])
        beats = sum(1 for r in cat_items if r['total_return'] > r['bh_return'])
        print(f"  {cat:<6}: 전략 {a_s:+.0f}%  B&H {a_b:+.0f}%  MDD {a_m:.0f}%  초과 {beats}/{len(cat_items)}")

    # ── 차트 ──
    valid = [(k,v) for k,v in results.items() if len(v['equity_df']) > 2]
    if not valid:
        return

    n = len(valid)
    cols = 3
    rows_c = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_c, cols, figsize=(18, rows_c * 3.8))
    axes_flat = axes.flatten() if n > 1 else [axes]

    for idx, (ticker, r) in enumerate(valid):
        ax = axes_flat[idx]
        eq  = r['equity_df']['equity']
        p0  = float(r['df'].iloc[0]['Close'])
        bh  = r['df']['Close'] / p0 * INITIAL
        bh  = bh[bh.index.isin(eq.index)]
        color = CATEGORY_COLOR.get(r['cat'], 'steelblue')

        ax.plot(eq.index, eq.values, color=color, linewidth=1.8, label='전략(10MA)')
        ax.plot(bh.index, bh.values, color='gray', linewidth=1.1, linestyle='--', label='B&H')
        ax.axhline(INITIAL, color='black', linewidth=0.5, linestyle=':')

        beat = 'O' if r['total_return'] > r['bh_return'] else 'X'
        title = (f"[{r['cat']}] {r['name']}\n"
                 f"전략{r['total_return']:+.0f}%  B&H{r['bh_return']:+.0f}%  "
                 f"MDD{r['mdd']:.0f}%  [{beat}]")
        ax.set_title(title, fontsize=8.5)
        ax.legend(fontsize=7)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x/1e6:.1f}M'))
        ax.grid(True, alpha=0.25)
        ax.tick_params(labelsize=7)

    for idx in range(len(valid), len(axes_flat)):
        axes_flat[idx].set_visible(False)

    fig.suptitle('DC/IRP/연금 ETF — 월봉 10이평선 전략 vs B&H', fontsize=13, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '한국ETF_시뮬레이션.png')
    plt.savefig(out, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'\n차트 저장: {out}')

    # ── 투자 전략 제언 ──
    print()
    print('[DC/IRP/연금 운용 전략 제언]')
    print('  테마형 ETF : 변동성 크므로 → 10이평 이탈 시 단기금리/채권으로 교체 고려')
    print('  S&P500/지수: 장기 B&H 우세 → 적립식 유지, 10이평은 MDD 관리용만')
    print('  혼합/채권형 : 안전자산 역할 → B&H 유지')
    print('  단기금리(CD): 현금성 자산 → 테마 이탈 시 피난처')
    print()
    print('  ★ 실용 전략: 테마ETF 월봉 10이평 이탈 → TIGER CD금리로 교체')
    print('               테마ETF 월봉 10이평 돌파 → 테마ETF 재진입')


if __name__ == '__main__':
    main()
