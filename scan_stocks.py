# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

matplotlib.rcParams['font.family'] = 'Malgun Gothic'
matplotlib.rcParams['axes.unicode_minus'] = False

# ── 핵심 기준선 ──────────────────────────────────────────────
MA_PERIOD = 10   # 월봉 10이평선
# ─────────────────────────────────────────────────────────────

STOCKS = {
    # 추천종목
    'LHX':  ('L3해리스테크',       '추천'),
    'ASTS': ('AST스페이스모바일',  '추천'),
    'NVDA': ('엔비디아',           '추천'),
    'IONQ': ('아이온큐',           '추천'),
    # AI 인프라/반도체
    'AVGO': ('브로드컴',           'AI반도체'),
    'MRVL': ('마벨테크놀로지',     'AI반도체'),
    'AMD':  ('AMD',                'AI반도체'),
    'ALAB': ('아스테라랩스',       'AI반도체'),
    'NTAP': ('넷앱',               'AI반도체'),
    'VRT':  ('버티브홀딩스',       'AI반도체'),
    # 차세대 반도체 소재
    'ON':   ('온세미콘덕터',       '반도체소재'),
    'NVTS': ('나비타스세미',       '반도체소재'),
    # 피지컬AI/휴머노이드
    'APH':  ('암페놀',             '휴머노이드'),
    # 우주위성
    'TDY':  ('텔레다인테크',       '우주위성'),
    'COHR': ('코히런트',           '우주위성'),
    'LITE': ('루멘텀홀딩스',       '우주위성'),
    'GLW':  ('코닝',               '우주위성'),
    'AAOI': ('어플라이드옵토',     '우주위성'),
    'RTX':  ('RTX',                '우주위성'),
    'ATI':  ('ATI',                '우주위성/희토류'),
    'CRS':  ('카펜터테크',         '우주위성'),
    'LDOS': ('레이도스(랜드)',      '우주위성'),
    # 소형원자로
    'BWXT': ('BWX테크놀로지스',    '소형원자로'),
    'PLUG': ('플러그파워',         '소형원자로'),
}


# ──────────────────────────────────────────────────────────────
# 1. 데이터 수집 & 지표 계산
# ──────────────────────────────────────────────────────────────
def fetch(ticker: str, period='3y', interval='1mo') -> pd.DataFrame:
    t = yf.Ticker(ticker)
    df = t.history(period=period, interval=interval, auto_adjust=True)
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    return df[['Open','High','Low','Close','Volume']].dropna()


def calc_priority(pct: float, fresh: bool) -> int:
    """우선순위 점수 (낮을수록 높은 우선순위)"""
    if fresh:           return 1   # 이번달 신규 돌파
    if pct <= 5:        return 2   # 눌림목 (돌파 후 5% 이내)
    if pct <= 15:       return 3   # 추세 진행 중
    if pct <= 30:       return 4   # 상승 중반
    return 5                       # 고점권 (추격 위험)


def scan_all() -> list:
    rows = []
    for ticker, (name, sector) in STOCKS.items():
        try:
            df = fetch(ticker)
            if len(df) < MA_PERIOD + 2:
                rows.append(dict(r=9, ticker=ticker, name=name, sector=sector,
                                 above=None, pct=None, price=None, ma=None))
                continue

            df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
            latest, prev = df.iloc[-1], df.iloc[-2]

            close = float(latest['Close'])
            ma    = float(latest['MA'])
            pct   = (close - ma) / ma * 100
            above = close > ma
            fresh = bool(float(prev['Close']) < float(prev['MA']) and above)

            vol_avg = float(df['Volume'].iloc[-6:].mean()) or 1
            vol_r   = float(latest['Volume']) / vol_avg

            rank = calc_priority(pct, fresh) if above else 99

            rows.append(dict(
                r=rank, ticker=ticker, name=name, sector=sector,
                above=above, pct=round(pct,1), price=round(close,2),
                ma=round(ma,2), fresh=fresh, vol_r=round(vol_r,2),
            ))
        except Exception as e:
            rows.append(dict(r=9, ticker=ticker, name=name, sector=sector,
                             above=None, pct=None, price=None, ma=None, err=str(e)))

    rows.sort(key=lambda x: (x['r'], x.get('pct') or 999))
    return rows


# ──────────────────────────────────────────────────────────────
# 2. 콘솔 출력
# ──────────────────────────────────────────────────────────────
RANK_LABEL = {
    1: '★ 신규돌파  — 이번달 10이평 상향 돌파 → 즉시 진입 검토',
    2: '▲ 눌림목   — 10이평 대비 +5% 이내  → 최적 진입 타이밍',
    3: '● 추세중   — +5~15%            → 보유 홀딩 / 신규는 눌림 대기',
    4: '◎ 상승중   — +15~30%           → 신규 주의, 보유자 홀딩',
    5: '△ 고점권   — +30% 초과         → 신규 매수 금지 (추격 위험)',
}

def print_report(rows: list):
    above = [r for r in rows if r.get('above') == True]
    below = [r for r in rows if r.get('above') == False]
    err   = [r for r in rows if r.get('above') is None]

    W = 76
    print('=' * W)
    print(f'  김학주 교수 추천종목 — 월봉 {MA_PERIOD}이평선 우선순위 스캔  (2026.05)')
    print('=' * W)
    print(f'  총 {len(rows)}종목 | 매수 가능: {len(above)}개 | 매수 금지: {len(below)}개')

    prev_rank = None
    HDR = f'  {"티커":<6} {"종목명":<16} {"섹터":<12} {"현재가":>9} {"10이평":>9} {"대비":>8} {"거래량비":>7}'

    for r in above:
        if r['r'] != prev_rank:
            print(f'\n  [{RANK_LABEL[r["r"]]}]')
            print('  ' + '-' * (W-2))
            print(HDR)
            print('  ' + '-' * (W-2))
            prev_rank = r['r']
        tag = '★ ' if r.get('fresh') else '   '
        print(f'  {tag}{r["ticker"]:<4} {r["name"]:<16} {r["sector"]:<12}'
              f' {r["price"]:>9,.2f} {r["ma"]:>9,.2f} {r["pct"]:>+7.1f}%'
              f' {r.get("vol_r", 0):>6.1f}x')

    if below:
        print(f'\n  [매수 금지] 월봉 {MA_PERIOD}이평선 아래 — 신규 진입 절대 금지')
        print('  ' + '-' * (W-2))
        for r in below:
            print(f'  ❌ {r["ticker"]:<6} {r["name"]:<16} {r["sector"]:<12}'
                  f' 현재가 {r["price"]:>8,.2f} | 10이평 대비 {r["pct"]:>+.1f}%')

    if err:
        print(f'\n  [데이터 오류]')
        for r in err:
            print(f'  ⚠  {r["ticker"]} {r["name"]} — {r.get("err","")}')

    print()
    print('=' * W)


# ──────────────────────────────────────────────────────────────
# 3. 차트 시각화 — 우선순위 1·2그룹만
# ──────────────────────────────────────────────────────────────
def plot_top_stocks(rows: list):
    top = [r for r in rows if r.get('r') in (1, 2)]
    if not top:
        top = [r for r in rows if r.get('r') == 3][:4]
    if not top:
        return

    ncols = min(3, len(top))
    nrows = (len(top) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols,
                              figsize=(7 * ncols, 5 * nrows),
                              squeeze=False)
    fig.patch.set_facecolor('#1a1a2e')

    for idx, r in enumerate(top):
        ax = axes[idx // ncols][idx % ncols]
        ax.set_facecolor('#16213e')
        for sp in ax.spines.values():
            sp.set_color('#444')
        ax.tick_params(colors='white', labelsize=8)

        try:
            df = fetch(r['ticker'], period='3y')
            df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
        except:
            ax.set_title(f'{r["name"]} — 데이터 오류', color='white')
            continue

        x = range(len(df))
        for i, (_, row) in enumerate(df.iterrows()):
            color = '#ff4444' if row['Close'] >= row['Open'] else '#4488ff'
            ax.bar(i, abs(row['Close'] - row['Open']),
                   bottom=min(row['Open'], row['Close']),
                   color=color, width=0.6, alpha=0.85)
            ax.plot([i, i], [row['Low'], row['High']], color=color, lw=0.7)

        ax.plot(x, df['MA'].values, color='#FFD700', lw=2,
                label=f'월봉 {MA_PERIOD}이평')

        # 매수/매도 신호 표시
        df['above'] = df['Close'] > df['MA']
        buy  = df[(df['above'] == True)  & (df['above'].shift(1) == False)]
        sell = df[(df['above'] == False) & (df['above'].shift(1) == True)]

        for date in buy.index:
            i = list(df.index).index(date)
            ax.axvline(i, color='#00ff88', lw=1.2, alpha=0.7, linestyle='--')
        for date in sell.index:
            i = list(df.index).index(date)
            ax.axvline(i, color='#ff4444', lw=1.2, alpha=0.7, linestyle='--')

        step = max(1, len(df) // 8)
        ax.set_xticks(range(0, len(df), step))
        ax.set_xticklabels(
            [df.index[i].strftime('%y.%m') for i in range(0, len(df), step)],
            rotation=45, fontsize=7, color='white')

        rank_tag = '★신규돌파' if r.get('fresh') else f'+{r["pct"]:.1f}%'
        ax.set_title(f'{r["name"]} ({r["ticker"]})  [{rank_tag}]',
                     color='#FFD700' if r.get('fresh') else 'white',
                     fontsize=10, fontweight='bold')
        ax.legend(loc='upper left', facecolor='#1a1a2e',
                  labelcolor='white', fontsize=8)

    # 빈 축 숨기기
    for idx in range(len(top), nrows * ncols):
        axes[idx // ncols][idx % ncols].set_visible(False)

    fig.suptitle(f'김학주 교수 추천 — 우선매수 후보 (월봉 {MA_PERIOD}이평 기준)',
                 color='white', fontsize=13, fontweight='bold', y=1.01)
    plt.tight_layout(pad=2)

    out = 'c:\\Users\\user\\Desktop\\주식정보\\우선매수후보_차트.png'
    plt.savefig(out, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    print(f'  차트 저장: {out}')
    plt.close()


# ──────────────────────────────────────────────────────────────
# 4. 백테스트 — 상위 종목 검증
# ──────────────────────────────────────────────────────────────
def backtest_ma(ticker: str, capital=10_000_000) -> dict:
    try:
        df = fetch(ticker, period='10y')
        df['MA'] = df['Close'].rolling(MA_PERIOD).mean()
        df['above'] = df['Close'] > df['MA']
        df['signal'] = 0
        df.loc[(df['above'] == True)  & (df['above'].shift(1) == False), 'signal'] =  1
        df.loc[(df['above'] == False) & (df['above'].shift(1) == True),  'signal'] = -1

        cash, shares = capital, 0.0
        in_pos = False
        entry_p, entry_d = 0.0, None
        trades, portfolio = [], []

        for i, (date, row) in enumerate(df.iterrows()):
            if pd.isna(row['MA']):
                portfolio.append(cash)
                continue
            if row['signal'] == 1 and not in_pos:
                shares  = cash / row['Close']
                entry_p = float(row['Close'])
                entry_d = date
                cash    = 0.0
                in_pos  = True
            elif row['signal'] == -1 and in_pos:
                cash   = shares * float(row['Close'])
                pnl    = (float(row['Close']) - entry_p) / entry_p * 100
                hm     = i - list(df.index).index(entry_d)
                trades.append(dict(entry_date=entry_d, exit_date=date,
                                   ep=entry_p, xp=float(row['Close']),
                                   pnl=round(pnl,1), hm=hm))
                shares = 0.0
                in_pos = False
            portfolio.append(shares * float(row['Close']) if in_pos else cash)

        if in_pos:
            final = shares * float(df.iloc[-1]['Close'])
            pnl   = (float(df.iloc[-1]['Close']) - entry_p) / entry_p * 100
            trades.append(dict(entry_date=entry_d,
                               exit_date=df.index[-1],
                               ep=entry_p, xp=float(df.iloc[-1]['Close']),
                               pnl=round(pnl,1), hm=len(df),
                               open=True))
        else:
            final = cash

        wins = [t for t in trades if t['pnl'] > 0]
        return dict(
            final=round(final),
            ret=round((final - capital) / capital * 100, 1),
            n=len(trades),
            win_r=round(len(wins)/len(trades)*100,1) if trades else 0,
            avg_win=round(np.mean([t['pnl'] for t in wins]),1) if wins else 0,
            trades=trades,
            ps=pd.Series(portfolio, index=df.index[:len(portfolio)]),
        )
    except:
        return {}


# ──────────────────────────────────────────────────────────────
# 5. 메인
# ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('\n  스캔 중...')
    rows = scan_all()
    print_report(rows)

    # 우선순위 1·2 그룹 차트 출력
    print('\n  차트 생성 중...')
    plot_top_stocks(rows)

    # 추천종목 4개 백테스트
    print(f'\n  {"="*76}')
    print(f'  [추천종목 백테스트] 월봉 {MA_PERIOD}이평선 전략 (10년)')
    print(f'  {"="*76}')
    for ticker in ['LHX', 'ASTS', 'NVDA', 'IONQ']:
        name = STOCKS[ticker][0]
        bt = backtest_ma(ticker)
        if not bt:
            print(f'  {ticker} {name}: 백테스트 실패')
            continue
        print(f'\n  {name} ({ticker})')
        print(f'    총수익률: {bt["ret"]:+.1f}%  |  거래: {bt["n"]}회  |  승률: {bt["win_r"]}%  |  평균수익: +{bt["avg_win"]}%')
        for t in bt['trades']:
            tag  = '✅' if t['pnl'] > 0 else '❌'
            open_str = ' (보유중)' if t.get('open') else ''
            print(f'    {tag} {t["entry_date"].strftime("%Y.%m")}~{t["exit_date"].strftime("%Y.%m")}'
                  f'  {t["ep"]:>8,.1f}→{t["xp"]:>8,.1f}  {t["pnl"]:>+6.1f}%  ({t["hm"]}개월){open_str}')

    print(f'\n  {"="*76}')
    print(f'  우선매수 기준 요약')
    print(f'  {"="*76}')
    top1 = [r for r in rows if r.get('r') == 1]
    top2 = [r for r in rows if r.get('r') == 2]
    print(f'  ★ 즉시 진입 후보 ({len(top1)}개): ' +
          ', '.join([f'{r["ticker"]}({r["name"]})' for r in top1]) if top1 else '  ★ 신규돌파 없음')
    print(f'  ▲ 눌림목 후보   ({len(top2)}개): ' +
          ', '.join([f'{r["ticker"]}({r["name"]}) {r["pct"]:+.1f}%' for r in top2]) if top2 else '  ▲ 눌림목 없음')
