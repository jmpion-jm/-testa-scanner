# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import warnings
warnings.filterwarnings('ignore')

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

# ── 설정 ─────────────────────────────────────────────────────
MA_PERIOD      = 10
INITIAL_CAPITAL = 100_000_000   # 1억 (종목당 균등 배분)
PERIOD         = "10y"
COMMISSION     = 0.001          # 거래비용 0.1%

STOCKS = {
    'LHX':  ('L3해리스테크',       '추천'),
    'ASTS': ('AST스페이스모바일',  '추천'),
    'NVDA': ('엔비디아',           '추천'),
    'IONQ': ('아이온큐',           '추천'),
    'AVGO': ('브로드컴',           'AI반도체'),
    'MRVL': ('마벨테크놀로지',     'AI반도체'),
    'AMD':  ('AMD',                'AI반도체'),
    'ALAB': ('아스테라랩스',       'AI반도체'),
    'NTAP': ('넷앱',               'AI반도체'),
    'VRT':  ('버티브홀딩스',       'AI반도체'),
    'ON':   ('온세미콘덕터',       '반도체소재'),
    'NVTS': ('나비타스세미',       '반도체소재'),
    'APH':  ('암페놀',             '휴머노이드'),
    'TDY':  ('텔레다인테크',       '우주위성'),
    'COHR': ('코히런트',           '우주위성'),
    'LITE': ('루멘텀홀딩스',       '우주위성'),
    'GLW':  ('코닝',               '우주위성'),
    'AAOI': ('어플라이드옵토',     '우주위성'),
    'RTX':  ('RTX',                '우주위성'),
    'ATI':  ('ATI',                '우주위성/희토류'),
    'CRS':  ('카펜터테크',         '우주위성'),
    'LDOS': ('레이도스(랜드)',      '우주위성'),
    'BWXT': ('BWX테크놀로지스',    '소형원자로'),
    'PLUG': ('플러그파워',         '소형원자로'),
}


# ── 데이터 수집 ───────────────────────────────────────────────
def fetch(ticker, period=PERIOD, interval='1mo'):
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open','High','Low','Close','Volume']].dropna()


# ── 단일 종목 백테스트 ────────────────────────────────────────
def backtest_single(ticker, name, capital):
    df = fetch(ticker)
    if len(df) < MA_PERIOD + 2:
        return None

    df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
    df['above'] = df['Close'] > df['MA']
    df['signal'] = 0
    df.loc[(df['above']) & (~df['above'].shift(1).fillna(False)), 'signal'] =  1
    df.loc[(~df['above']) & (df['above'].shift(1).fillna(False)), 'signal'] = -1

    cash, shares = capital, 0.0
    in_pos = False
    entry_p, entry_d = 0.0, None
    trades, portfolio = [], []

    for i, (date, row) in enumerate(df.iterrows()):
        if pd.isna(row['MA']):
            portfolio.append(cash)
            continue

        price = float(row['Close'])

        if row['signal'] == 1 and not in_pos:
            cost    = cash * (1 - COMMISSION)
            shares  = cost / price
            entry_p = price
            entry_d = date
            cash    = 0.0
            in_pos  = True

        elif row['signal'] == -1 and in_pos:
            cash   = shares * price * (1 - COMMISSION)
            pnl    = (price - entry_p) / entry_p * 100
            hm     = i - list(df.index).index(entry_d)
            trades.append(dict(
                entry_date=entry_d, exit_date=date,
                ep=entry_p, xp=price,
                pnl=round(pnl, 1), hm=hm,
            ))
            shares = 0.0
            in_pos = False

        portfolio.append(shares * price if in_pos else cash)

    # 미청산
    if in_pos:
        final = shares * float(df.iloc[-1]['Close']) * (1 - COMMISSION)
        pnl   = (float(df.iloc[-1]['Close']) - entry_p) / entry_p * 100
        trades.append(dict(
            entry_date=entry_d, exit_date=df.index[-1],
            ep=entry_p, xp=float(df.iloc[-1]['Close']),
            pnl=round(pnl, 1), hm=len(df), open=True,
        ))
    else:
        final = cash

    # Buy & Hold
    bh_ret = (float(df['Close'].iloc[-1]) - float(df['Close'].iloc[0])) / float(df['Close'].iloc[0]) * 100

    wins  = [t for t in trades if t['pnl'] > 0]
    losses= [t for t in trades if t['pnl'] <= 0]
    ps    = pd.Series(portfolio, index=df.index[:len(portfolio)])

    # MDD 계산
    roll_max = ps.cummax()
    dd       = (ps - roll_max) / roll_max * 100
    mdd      = round(dd.min(), 1)

    return dict(
        ticker=ticker, name=name,
        capital=capital, final=round(final),
        ret=round((final - capital) / capital * 100, 1),
        bh_ret=round(bh_ret, 1),
        alpha=round(((final - capital) / capital * 100) - bh_ret, 1),
        n_trades=len(trades),
        win_rate=round(len(wins)/len(trades)*100, 1) if trades else 0,
        avg_win=round(np.mean([t['pnl'] for t in wins]), 1) if wins else 0,
        avg_loss=round(np.mean([t['pnl'] for t in losses]), 1) if losses else 0,
        mdd=mdd,
        trades=trades,
        ps=ps,
        df=df,
        start=df.index[0].strftime('%Y.%m'),
        end=df.index[-1].strftime('%Y.%m'),
    )


# ── 포트폴리오 시뮬레이션 ─────────────────────────────────────
def run_portfolio():
    n         = len(STOCKS)
    per_stock = INITIAL_CAPITAL / n   # 균등 배분

    print(f'\n{"="*72}')
    print(f'  김학주 교수 추천종목 — 월봉 {MA_PERIOD}이평선 전략 포트폴리오 시뮬레이션')
    print(f'  초기 자본: {INITIAL_CAPITAL:,}원 | 종목당: {per_stock:,.0f}원 | 거래비용: {COMMISSION*100:.1f}%')
    print(f'{"="*72}')

    results = []
    for ticker, (name, sector) in STOCKS.items():
        print(f'  분석 중: {ticker} {name}...', end=' ', flush=True)
        r = backtest_single(ticker, name, per_stock)
        if r:
            r['sector'] = sector
            results.append(r)
            print(f'{r["ret"]:+.1f}%')
        else:
            print('데이터 부족')

    if not results:
        print('결과 없음')
        return

    # ── 개별 종목 성과표 ──────────────────────────────────────
    print(f'\n{"="*72}')
    print(f'  개별 종목 성과 (수익률 순)')
    print(f'{"="*72}')
    print(f'  {"티커":<6} {"종목명":<16} {"섹터":<12} '
          f'{"전략수익":>8} {"B&H":>8} {"알파":>8} '
          f'{"승률":>6} {"MDD":>7} {"거래":>5}')
    print(f'  {"-"*70}')

    sorted_r = sorted(results, key=lambda x: x['ret'], reverse=True)
    for r in sorted_r:
        alpha_str = f'{r["alpha"]:+.1f}%'
        alpha_col = alpha_str
        print(f'  {r["ticker"]:<6} {r["name"]:<16} {r["sector"]:<12} '
              f'{r["ret"]:>+7.1f}% {r["bh_ret"]:>+7.1f}% {alpha_col:>8} '
              f'{r["win_rate"]:>5.1f}% {r["mdd"]:>6.1f}% {r["n_trades"]:>4}회')

    # ── 포트폴리오 집계 ───────────────────────────────────────
    total_final   = sum(r['final'] for r in results)
    total_capital = INITIAL_CAPITAL
    port_ret      = (total_final - total_capital) / total_capital * 100

    bh_final  = sum(r['capital'] * (1 + r['bh_ret']/100) for r in results)
    bh_ret    = (bh_final - total_capital) / total_capital * 100

    best  = max(results, key=lambda x: x['ret'])
    worst = min(results, key=lambda x: x['ret'])
    avg_wr= np.mean([r['win_rate'] for r in results])
    avg_mdd= np.mean([r['mdd'] for r in results])

    winners = [r for r in results if r['ret'] > 0]
    losers  = [r for r in results if r['ret'] <= 0]

    print(f'\n{"="*72}')
    print(f'  포트폴리오 종합 성과')
    print(f'{"="*72}')
    print(f'  초기 투자금  : {total_capital:>15,}원')
    print(f'  최종 자산    : {total_final:>15,}원')
    print(f'  전략 수익률  : {port_ret:>+14.1f}%')
    print(f'  Buy&Hold    : {bh_ret:>+14.1f}%')
    print(f'  알파 (초과)  : {port_ret - bh_ret:>+14.1f}%p')
    print(f'  수익 종목    : {len(winners)}개 / 손실 종목: {len(losers)}개')
    print(f'  평균 승률    : {avg_wr:.1f}%')
    print(f'  평균 MDD     : {avg_mdd:.1f}%')
    print(f'  최고 종목    : {best["ticker"]} {best["name"]} ({best["ret"]:+.1f}%)')
    print(f'  최저 종목    : {worst["ticker"]} {worst["name"]} ({worst["ret"]:+.1f}%)')

    # ── 거래 상세 (상위 5개) ──────────────────────────────────
    print(f'\n{"="*72}')
    print(f'  상위 5개 종목 거래 내역')
    print(f'{"="*72}')
    for r in sorted_r[:5]:
        print(f'\n  [{r["ticker"]}] {r["name"]}  ({r["start"]}~{r["end"]})  '
              f'전략: {r["ret"]:+.1f}%  B&H: {r["bh_ret"]:+.1f}%')
        print(f'  {"─"*65}')
        for t in r['trades']:
            tag = '✅' if t['pnl'] > 0 else '❌'
            op  = ' (보유중)' if t.get('open') else ''
            print(f'  {tag} {t["entry_date"].strftime("%Y.%m")}~'
                  f'{t["exit_date"].strftime("%Y.%m")}  '
                  f'{t["ep"]:>9,.2f}→{t["xp"]:>9,.2f}  '
                  f'{t["pnl"]:>+6.1f}%  ({t["hm"]}개월){op}')

    # ── 시각화 ───────────────────────────────────────────────
    plot_portfolio(results, sorted_r, port_ret, bh_ret)

    return results


# ── 시각화 ───────────────────────────────────────────────────
def plot_portfolio(results, sorted_r, port_ret, bh_ret):
    fig = plt.figure(figsize=(20, 24), facecolor='#1a1a2e')
    gs  = gridspec.GridSpec(3, 1, figure=fig, hspace=0.4)

    # ── 1. 개별 종목 수익률 막대 차트 ──
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor('#16213e')

    tickers = [r['ticker'] for r in sorted_r]
    strat   = [r['ret']    for r in sorted_r]
    bh      = [r['bh_ret'] for r in sorted_r]

    x = np.arange(len(tickers))
    w = 0.35
    bars1 = ax1.bar(x - w/2, strat, w, label='월봉10이평 전략',
                    color=['#00ff88' if v >= 0 else '#ff4444' for v in strat], alpha=0.85)
    bars2 = ax1.bar(x + w/2, bh,    w, label='단순보유(B&H)',
                    color='#aaaaaa', alpha=0.5)

    ax1.axhline(0, color='#555', lw=1)
    ax1.set_xticks(x)
    ax1.set_xticklabels(tickers, rotation=45, fontsize=9, color='white')
    ax1.set_ylabel('수익률 (%)', color='white')
    ax1.set_title(f'개별 종목 수익률 비교 — 월봉 {MA_PERIOD}이평선 전략 vs 단순보유',
                  color='white', fontsize=13, fontweight='bold')
    ax1.legend(facecolor='#1a1a2e', labelcolor='white')
    ax1.tick_params(colors='white')
    for sp in ax1.spines.values(): sp.set_color('#444')
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f'{v:+.0f}%'))

    # 수치 표시
    for bar, val in zip(bars1, strat):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 2,
                 f'{val:+.0f}%', ha='center', va='bottom',
                 fontsize=7, color='white')

    # ── 2. 포트폴리오 누적 수익률 ──
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor('#16213e')

    # 공통 날짜 인덱스로 합산
    all_ps = []
    for r in results:
        ps_norm = r['ps'] / r['capital'] - 1
        all_ps.append(ps_norm)

    if all_ps:
        combined = pd.concat(all_ps, axis=1).ffill().fillna(0)
        port_series = combined.mean(axis=1) * 100

        ax2.plot(port_series.index, port_series.values,
                 color='#00ff88', lw=2.5, label=f'포트폴리오 전략 ({port_ret:+.1f}%)')
        ax2.axhline(0, color='#555', lw=1)
        ax2.fill_between(port_series.index, port_series.values, 0,
                         where=(port_series.values >= 0), alpha=0.15, color='#00ff88')
        ax2.fill_between(port_series.index, port_series.values, 0,
                         where=(port_series.values < 0),  alpha=0.15, color='#ff4444')

        ax2.set_title('포트폴리오 누적 수익률 (24종목 균등 배분)',
                      color='white', fontsize=13, fontweight='bold')
        ax2.set_ylabel('누적 수익률 (%)', color='white')
        ax2.legend(facecolor='#1a1a2e', labelcolor='white')
        ax2.tick_params(colors='white')
        for sp in ax2.spines.values(): sp.set_color('#444')
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f'{v:+.0f}%'))

    # ── 3. 승률 & MDD 산점도 ──
    ax3 = fig.add_subplot(gs[2])
    ax3.set_facecolor('#16213e')

    for r in results:
        color = '#00ff88' if r['ret'] > 0 else '#ff4444'
        size  = max(50, abs(r['ret']) * 3)
        ax3.scatter(r['win_rate'], r['mdd'], s=size, color=color, alpha=0.7)
        ax3.annotate(r['ticker'],
                     (r['win_rate'], r['mdd']),
                     textcoords='offset points', xytext=(5, 3),
                     fontsize=8, color='white')

    ax3.axvline(50, color='#FFD700', lw=1, linestyle='--', alpha=0.5, label='승률 50%')
    ax3.set_xlabel('승률 (%)', color='white')
    ax3.set_ylabel('MDD (%)', color='white')
    ax3.set_title('종목별 승률 vs 최대낙폭(MDD)  [원 크기 = 수익률 크기]',
                  color='white', fontsize=13, fontweight='bold')
    ax3.legend(facecolor='#1a1a2e', labelcolor='white')
    ax3.tick_params(colors='white')
    for sp in ax3.spines.values(): sp.set_color('#444')
    ax3.invert_yaxis()

    fig.suptitle(f'김학주 교수 추천종목 — 월봉 {MA_PERIOD}이평선 전략 시뮬레이션 (10년)',
                 color='white', fontsize=15, fontweight='bold', y=0.98)

    out = 'c:\\Users\\user\\Desktop\\주식정보\\포트폴리오_시뮬레이션.png'
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    print(f'\n  차트 저장: {out}')
    plt.close()


if __name__ == '__main__':
    run_portfolio()
