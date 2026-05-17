"""
optimize_strategy.py v2
────────────────────────────────────────────────────────────
문제 발견:
  1. 기간 불통일 — NVDA 27년, AMD 41년, CRWD 7년 혼재
  2. NVDA가 평균을 97,000%로 왜곡 (549,845% 독주)
  3. A안 43% 이유: 진입 조건 너무 엄격 (전환+D0≤3% 만)

이번 시뮬레이션:
  ✅ 공통 기간 2015-01 ~ 2025-12 (10년) 고정
  ✅ 종목별 결과 개별 표시 + 평균
  ✅ 5가지 전략 비교 + 최적 파라미터 서치
────────────────────────────────────────────────────────────
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product

STOCKS = {
    "NVDA": "엔비디아",
    "AVGO": "브로드컴",
    "AMD":  "AMD",
    "MRVL": "마벨테크",
    "CRWD": "크라우드스트라이크",
    "ASTS": "AST스페이스모바일",
}

START = "2015-01-01"
END   = "2025-12-31"

# ────────────────────────────────────────────
# 데이터 수집 (10년 고정)
# ────────────────────────────────────────────
def fetch_monthly(ticker: str) -> pd.Series:
    tk  = yf.Ticker(ticker)
    df  = tk.history(start=START, end=END, interval="1mo")
    if df.empty:
        return pd.Series(dtype=float)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df["Close"].dropna()


# ────────────────────────────────────────────
# 백테스트 엔진
# ────────────────────────────────────────────
def backtest(
    close: pd.Series,
    sell_d0_min: float,
    buy_turn_d0_max: float,
    chase_block_d0_pct: float,
    chase_block_months: int,
    mode: str = "a안",   # "성승현" | "성승현+휩쏘" | "a안"
) -> dict:
    ma10 = close.rolling(10, min_periods=10).mean()

    cash, shares = 1.0, 0.0
    in_pos = False
    entry_price = 0.0
    trades: list[float] = []
    port: list[float] = []

    for i in range(len(close)):
        c = float(close.iloc[i])
        m = ma10.iloc[i]
        if pd.isna(m):
            port.append(cash + shares * c)
            continue
        m = float(m)
        d0 = (c - m) / m * 100

        turn_up = False
        if i > 0 and not pd.isna(ma10.iloc[i - 1]):
            turn_up = (c > m) and (float(close.iloc[i - 1]) <= float(ma10.iloc[i - 1]))

        cons_up = 0
        for j in range(i, 0, -1):
            if float(close.iloc[j]) > float(close.iloc[j - 1]):
                cons_up += 1
            else:
                break

        # ── 신호 결정 ──────────────────────
        if mode == "성승현":
            # 순수: 위=매수/보유, 아래=즉시매도
            signal = "매수보유" if c > m else "매도"

        elif mode == "성승현+휩쏘":
            # 성승현 + 휩쏘방지: 아래여도 -2% 이내면 관망
            if c > m:
                signal = "매수보유"
            elif d0 <= sell_d0_min:
                signal = "매도"
            else:
                signal = "관망"

        else:  # A안
            if c < m:
                signal = "매도" if d0 <= sell_d0_min else "관망"
            elif turn_up and d0 <= buy_turn_d0_max:
                signal = "매수"
            elif c >= m and (
                d0 >= chase_block_d0_pct
                or cons_up >= chase_block_months
                or (turn_up and d0 > buy_turn_d0_max)
            ):
                signal = "추금"
            else:
                signal = "관망"

        # ── 포지션 실행 ────────────────────
        if not in_pos:
            if signal in ("매수보유", "매수"):
                in_pos = True
                entry_price = c
                shares = cash / c
                cash = 0.0
        else:
            if signal == "매도":
                in_pos = False
                pnl = (c - entry_price) / entry_price * 100
                trades.append(pnl)
                cash = shares * c
                shares = 0.0

        port.append(cash + shares * c)

    # 마감
    if in_pos and port:
        c = float(close.iloc[-1])
        pnl = (c - entry_price) / entry_price * 100
        trades.append(pnl)
        port[-1] = shares * c

    ps  = pd.Series(port)
    tot = (ps.iloc[-1] - 1.0) * 100
    rmx = ps.expanding().max()
    mdd = ((ps - rmx) / rmx * 100).min()
    n   = len(trades)
    wr  = sum(1 for t in trades if t > 0) / n * 100 if n else 0
    eff = tot / abs(mdd) if mdd != 0 else 0

    return {"ret": tot, "mdd": mdd, "n": n, "wr": wr, "eff": eff}


def bnh(close: pd.Series) -> dict:
    tot = (float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100
    ps  = close / float(close.iloc[0])
    rmx = ps.expanding().max()
    mdd = ((ps - rmx) / rmx * 100).min()
    eff = tot / abs(mdd) if mdd != 0 else 0
    return {"ret": tot, "mdd": mdd, "n": 1, "wr": 100, "eff": eff}


# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────
def main():
    print(f"📅 시뮬레이션 기간: {START} ~ {END} (10년 고정)\n")
    print("데이터 수집 중...")
    data: dict[str, pd.Series] = {}
    for ticker, name in STOCKS.items():
        s = fetch_monthly(ticker)
        if len(s) >= 15:
            data[ticker] = s
            print(f"  ✅ {name}({ticker}): {len(s)}개월")
        else:
            print(f"  ⚠️ {name}({ticker}): 데이터 부족 ({len(s)}개)")

    # ── 5가지 전략 비교 ────────────────────
    strategies = [
        ("B&H",          None),
        ("순수 성승현",    "성승현"),
        ("성승현+휩쏘방지", "성승현+휩쏘"),
        ("A안 현재설정",   "a안_현재"),
        ("A안 최적(예상)", "a안_최적"),
    ]

    print("\n" + "═" * 85)
    print(f"【 10년 기간 고정 — 5가지 전략 종목별 비교 】")
    print("═" * 85)

    # 헤더
    hdr = f"{'종목':<12}"
    for s_name, _ in strategies:
        hdr += f" {s_name[:7]:>10}"
    print(hdr + "  (수익률)")
    print("-" * 85)

    summary: dict[str, list] = {s[0]: [] for s in strategies}

    for ticker, close in data.items():
        name = STOCKS[ticker]
        row  = f"{name:<12}"

        b = bnh(close)
        summary["B&H"].append(b["ret"])
        row += f" {b['ret']:>9.0f}%"

        ss = backtest(close, -2, 100, 100, 999, mode="성승현")
        summary["순수 성승현"].append(ss["ret"])
        row += f" {ss['ret']:>9.0f}%"

        sw = backtest(close, -2, 100, 100, 999, mode="성승현+휩쏘")
        summary["성승현+휩쏘방지"].append(sw["ret"])
        row += f" {sw['ret']:>9.0f}%"

        ac = backtest(close, -2, 3, 7, 1, mode="a안")
        summary["A안 현재설정"].append(ac["ret"])
        row += f" {ac['ret']:>9.0f}%"

        ao = backtest(close, -3, 7, 15, 3, mode="a안")
        summary["A안 최적(예상)"].append(ao["ret"])
        row += f" {ao['ret']:>9.0f}%"

        print(row)

    print("-" * 85)
    avg_row = f"{'평균':12}"
    for s_name, _ in strategies:
        avg_row += f" {np.mean(summary[s_name]):>9.0f}%"
    print(avg_row)

    # ── MDD 비교 ──────────────────────────
    print("\n" + "─" * 85)
    print("MDD (최대 낙폭) 비교:")
    print("─" * 85)

    mdd_data: dict[str, list] = {s[0]: [] for s in strategies}
    for ticker, close in data.items():
        name = STOCKS[ticker]
        mdd_data["B&H"].append(bnh(close)["mdd"])
        mdd_data["순수 성승현"].append(backtest(close,-2,100,100,999,"성승현")["mdd"])
        mdd_data["성승현+휩쏘방지"].append(backtest(close,-2,100,100,999,"성승현+휩쏘")["mdd"])
        mdd_data["A안 현재설정"].append(backtest(close,-2,3,7,1,"a안")["mdd"])
        mdd_data["A안 최적(예상)"].append(backtest(close,-3,7,15,3,"a안")["mdd"])

    mdd_hdr = f"{'종목':<12}"
    for s_name, _ in strategies:
        mdd_hdr += f" {s_name[:7]:>10}"
    print(mdd_hdr)
    print("-" * 85)
    for ticker, close in data.items():
        name = STOCKS[ticker]
        row  = f"{name:<12}"
        row += f" {mdd_data['B&H'][list(data.keys()).index(ticker)]:>9.1f}%"
        row += f" {mdd_data['순수 성승현'][list(data.keys()).index(ticker)]:>9.1f}%"
        row += f" {mdd_data['성승현+휩쏘방지'][list(data.keys()).index(ticker)]:>9.1f}%"
        row += f" {mdd_data['A안 현재설정'][list(data.keys()).index(ticker)]:>9.1f}%"
        row += f" {mdd_data['A안 최적(예상)'][list(data.keys()).index(ticker)]:>9.1f}%"
        print(row)
    print("-" * 85)
    avg_mdd = f"{'평균':12}"
    for s_name, _ in strategies:
        avg_mdd += f" {np.mean(mdd_data[s_name]):>9.1f}%"
    print(avg_mdd)

    # ── 그리드 서치 (10년 기준) ───────────
    print("\n" + "═" * 75)
    print("【 파라미터 최적화 — 10년 기준 그리드 서치 】")
    print("═" * 75)

    grid = {
        "sell":     [-1, -2, -3, -5],
        "buy_max":  [1, 3, 5, 7, 10],
        "chase_d0": [5, 7, 10, 15, 20],
        "chase_mo": [1, 2, 3, 5],
    }
    combos = list(product(
        grid["sell"], grid["buy_max"], grid["chase_d0"], grid["chase_mo"]
    ))
    print(f"총 {len(combos)}개 조합 × {len(data)}종목 테스트 중...\n")

    results = []
    for sell, buy_max, chase_d0, chase_mo in combos:
        rets, mdds, effs = [], [], []
        for close in data.values():
            r = backtest(close, sell, buy_max, chase_d0, chase_mo, mode="a안")
            rets.append(r["ret"])
            mdds.append(r["mdd"])
            effs.append(r["eff"])
        results.append({
            "sell": sell, "buy_max": buy_max,
            "chase_d0": chase_d0, "chase_mo": chase_mo,
            "avg_ret": np.mean(rets),
            "avg_mdd": np.mean(mdds),
            "avg_eff": np.mean(effs),
        })

    df_r = pd.DataFrame(results).sort_values("avg_eff", ascending=False)

    print(f"{'순위':<4} {'매도기준':>7} {'매수이격':>7} {'추금이격':>7} {'연속월':>6} "
          f"{'평균수익':>8} {'평균MDD':>8} {'효율지수':>8}")
    print("-" * 65)

    current_rank = None
    for rank, (_, row) in enumerate(df_r.head(20).iterrows(), 1):
        marker = ""
        if row["sell"]==-2 and row["buy_max"]==3 and row["chase_d0"]==7 and row["chase_mo"]==1:
            marker = " ◀ 현재설정"
            current_rank = rank
        print(f"{rank:<4} {row['sell']:>6.0f}%  {row['buy_max']:>6.0f}%  "
              f"{row['chase_d0']:>6.0f}%  {row['chase_mo']:>5.0f}개월  "
              f"{row['avg_ret']:>7.0f}%  {row['avg_mdd']:>7.1f}%  "
              f"{row['avg_eff']:>7.2f}{marker}")

    if current_rank is None:
        cur = df_r[(df_r["sell"]==-2)&(df_r["buy_max"]==3)&(df_r["chase_d0"]==7)&(df_r["chase_mo"]==1)]
        current_rank = df_r.index.get_loc(cur.index[0]) + 1 if not cur.empty else "?"

    best = df_r.iloc[0]
    print(f"\n현재 A안 v1.1 순위: {current_rank} / {len(combos)}위")
    print(f"\n🏆 최적 파라미터:")
    print(f"   sell_d0_min      (매도기준): {best['sell']:.0f}%")
    print(f"   buy_turn_d0_max  (매수이격): {best['buy_max']:.0f}%")
    print(f"   chase_block_d0   (추금이격): {best['chase_d0']:.0f}%")
    print(f"   chase_block_mo   (연속월수): {best['chase_mo']:.0f}개월")
    print(f"   → 평균수익: {best['avg_ret']:.0f}%  |  MDD: {best['avg_mdd']:.1f}%  |  효율지수: {best['avg_eff']:.2f}")

    print("\n" + "═" * 75)
    print("※ 효율지수 = 수익률÷|MDD|  (리스크 대비 수익 효율)")
    print("※ 현재 A안이 낮은 이유: 진입조건(전환+D0≤3%)이 너무 엄격")
    print("   → 폭발적 돌파 종목(NVDA 등)에서 진입 기회를 대부분 놓침")


if __name__ == "__main__":
    main()
