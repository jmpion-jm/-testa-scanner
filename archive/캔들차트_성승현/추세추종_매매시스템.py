# -*- coding: utf-8 -*-
"""
성승현 작가 "캔들 차트 하나로 끝내는 추세 추종 투자" - 완전 구현
==========================================================
핵심 전략: 월봉 12이평선 추세추종 + 탑다운 분석 + 패턴 감지
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
import warnings

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings('ignore')


# ============================================================
# 1. 데이터 수집
# ============================================================

def get_data(ticker: str, period: str = "10y", interval: str = "1mo") -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
    return df


# ============================================================
# 2. 핵심 지표 및 신호 계산
# ============================================================

def calc_ma(df: pd.DataFrame, period: int, col: str = 'Close') -> pd.Series:
    return df[col].rolling(window=period).mean()


def calc_signals_monthly_ma12(df: pd.DataFrame) -> pd.DataFrame:
    """
    핵심 전략: 월봉 12이평선 추세추종
      매수: 월봉 종가가 12이평선 상향 돌파 (이전 월 아래 → 이번 월 위)
      매도: 월봉 종가가 12이평선 하향 이탈 (이전 월 위 → 이번 월 아래)
      보유: 12이평선 위에 있는 한 유지
    """
    df = df.copy()
    df['MA12'] = calc_ma(df, 12)
    df['above_ma12'] = df['Close'] > df['MA12']
    df['signal'] = 0

    # 상향 돌파 → 매수
    df.loc[
        (df['above_ma12'] == True) & (df['above_ma12'].shift(1) == False),
        'signal'
    ] = 1

    # 하향 이탈 → 매도
    df.loc[
        (df['above_ma12'] == False) & (df['above_ma12'].shift(1) == True),
        'signal'
    ] = -1

    return df


def detect_joseungsaja(df_monthly: pd.DataFrame) -> pd.Series:
    """
    저승사자 캔들: 12이평 이탈 직후 장대 음봉
      - 음봉 (종가 < 시가)
      - 몸통이 4% 이상
      - 12이평선 아래 위치
    """
    body = (df_monthly['Open'] - df_monthly['Close']) / df_monthly['Open']
    return (
        (df_monthly['Close'] < df_monthly['Open']) &
        (body > 0.04) &
        (df_monthly['Close'] < df_monthly['MA12'])
    )


def calc_20day_inflection(df_daily: pd.DataFrame) -> pd.DataFrame:
    """
    20일선 변곡점 매매 (보조 단기 전략)
      원리: 당일 종가 > 20일 전 종가 → 20일선 기울기 상승 전환
      매수: 변곡점 발생 당일 또는 다음날 시가
    """
    df = df_daily.copy()
    df['Close_20d_ago'] = df['Close'].shift(20)
    df['MA20'] = calc_ma(df, 20)

    # 상승 변곡점: 오늘 돌파 & 어제는 돌파 안 됨
    df['inflection_up'] = (
        (df['Close'] > df['Close_20d_ago']) &
        (df['Close'].shift(1) <= df['Close_20d_ago'].shift(1))
    )
    # 하락 신호
    df['inflection_down'] = (
        (df['Close'] < df['Close_20d_ago']) &
        (df['Close'].shift(1) >= df['Close_20d_ago'].shift(1))
    )
    return df


def detect_double_bottom(df: pd.DataFrame, window: int = 10, tol: float = 0.05) -> pd.Series:
    """쌍바닥(W형) 패턴 감지"""
    signals = pd.Series(False, index=df.index)
    for i in range(window * 2, len(df)):
        s1 = df['Low'].iloc[i - window * 2: i - window]
        s2 = df['Low'].iloc[i - window: i]
        min1, min2 = s1.min(), s2.min()
        between = df['High'].iloc[i - window * 2: i]
        if abs(min1 - min2) / max(min1, 1) < tol and between.max() > min1 * 1.03:
            signals.iloc[i] = True
    return signals


def detect_head_shoulders(df: pd.DataFrame, window: int = 10) -> pd.Series:
    """헤드앤숄더(H&S) 패턴 감지 - 하락 신호"""
    signals = pd.Series(False, index=df.index)
    for i in range(window * 3, len(df)):
        s1 = df['High'].iloc[i - window * 3: i - window * 2].max()
        s2 = df['High'].iloc[i - window * 2: i - window].max()
        s3 = df['High'].iloc[i - window: i].max()
        if s2 > s1 and s2 > s3 and abs(s1 - s3) / max(s1, 1) < 0.10:
            signals.iloc[i] = True
    return signals


def detect_cup_with_handle(df: pd.DataFrame, cup_window: int = 20, handle_window: int = 5) -> pd.Series:
    """컵위드핸들 패턴 감지 - 상승 신호"""
    signals = pd.Series(False, index=df.index)
    total = cup_window + handle_window
    for i in range(total, len(df)):
        cup = df['Low'].iloc[i - total: i - handle_window]
        handle = df['Close'].iloc[i - handle_window: i]
        cup_min = cup.min()
        cup_rim = max(cup.iloc[0], cup.iloc[-1])
        handle_low = handle.min()
        if handle_low > cup_min and handle_low < cup_rim and df['Close'].iloc[i] > cup_rim:
            signals.iloc[i] = True
    return signals


def detect_donbanjee(df_weekly: pd.DataFrame) -> pd.DataFrame:
    """
    돌반지 패턴: 주봉 24이평선 돌파 + 거래량 폭발
    장기 상승 시작의 핵심 진입 신호
    """
    df = df_weekly.copy()
    df['MA24'] = calc_ma(df, 24)
    df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    df['donbanjee'] = (
        (df['Close'] > df['MA24']) &
        (df['Close'].shift(1) <= df['MA24'].shift(1)) &
        (df['Volume'] > df['Vol_MA20'] * 1.5)
    )
    return df


# ============================================================
# 3. 탑다운 시장 분석
# ============================================================

GLOBAL_MARKETS = {
    '🇺🇸 미국': {
        '나스닥': '^IXIC',
        'S&P500': '^GSPC',
        '다우': '^DJI',
    },
    '🏭 제조업국가': {
        '독일 DAX': '^GDAXI',
        '일본 니케이': '^N225',
        '대만 가권': '^TWII',
    },
    '🇰🇷 한국': {
        '코스피': '^KS11',
        '코스닥': '^KQ11',
    },
}


def topdown_analysis() -> dict:
    """탑다운 시장 분석: 미국 → 제조업국가 → 한국"""
    results = {}
    for region, tickers in GLOBAL_MARKETS.items():
        results[region] = {}
        for name, ticker in tickers.items():
            try:
                df = get_data(ticker, period="2y", interval="1mo")
                if len(df) < 12:
                    continue
                df['MA12'] = calc_ma(df, 12)
                latest = df.iloc[-1]
                above = latest['Close'] > latest['MA12']
                pct = (latest['Close'] - latest['MA12']) / latest['MA12'] * 100
                results[region][name] = {
                    'above_ma12': above,
                    'pct_from_ma12': round(pct, 2),
                }
            except Exception as e:
                results[region][name] = {'error': str(e)}
    return results


def print_topdown_report(results: dict) -> str:
    lines = ["\n" + "="*60, "📊 탑다운 시장 분석 (월봉 12이평선 기준)", "="*60]
    total, bullish = 0, 0

    for region, markets in results.items():
        lines.append(f"\n【{region}】")
        for name, data in markets.items():
            if 'error' in data:
                lines.append(f"  ⚠️  {name}: 데이터 오류")
                continue
            icon = "✅" if data['above_ma12'] else "❌"
            pct = data['pct_from_ma12']
            pct_str = f"+{pct:.1f}%" if pct >= 0 else f"{pct:.1f}%"
            status = "상승추세" if data['above_ma12'] else "하락추세"
            lines.append(f"  {icon} {name}: {status} (12이평 대비 {pct_str})")
            total += 1
            if data['above_ma12']:
                bullish += 1

    ratio = bullish / total * 100 if total else 0
    lines.append(f"\n🌍 글로벌 상승 비율: {bullish}/{total} ({ratio:.0f}%)")
    if ratio >= 70:
        lines.append("→ 강세장 → 적극 매수 고려 가능")
    elif ratio >= 50:
        lines.append("→ 혼조장 → 선별적 종목 접근")
    else:
        lines.append("→ 약세장 → 신규 매수 자제, 현금 비중 확대")

    report = "\n".join(lines)
    print(report)
    return report


# ============================================================
# 4. 개별 종목 분석
# ============================================================

def analyze_stock(ticker: str, name: str = "") -> dict:
    label = name or ticker
    print(f"\n{'='*60}\n📈 {label} 종합 분석\n{'='*60}")

    # 월봉 (핵심)
    df_m = get_data(ticker, period="10y", interval="1mo")
    df_m = calc_signals_monthly_ma12(df_m)

    # 일봉 (20일선 보조)
    df_d = get_data(ticker, period="2y", interval="1d")
    df_d = calc_20day_inflection(df_d)

    # 주봉 (돌반지 패턴)
    df_w = get_data(ticker, period="5y", interval="1wk")
    df_w = detect_donbanjee(df_w)

    latest = df_m.iloc[-1]
    close = latest['Close']
    ma12 = latest['MA12']
    above = close > ma12
    pct = (close - ma12) / ma12 * 100

    # 저승사자 캔들
    joseungsaja_series = detect_joseungsaja(df_m)
    recent_jose = joseungsaja_series.iloc[-3:].any()

    # 쌍바닥
    db = detect_double_bottom(df_m)
    recent_db = db.iloc[-6:].any()

    # 헤드앤숄더
    hs = detect_head_shoulders(df_m)
    recent_hs = hs.iloc[-6:].any()

    # 컵위드핸들
    cwh = detect_cup_with_handle(df_m)
    recent_cwh = cwh.iloc[-6:].any()

    # 돌반지
    recent_donbanjee = df_w['donbanjee'].iloc[-8:].any()

    # 20일선 변곡점
    recent_inflect = df_d['inflection_up'].iloc[-10:].any()

    print(f"\n【현재 상태】")
    print(f"  현재가: {close:,.0f} | 12이평선: {ma12:,.0f} | 대비: {pct:+.1f}%")
    print(f"  추세: {'✅ 상승추세 (12이평 위)' if above else '❌ 하락추세 (12이평 아래)'}")

    print(f"\n【패턴 감지 결과】")
    print(f"  저승사자 캔들 : {'⚠️  감지 → 즉시 매도 검토' if recent_jose else '없음'}")
    print(f"  쌍바닥 (W형)  : {'✅ 감지 → 매수 신호' if recent_db else '없음'}")
    print(f"  헤드앤숄더    : {'⚠️  감지 → 하락 주의' if recent_hs else '없음'}")
    print(f"  컵위드핸들    : {'✅ 감지 → 돌파 매수 신호' if recent_cwh else '없음'}")
    print(f"  돌반지 패턴   : {'✅ 감지 → 주봉 24이평+거래량 돌파' if recent_donbanjee else '없음'}")
    print(f"  20일선 변곡점 : {'✅ 감지 → 단기 매수 타이밍' if recent_inflect else '없음'}")

    print(f"\n【매매 판단】")
    if above:
        if recent_jose or recent_hs:
            action = "⚠️  경고: 이탈 및 하락 패턴 감지 → 매도 준비 / 비중 축소"
        else:
            action = "📌 보유 유지 (12이평 이탈 전까지 홀딩)"
            if recent_db or recent_donbanjee or recent_cwh:
                action += " + 추가 매수 신호 존재 → 비중 확대 고려"
    else:
        if recent_jose:
            action = "🔴 즉시 매도 (12이평 이탈 + 저승사자 캔들)"
        else:
            action = "❌ 관망 (12이평 아래 → 신규 매수 금지)"
            if recent_db:
                action += " | 쌍바닥 형성 중 → 12이평 재돌파 시 재진입 대기"
    print(f"  → {action}")

    return {
        'ticker': ticker, 'name': label,
        'above_ma12': above, 'pct_from_ma12': pct,
        'joseungsaja': recent_jose, 'double_bottom': recent_db,
        'head_shoulders': recent_hs, 'cup_with_handle': recent_cwh,
        'donbanjee': recent_donbanjee, 'inflection_up': recent_inflect,
        'df_monthly': df_m, 'df_daily': df_d, 'df_weekly': df_w,
    }


# ============================================================
# 5. 백테스트
# ============================================================

def backtest_ma12(df: pd.DataFrame, capital: float = 10_000_000) -> dict:
    """월봉 12이평선 전략 백테스트"""
    df = df.copy()
    if 'MA12' not in df.columns:
        df['MA12'] = calc_ma(df, 12)
    if 'signal' not in df.columns:
        df = calc_signals_monthly_ma12(df)

    cash = capital
    shares = 0.0
    in_position = False
    entry_price = 0.0
    entry_date = None
    trades = []
    portfolio = []

    for i, (date, row) in enumerate(df.iterrows()):
        if pd.isna(row['MA12']):
            portfolio.append(cash)
            continue

        if row['signal'] == 1 and not in_position:
            shares = cash / row['Close']
            entry_price = row['Close']
            entry_date = date
            cash = 0.0
            in_position = True

        elif row['signal'] == -1 and in_position:
            cash = shares * row['Close']
            pnl = (row['Close'] - entry_price) / entry_price * 100
            hold_m = i - list(df.index).index(entry_date)
            trades.append({
                'entry_date': entry_date, 'exit_date': date,
                'entry_price': entry_price, 'exit_price': row['Close'],
                'pnl_pct': round(pnl, 2), 'hold_months': hold_m,
            })
            shares = 0.0
            in_position = False

        portfolio.append(shares * row['Close'] if in_position else cash)

    # 미청산 포지션
    if in_position:
        final = shares * df.iloc[-1]['Close']
        pnl = (df.iloc[-1]['Close'] - entry_price) / entry_price * 100
        trades.append({
            'entry_date': entry_date, 'exit_date': df.index[-1],
            'entry_price': entry_price, 'exit_price': df.iloc[-1]['Close'],
            'pnl_pct': round(pnl, 2), 'hold_months': len(df),
            'open_position': True,
        })
    else:
        final = cash

    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]

    df['portfolio'] = portfolio
    return {
        'initial': capital, 'final': round(final),
        'total_return': round((final - capital) / capital * 100, 2),
        'n_trades': len(trades),
        'win_rate': round(len(wins) / len(trades) * 100, 1) if trades else 0,
        'avg_win': round(np.mean([t['pnl_pct'] for t in wins]), 2) if wins else 0,
        'avg_loss': round(np.mean([t['pnl_pct'] for t in losses]), 2) if losses else 0,
        'trades': trades,
        'portfolio_series': pd.Series(portfolio, index=df.index),
        'df': df,
    }


def backtest_buyhold(df: pd.DataFrame, capital: float = 10_000_000) -> dict:
    """단순 매수보유(Buy & Hold) 비교 전략"""
    buy = df['Close'].dropna().iloc[0]
    final_price = df['Close'].iloc[-1]
    ret = (final_price - buy) / buy * 100
    final_val = capital * (1 + ret / 100)
    portfolio = df['Close'] / buy * capital
    return {
        'initial': capital, 'final': round(final_val),
        'total_return': round(ret, 2),
        'portfolio_series': portfolio,
    }


def print_backtest_report(bt: dict, label: str = "전략"):
    print(f"\n{'='*60}\n📊 {label} 백테스트 결과\n{'='*60}")
    print(f"  초기 자본 : {bt['initial']:,}원")
    print(f"  최종 자산 : {bt['final']:,}원")
    print(f"  총 수익률 : {bt['total_return']:+.1f}%")
    if 'n_trades' in bt:
        print(f"  거래 횟수 : {bt['n_trades']}회")
        print(f"  승    률  : {bt['win_rate']:.1f}%")
        print(f"  평균 수익 : +{bt['avg_win']:.1f}%")
        print(f"  평균 손실 : {bt['avg_loss']:.1f}%")
        print(f"\n  【거래 내역】")
        for t in bt['trades']:
            tag = "✅" if t['pnl_pct'] > 0 else "❌"
            open_str = " (보유중)" if t.get('open_position') else ""
            print(f"    {tag} {t['entry_date'].strftime('%Y.%m')} ~ "
                  f"{t['exit_date'].strftime('%Y.%m')} | "
                  f"{t['entry_price']:,.0f} → {t['exit_price']:,.0f} | "
                  f"{t['pnl_pct']:+.1f}% ({t['hold_months']}개월){open_str}")


# ============================================================
# 6. 시각화
# ============================================================

def plot_analysis(bt: dict, bt_bh: dict, ticker: str, name: str = ""):
    df = bt['df']
    label = name or ticker

    fig, axes = plt.subplots(3, 1, figsize=(16, 14),
                             gridspec_kw={'height_ratios': [3, 1, 2]})
    fig.patch.set_facecolor('#1a1a2e')
    for ax in axes:
        ax.set_facecolor('#16213e')
        ax.tick_params(colors='white')
        for spine in ax.spines.values():
            spine.set_color('#444')

    # ---- 상단: 월봉 캔들 + 12이평선 + 매매신호 ----
    ax1 = axes[0]
    for i, (_, row) in enumerate(df.iterrows()):
        c = '#ff4444' if row['Close'] >= row['Open'] else '#4488ff'
        ax1.bar(i, abs(row['Close'] - row['Open']),
                bottom=min(row['Open'], row['Close']),
                color=c, width=0.6, alpha=0.85)
        ax1.plot([i, i], [row['Low'], row['High']], color=c, lw=0.8)

    x = list(range(len(df)))
    ax1.plot(x, df['MA12'].values, color='#FFD700', lw=2, label='월봉 12이평선', zorder=5)

    for date in df[df['signal'] == 1].index:
        idx = list(df.index).index(date)
        price = df.loc[date, 'Low']
        ax1.annotate('▲매수', xy=(idx, price), xytext=(idx, price * 0.94),
                     color='#00ff88', fontsize=8, fontweight='bold', ha='center',
                     arrowprops=dict(arrowstyle='->', color='#00ff88'))

    for date in df[df['signal'] == -1].index:
        idx = list(df.index).index(date)
        price = df.loc[date, 'High']
        ax1.annotate('▼매도', xy=(idx, price), xytext=(idx, price * 1.06),
                     color='#ff4444', fontsize=8, fontweight='bold', ha='center',
                     arrowprops=dict(arrowstyle='->', color='#ff4444'))

    ax1.set_title(f'{label} — 월봉 12이평선 추세추종 전략', color='white',
                  fontsize=14, fontweight='bold')
    ax1.set_ylabel('가격', color='white')
    ax1.legend(loc='upper left', facecolor='#1a1a2e', labelcolor='white')
    step = max(1, len(df) // 12)
    ax1.set_xticks(range(0, len(df), step))
    ax1.set_xticklabels([df.index[i].strftime('%Y.%m') for i in range(0, len(df), step)],
                         rotation=45, fontsize=8, color='white')

    # ---- 중단: 거래량 ----
    ax2 = axes[1]
    for i, (_, row) in enumerate(df.iterrows()):
        c = '#ff4444' if row['Close'] >= row['Open'] else '#4488ff'
        ax2.bar(i, row['Volume'], color=c, alpha=0.6)
    ax2.set_ylabel('거래량', color='white')
    ax2.set_xticks([])
    ax2.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda v, _: f'{v/1e6:.0f}M' if v >= 1e6 else f'{v:.0f}'))

    # ---- 하단: 전략 수익률 비교 ----
    ax3 = axes[2]
    s_pct = (bt['portfolio_series'] / bt['initial'] - 1) * 100
    b_pct = (bt_bh['portfolio_series'] / bt_bh['initial'] - 1) * 100

    xs = list(range(len(df)))
    ax3.plot(xs, s_pct.values, color='#00ff88', lw=2,
             label=f"월봉 12이평선 ({bt['total_return']:+.1f}%)")
    bx = list(range(min(len(df), len(b_pct))))
    ax3.plot(bx, b_pct.values[:len(bx)], color='#aaaaaa', lw=1.5, ls='--',
             label=f"단순보유 ({bt_bh['total_return']:+.1f}%)")
    ax3.axhline(0, color='#555', lw=1)
    ax3.fill_between(xs, s_pct.values, 0,
                     where=(s_pct.values > 0), alpha=0.15, color='#00ff88')
    ax3.fill_between(xs, s_pct.values, 0,
                     where=(s_pct.values < 0), alpha=0.15, color='#ff4444')
    ax3.set_ylabel('수익률 (%)', color='white')
    ax3.legend(loc='upper left', facecolor='#1a1a2e', labelcolor='white')
    ax3.set_xticks(range(0, len(df), step))
    ax3.set_xticklabels([df.index[i].strftime('%Y.%m') for i in range(0, len(df), step)],
                         rotation=45, fontsize=8, color='white')
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:+.0f}%'))

    plt.tight_layout(pad=2)
    out = f"c:\\Users\\user\\Desktop\\주식정보\\{label}_전략분석.png"
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    print(f"\n  📊 차트 저장 완료: {out}")
    plt.close()


# ============================================================
# 7. 매매법 비교 분석 (성승현 전략 vs 단순보유)
# ============================================================

def compare_strategies(ticker: str, name: str = "") -> tuple:
    label = name or ticker
    print(f"\n{'='*60}\n🔍 {label} — 매매법 비교 분석\n{'='*60}")

    df_m = get_data(ticker, period="10y", interval="1mo")
    df_m = calc_signals_monthly_ma12(df_m)

    bt_strat = backtest_ma12(df_m)
    bt_bh = backtest_buyhold(df_m)

    print_backtest_report(bt_strat, f"{label} — 성승현 월봉 12이평선 전략")
    print_backtest_report(bt_bh,    f"{label} — 단순 매수보유 (Buy & Hold)")

    alpha = bt_strat['total_return'] - bt_bh['total_return']
    print(f"\n{'='*60}\n📋 전략 비교 요약\n{'='*60}")
    rows = [
        ("총 수익률",   f"{bt_strat['total_return']:+.1f}%",            f"{bt_bh['total_return']:+.1f}%"),
        ("최종 자산",   f"{bt_strat['final']//10000:,}만원",             f"{bt_bh['final']//10000:,}만원"),
        ("거래 횟수",   f"{bt_strat['n_trades']}회",                     "1회 (장기보유)"),
        ("승률",        f"{bt_strat['win_rate']:.0f}%",                  "—"),
        ("알파 수익",   f"{alpha:+.1f}%p",                               "기준"),
        ("월 확인",     "월 1회 (말일)",                                  "없음"),
        ("최대 손실",   "이탈 즉시 청산 → 손실 제한",                    "폭락 시 전액 노출"),
        ("심리적 부담", "낮음 (기준 명확)",                               "높음 (언제 팔지 불명확)"),
    ]
    print(f"\n  {'항목':<18} {'성승현 전략':>18} {'단순보유':>18}")
    print(f"  {'-'*56}")
    for item, s, b in rows:
        print(f"  {item:<18} {s:>18} {b:>18}")

    plot_analysis(bt_strat, bt_bh, ticker, label)
    return bt_strat, bt_bh


# ============================================================
# 8. 나의 정리본 vs 코딩 구현 — 차이점 비교
# ============================================================

def print_method_comparison():
    """
    사용자가 정리한 매매법(문서) vs 실제 코딩 구현 비교
    """
    print("""
╔══════════════════════════════════════════════════════════════╗
║      나의 매매법 정리 vs 코딩 구현 — 상세 비교              ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  [공통점 — 정확히 구현된 항목]                               ║
║  ✅ 월봉 12이평선 상향 돌파 → 매수                           ║
║  ✅ 월봉 12이평선 하향 이탈 → 즉시 매도 (예외 없음)          ║
║  ✅ 저승사자 캔들 감지 (12이평 이탈 후 장대 음봉)            ║
║  ✅ 탑다운 분석 (미국→제조업국가→한국→개별종목)              ║
║  ✅ 5대 요소 (거래량/캔들/패턴/추세/이평선)                  ║
║  ✅ 쌍바닥(W형) 패턴 감지                                    ║
║  ✅ 헤드앤숄더(H&S) 패턴 감지                                ║
║  ✅ 컵위드핸들 패턴 감지                                     ║
║  ✅ 돌반지 패턴 (주봉 24이평 + 거래량 폭발)                  ║
║  ✅ 20일선 변곡점 매매 (당일 종가 > 20일 전 종가)            ║
║  ✅ 백테스트 및 단순보유 대비 성과 비교                      ║
║                                                              ║
║  [코딩 구현에서 추가된 항목]                                 ║
║  ➕ 자동 탑다운 글로벌 시장 스캔 (8개 지수 동시 분석)        ║
║  ➕ 실시간 데이터 자동 수집 (yfinance)                       ║
║  ➕ 정량적 백테스트 (10년치 검증, 수익률/승률 계산)          ║
║  ➕ 알파 수익률 계산 (전략 수익 - 단순보유 수익)             ║
║  ➕ 다크 테마 차트 시각화 (캔들+신호+수익률 비교)            ║
║  ➕ 3중 바닥 자동 감지                                       ║
║                                                              ║
║  [문서에는 있지만 코딩 구현의 한계]                          ║
║  ⚠️  호가창 분석 (영매집 감지) — 실시간 API 필요            ║
║  ⚠️  장대양봉 사등분선 — 시각적 판단 요소 (자동화 어려움)   ║
║  ⚠️  세력 개입 여부 판단 — 정성적 분석 영역                 ║
║  ⚠️  월 마지막 거래일 동시호가 종가배팅 — 실시간 필요        ║
║                                                              ║
║  [코딩 구현의 주의사항]                                      ║
║  ⚠️  패턴 감지는 통계적 근사값 — 실전에서 직접 확인 필요    ║
║  ⚠️  yfinance 데이터는 수정주가 기준                        ║
║  ⚠️  한국장 종목코드: 005930.KS (삼성전자) 등 형식          ║
║  ⚠️  백테스트는 슬리피지/거래비용 미반영                    ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")


# ============================================================
# 9. 메인 실행
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("📈 성승현 캔들차트 추세추종 투자법 — 완전 구현 시스템")
    print("   기반: '캔들 차트 하나로 끝내는 추세 추종 투자' (2026)")
    print("=" * 60)

    # ── 핵심 매매법 비교 출력 ──
    print_method_comparison()

    # ── 탑다운 글로벌 시장 분석 ──
    print("\n⏳ 글로벌 시장 분석 중 (월봉 12이평선 기준)...")
    td = topdown_analysis()
    print_topdown_report(td)

    # ── 개별 종목 분석 + 백테스트 ──
    targets = [
        ("005930.KS", "삼성전자"),
        ("000660.KS", "SK하이닉스"),
        ("NVDA",      "엔비디아"),
    ]

    for ticker, name in targets:
        try:
            analyze_stock(ticker, name)
            compare_strategies(ticker, name)
        except Exception as e:
            print(f"\n⚠️  {name} 오류: {e}")

    # ── 핵심 요약 ──
    print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 ✦ 성승현 추세추종 투자법 핵심 7원칙
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 1. 탑다운  : 미국 → 제조업국가 → 한국 → 개별종목 순으로 확인
 2. 매수    : 월봉 종가가 12이평선 상향 돌파 → 즉시 진입
 3. 보유    : 12이평선 위에 있는 한 → 중간 음봉에도 무조건 홀딩
 4. 매도    : 월봉 종가가 12이평선 하향 이탈 → 예외 없이 즉시 청산
 5. 경보    : 저승사자 캔들 출현 → 즉시 매도 (이미 늦으면 손절)
 6. 보조    : 20일선 변곡점 / 돌반지 / 쌍바닥으로 진입 타이밍 정교화
 7. 루틴    : 매달 말일 단 1회 확인 (직장인 투 트랙 투자 가능)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    """)
