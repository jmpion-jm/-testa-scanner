# -*- coding: utf-8 -*-
"""
복합 하이브리드 전략 시뮬레이션
  [월봉 숲] 10MA 전환/추금/매도 신호
  [일봉 나무] 200MA 생명선 + 20MA 눌림목 진입 타이밍
비교: 단순 월봉 10MA vs 복합 하이브리드 vs B&H
"""
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

for font in ['Malgun Gothic', 'NanumGothic', 'AppleGothic']:
    if any(font.replace(' ','').lower() in f.replace(' ','').lower() for f in fm.findSystemFonts()):
        plt.rcParams['font.family'] = font
        break
plt.rcParams['axes.unicode_minus'] = False

# ── 종목 ────────────────────────────────────────────────────
STOCKS = {
    'LHX':  'L3해리스',    'ASTS': 'AST스페이스', 'NVDA': '엔비디아',   'IONQ': '아이온큐',
    'AVGO': '브로드컴',    'MRVL': '마벨테크',    'AMD':  'AMD',        'ALAB': '아스테라랩스',
    'NTAP': '넷앱',        'VRT':  '버티브',       'ON':   '온세미',     'NVTS': '나비타스',
    'APH':  '암페놀',      'TDY':  '텔레다인',     'COHR': '코히런트',   'LITE': '루멘텀',
    'GLW':  '코닝',        'AAOI': '어플라이드옵토','RTX':  'RTX',        'ATI':  'ATI',
    'CRS':  '카펜터테크',  'LDOS': '레이도스',     'BWXT': 'BWX테크',    'PLUG': '플러그파워',
}

INITIAL    = 10_000_000
COMMISSION = 0.001

# ── 하이브리드 파라미터 (config.yaml 동일) ──────────────────
BUY_D0_MAX        = 3.0    # 전환 매수 허용 최대 이격도 (%)
CHASE_BLOCK_D0    = 7.0    # 추격 금지 이격도 (%)
SELL_D0_MIN       = -2.0   # 매도 이격도 기준 (%)
MA20_ENTRY_RANGE  = 2.0    # 일봉 20MA 진입 허용 범위 (±%)
MA20_WATCH_LOW    = -3.0   # 일봉 20MA 주시 하한 (%)


# ── 데이터 수집 ─────────────────────────────────────────────
def fetch(ticker: str, interval: str, period: str = '10y') -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open','High','Low','Close','Volume']].dropna()


# ── 월봉 신호 판단 ───────────────────────────────────────────
def monthly_signal(close_s: pd.Series, ma10_s: pd.Series, idx: int) -> str:
    """해당 월봉 인덱스에서 신호 반환: 매수/추금/매도/관망"""
    if idx < 1:
        return '관망'
    c0  = float(close_s.iloc[idx])
    c1  = float(close_s.iloc[idx - 1])
    m0  = float(ma10_s.iloc[idx])
    m1  = float(ma10_s.iloc[idx - 1])
    if pd.isna(m0) or pd.isna(m1) or m0 == 0:
        return '관망'

    d0 = (c0 - m0) / m0 * 100

    # 매도 (휩쏘 방지 포함)
    if c0 < m0:
        return '매도' if d0 <= SELL_D0_MIN else '관망경고'

    # 전환 매수
    turn_up = (c0 > m0) and (c1 <= m1)
    if turn_up:
        return '매수' if d0 <= BUY_D0_MAX else '추금'

    # 추격 금지
    if d0 >= CHASE_BLOCK_D0:
        return '추금'

    return '관망'


# ── 일봉 진입 조건 체크 ──────────────────────────────────────
def daily_entry_check(price: float, ma20: float, ma200: float) -> str:
    """일봉 기준 진입 상태 반환"""
    if pd.isna(ma20) or pd.isna(ma200):
        return '데이터부족'
    if price < ma200:
        return '200선붕괴'
    dist20 = (price - ma20) / ma20 * 100
    if abs(dist20) <= MA20_ENTRY_RANGE and price >= ma20:
        return '💎눌림목'
    if MA20_WATCH_LOW <= dist20 < 0:
        return '👀주시'
    if dist20 > MA20_ENTRY_RANGE:
        return '🔵상승중'
    return '⚪대기'


# ── 단순 월봉 10MA 백테스트 ──────────────────────────────────
def backtest_simple(df_m: pd.DataFrame) -> dict:
    df = df_m.copy()
    df['MA10'] = df['Close'].rolling(10).mean()
    df.dropna(inplace=True)

    cash, shares = INITIAL, 0.0
    in_market = False
    trades, equity = [], []

    for i in range(1, len(df)):
        prev = df.iloc[i-1]
        curr = df.iloc[i]
        price = float(curr['Close'])
        ma    = float(curr['MA10'])
        pma   = float(prev['MA10'])
        pc    = float(prev['Close'])

        if not in_market and pc < pma and price > ma:
            shares = cash / (price * (1 + COMMISSION))
            cash = 0.0
            in_market = True
            trades.append(('BUY', curr.name, price))
        elif in_market and pc > pma and price < ma:
            cash = shares * price * (1 - COMMISSION)
            shares = 0.0
            in_market = False
            trades.append(('SELL', curr.name, price))

        equity.append({'date': curr.name, 'equity': cash + shares * price})

    if in_market:
        cash = shares * float(df.iloc[-1]['Close']) * (1 - COMMISSION)

    return _calc_stats(equity, cash, trades, df)


# ── 복합 하이브리드 백테스트 ────────────────────────────────
def backtest_hybrid(df_m: pd.DataFrame, df_d: pd.DataFrame) -> dict:
    """
    월봉으로 신호 결정, 일봉으로 진입 타이밍 최적화
    진입: 월봉 매수 신호 나온 달 이후 → 일봉에서 20MA 눌림목 첫 날 진입
    매도: 월봉 이격도 ≤ -2% 확정 다음 거래일 / 일봉 200MA 이탈 시
    """
    df_m = df_m.copy()
    df_m['MA10'] = df_m['Close'].rolling(10).mean()

    df_d = df_d.copy()
    df_d['MA20']  = df_d['Close'].rolling(20).mean()
    df_d['MA200'] = df_d['Close'].rolling(200).mean()

    close_s = df_m['Close']
    ma10_s  = df_m['MA10']

    # 월봉 신호를 (year, month) → 신호 딕셔너리로 변환
    monthly_signal_map = {}
    for i in range(10, len(df_m)):
        sig = monthly_signal(close_s, ma10_s, i)
        mdate = df_m.index[i]
        monthly_signal_map[(mdate.year, mdate.month)] = sig

    cash, shares = INITIAL, 0.0
    in_market = False
    buy_window = False   # 매수 대기 창 (월봉 전환 후 일봉 타이밍 기다리는 중)
    trades, equity_list = [], []

    for dt, row in df_d.iterrows():
        price = float(row['Close'])
        ma20  = float(row['MA20'])  if not pd.isna(row['MA20'])  else float('nan')
        ma200 = float(row['MA200']) if not pd.isna(row['MA200']) else float('nan')

        # 직전 달 월봉 신호 조회 (종가 확정된 월 기준)
        prev_y = dt.year if dt.month > 1 else dt.year - 1
        prev_m = dt.month - 1 if dt.month > 1 else 12
        sig = monthly_signal_map.get((prev_y, prev_m))

        # ── 매도 조건 ──
        if in_market:
            # 월봉 매도 (이격도 ≤ -2%)
            if sig == '매도':
                cash = shares * price * (1 - COMMISSION)
                shares = 0.0
                in_market = False
                buy_window = False
                trades.append(('SELL_월봉', dt, price))

            # 일봉 200MA 붕괴
            elif not pd.isna(ma200) and price < ma200:
                cash = shares * price * (1 - COMMISSION)
                shares = 0.0
                in_market = False
                buy_window = False
                trades.append(('SELL_200붕괴', dt, price))

        # ── 매수 창 업데이트 ──
        if not in_market:
            if sig == '매수':
                buy_window = True          # 전환 신호 → 창 열기
            elif sig in ('매도', '추금', '관망경고'):
                buy_window = False         # 창 닫기

            # ── 일봉 진입 타이밍 ──
            if buy_window and not pd.isna(ma200) and price > ma200:
                entry_status = daily_entry_check(price, ma20, ma200)
                if entry_status in ('💎눌림목', '👀주시'):
                    shares = cash / (price * (1 + COMMISSION))
                    cash = 0.0
                    in_market = True
                    buy_window = False
                    trades.append(('BUY', dt, price))

        equity_list.append({'date': dt, 'equity': cash + shares * price})

    if in_market:
        cash = shares * float(df_d.iloc[-1]['Close']) * (1 - COMMISSION)

    return _calc_stats(equity_list, cash, trades, df_d, is_daily=True)


def _calc_stats(equity_list: list, final_cash: float, trades: list, df, is_daily=False) -> dict:
    if not equity_list:
        return None
    eq_df = pd.DataFrame(equity_list).set_index('date')
    total_return = (final_cash - INITIAL) / INITIAL * 100
    eq = eq_df['equity']
    mdd = float(((eq - eq.cummax()) / eq.cummax() * 100).min())

    p0 = float(df.iloc[0]['Close'])
    p1 = float(df.iloc[-1]['Close'])
    bh = (p1 - p0) / p0 * 100

    buys  = [t for t in trades if t[0] == 'BUY']
    sells = [t for t in trades if 'SELL' in t[0]]
    wins  = sum(1 for i in range(min(len(buys), len(sells))) if sells[i][2] > buys[i][2])
    n     = min(len(buys), len(sells))

    return dict(
        total_return=total_return, bh_return=bh, mdd=mdd,
        n_trades=len(buys),
        win_rate=wins/n*100 if n else 0,
        equity_df=eq_df,
    )


# ── 메인 ────────────────────────────────────────────────────
def main():
    print('복합 하이브리드 전략 시뮬레이션 (10년)')
    print('단순 월봉 10MA  vs  복합 하이브리드  vs  B&H')
    print('=' * 72)

    results = {}

    for ticker, name in STOCKS.items():
        try:
            print(f'  {name} ({ticker}) ...', end=' ', flush=True)
            df_m = fetch(ticker, '1mo', '10y')
            df_d = fetch(ticker, '1d',  '10y')

            if len(df_m) < 12 or len(df_d) < 200:
                print('데이터 부족')
                continue

            r_simple = backtest_simple(df_m)
            r_hybrid = backtest_hybrid(df_m, df_d)

            if r_simple is None or r_hybrid is None:
                print('계산 실패')
                continue

            results[ticker] = {'name': name, 'simple': r_simple, 'hybrid': r_hybrid}

            diff = r_hybrid['total_return'] - r_simple['total_return']
            winner = '하이브리드↑' if diff > 0 else '단순MA↑' if diff < -1 else '≈동일'
            print(f"단순{r_simple['total_return']:+.0f}%  하이브리드{r_hybrid['total_return']:+.0f}%  "
                  f"B&H{r_simple['bh_return']:+.0f}%  [{winner}]")
        except Exception as e:
            print(f'오류: {e}')

    if not results:
        print('결과 없음')
        return

    # ── 결과 테이블 ──
    print()
    print('=' * 80)
    print(f"{'종목':<14} {'단순10MA':>9} {'하이브리드':>10} {'B&H':>8} {'단순MDD':>8} {'복합MDD':>8} {'승자'}")
    print('-' * 80)

    s_beats_bh, h_beats_bh, h_beats_s = 0, 0, 0
    for ticker, r in results.items():
        s = r['simple']
        h = r['hybrid']
        winner = '하이브' if h['total_return'] > s['total_return'] + 1 else \
                 '단순'  if s['total_return'] > h['total_return'] + 1 else '동일'
        print(f"{r['name']:<10}({ticker:<4}) "
              f"{s['total_return']:>+8.0f}%  {h['total_return']:>+8.0f}%  "
              f"{s['bh_return']:>+7.0f}%  "
              f"{s['mdd']:>7.0f}%  {h['mdd']:>7.0f}%  {winner}")
        if s['total_return'] > s['bh_return']: s_beats_bh += 1
        if h['total_return'] > s['bh_return']: h_beats_bh += 1
        if h['total_return'] > s['total_return']: h_beats_s += 1

    print('=' * 80)
    n = len(results)
    s_avg = np.mean([r['simple']['total_return'] for r in results.values()])
    h_avg = np.mean([r['hybrid']['total_return'] for r in results.values()])
    b_avg = np.mean([r['simple']['bh_return'] for r in results.values()])
    s_mdd = np.mean([r['simple']['mdd'] for r in results.values()])
    h_mdd = np.mean([r['hybrid']['mdd'] for r in results.values()])
    print(f"{'평균':<14} {s_avg:>+8.0f}%  {h_avg:>+8.0f}%  {b_avg:>+7.0f}%  {s_mdd:>7.0f}%  {h_mdd:>7.0f}%")
    print()
    print(f'[요약] 총 {n}개 종목')
    print(f'  B&H 초과: 단순 10MA {s_beats_bh}개 / 하이브리드 {h_beats_bh}개')
    print(f'  하이브리드가 단순MA 초과: {h_beats_s}개 / {n}개')
    print(f'  평균 MDD: 단순 {s_mdd:.1f}%  →  하이브리드 {h_mdd:.1f}%')

    # ── 차트 ──
    n_stocks = min(len(results), 12)
    sample = list(results.items())[:n_stocks]
    cols = 3
    rows_c = (n_stocks + cols - 1) // cols
    fig, axes = plt.subplots(rows_c, cols, figsize=(18, rows_c * 3.8))
    axes_flat = axes.flatten() if n_stocks > 1 else [axes]

    for idx, (ticker, r) in enumerate(sample):
        ax = axes_flat[idx]
        s_eq = r['simple']['equity_df']['equity']
        h_eq = r['hybrid']['equity_df']['equity']
        # 날짜 범위 맞추기
        common = s_eq.index.intersection(h_eq.index)
        if len(common) == 0:
            axes_flat[idx].set_visible(False)
            continue
        ax.plot(h_eq.index, h_eq.values, color='steelblue',  linewidth=1.8, label='하이브리드')
        ax.plot(s_eq.index, s_eq.values, color='darkorange',  linewidth=1.2, linestyle='--', label='단순10MA')
        ax.axhline(INITIAL, color='black', linewidth=0.5, linestyle=':')

        s, h = r['simple'], r['hybrid']
        winner = '↑하이브' if h['total_return'] > s['total_return'] + 1 else \
                 '↑단순'  if s['total_return'] > h['total_return'] + 1 else '≈동일'
        title = (f"{r['name']} ({ticker})\n"
                 f"단순{s['total_return']:+.0f}%  복합{h['total_return']:+.0f}%  [{winner}]")
        ax.set_title(title, fontsize=8.5)
        ax.legend(fontsize=7)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x/1e6:.1f}M'))
        ax.grid(True, alpha=0.25)
        ax.tick_params(labelsize=7)

    for idx in range(n_stocks, len(axes_flat)):
        axes_flat[idx].set_visible(False)

    fig.suptitle('단순 월봉 10MA  vs  복합 하이브리드 (월봉숲 + 일봉나무)', fontsize=12, fontweight='bold')
    plt.tight_layout(rect=[0, 0, 1, 0.96])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '하이브리드_시뮬레이션.png')
    plt.savefig(out, dpi=150, bbox_inches='tight')
    plt.close()
    print(f'\n차트 저장: {out}')

    # ── 전략 해석 ──
    print()
    print('[복합 하이브리드 전략 해석]')
    print('  - 월봉 전환 진입(D0≤3%) + 일봉 20MA 눌림목 조합으로 단가 개선')
    print('  - 휩쏘 방지(D0>-2% 관망경고)로 잦은 손절 차단')
    print('  - 추격금지(D0≥7%)로 고점 매수 차단')
    print('  - 일봉 200MA 이탈 시 즉시 청산으로 대형 손실 방어')
    print()
    if h_avg > s_avg:
        print(f'  ★ 결론: 복합 하이브리드가 단순 10MA 대비 평균 {h_avg-s_avg:+.1f}%p 우수')
    else:
        print(f'  ★ 결론: 단순 10MA가 평균 {s_avg-h_avg:+.1f}%p 우수 (일봉 타이밍 대기 손실)')


if __name__ == '__main__':
    main()
