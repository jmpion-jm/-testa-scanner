"""
optimize_by_sector.py
────────────────────────────────────────────────────────────
섹터별 다종목 월봉 10MA 파라미터 최적화

목표: 방산·AI·반도체·전력·조선 등 다양한 섹터에서
     공통적으로 잘 작동하는 매매법 파라미터 탐색
────────────────────────────────────────────────────────────
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pandas as pd
import numpy as np
import yfinance as yf
from itertools import product
import FinanceDataReader as fdr

# ────────────────────────────────────────────
# 섹터별 종목 (미국주식 + 한국ETF 혼합)
# ────────────────────────────────────────────
SECTORS = {
    "🛡️ 방산": {
        "LHX":  "L3해리스",
        "RTX":  "RTX",
        "CRS":  "카펜터테크",
        "ATI":  "ATI",
    },
    "🤖 AI·데이터센터": {
        "NVDA": "엔비디아",
        "AMD":  "AMD",
        "AVGO": "브로드컴",
        "NTAP": "넷앱",
        "ALAB": "아스테라랩스",
    },
    "💡 반도체·소재": {
        "MRVL": "마벨테크",
        "ON":   "온세미",
        "NVTS": "나비타스",
        "APH":  "암페놀",
        "COHR": "코히런트",
    },
    "⚡ 전력·에너지": {
        "VRT":  "버티브",
        "BWXT": "BWX테크",
        "GLW":  "코닝",
        "LITE": "루멘텀",
    },
    "🚀 우주·위성·양자": {
        "ASTS": "AST스페이스",
        "IONQ": "아이온큐",
        "AAOI": "어플라이드옵토",
    },
    "🔋 2차전지(KR)": {
        "364980": "TIGER2차전지TOP10",
    },
    "⚓ 조선(KR)": {
        "466920": "SOL조선TOP3",
    },
    "☢️ 원자력(KR)": {
        "0051G0": "SOL원자력SMR",
    },
    "🧠 AI ETF(KR)": {
        "491010": "TIGER글로벌AI인프라",
        "466950": "TIGER글로벌AI액티브",
        "0023A0": "SOL양자컴퓨팅TOP10",
    },
}

START = "2018-01-01"   # 2018 이후 공통 (단기 ETF 포함)
END   = "2025-12-31"

# ────────────────────────────────────────────
# 데이터 수집
# ────────────────────────────────────────────
def fetch(code: str) -> pd.Series:
    """US 주식 or 한국 ETF 월봉 종가 반환"""
    # 한국 코드 판별 (숫자 6자리 or 알파숫자 혼합 6자리)
    is_kr = len(code) == 6 and not code.isalpha()
    try:
        if is_kr:
            df = fdr.DataReader(code, start=START, end=END)
            if df is None or df.empty:
                return pd.Series(dtype=float)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            s = df["Close"].resample("ME").last().dropna()
        else:
            tk = yf.Ticker(code)
            df = tk.history(start=START, end=END, interval="1mo")
            if df.empty:
                return pd.Series(dtype=float)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            s = df["Close"].dropna()

        # 미완성 월봉 제거
        today = pd.Timestamp.now()
        if not s.empty and s.index[-1].month == today.month and s.index[-1].year == today.year:
            s = s.iloc[:-1]
        return s
    except Exception:
        return pd.Series(dtype=float)


# ────────────────────────────────────────────
# 백테스트 엔진
# ────────────────────────────────────────────
def backtest(close: pd.Series, sell: float, buy_max: float,
             chase_d0: float, chase_mo: int) -> dict:
    ma10 = close.rolling(10, min_periods=10).mean()
    cash, shares = 1.0, 0.0
    in_pos, entry_price = False, 0.0
    trades, port = [], []

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

        # 신호
        if c < m:
            sig = "매도" if d0 <= sell else "관망"
        elif turn_up and d0 <= buy_max:
            sig = "매수"
        elif c >= m and (d0 >= chase_d0 or cons_up >= chase_mo
                         or (turn_up and d0 > buy_max)):
            sig = "추금"
        else:
            sig = "관망"

        # 실행
        if not in_pos:
            if sig == "매수":
                in_pos, entry_price = True, c
                shares, cash = cash / c, 0.0
        else:
            if sig == "매도":
                trades.append((c - entry_price) / entry_price * 100)
                in_pos, cash, shares = False, shares * c, 0.0

        port.append(cash + shares * c)

    if in_pos and port:
        c = float(close.iloc[-1])
        trades.append((c - entry_price) / entry_price * 100)
        port[-1] = shares * c

    ps  = pd.Series(port)
    tot = (ps.iloc[-1] - 1) * 100
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
    return {"ret": tot, "mdd": mdd, "eff": tot / abs(mdd) if mdd else 0}


# ────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────
def main():
    print(f"📅 시뮬레이션 기간: {START} ~ {END}\n")

    # 1) 데이터 수집
    print("데이터 수집 중...")
    all_data: dict[str, dict[str, pd.Series]] = {}
    for sector, stocks in SECTORS.items():
        all_data[sector] = {}
        for code, name in stocks.items():
            s = fetch(code)
            if len(s) >= 12:
                all_data[sector][code] = s
                print(f"  ✅ [{sector[2:6]}] {name}({code}): {len(s)}개월")
            else:
                print(f"  ⚠️ [{sector[2:6]}] {name}({code}): 데이터 부족({len(s)}개)")

    # 2) 섹터별 현재 A안 vs B&H 비교
    print("\n" + "═" * 72)
    print("【 섹터별 현재 A안(3%) vs B&H 비교 】")
    print(f"{'섹터':<16} {'종목':<14} {'B&H수익':>8} {'A안수익':>8} {'B&H MDD':>8} {'A안 MDD':>8} {'거래수':>5}")
    print("─" * 72)

    for sector, stocks_data in all_data.items():
        for code, close in stocks_data.items():
            name = SECTORS[sector][code][:7]
            b = bnh(close)
            a = backtest(close, -2, 3, 7, 1)
            print(f"{sector[2:9]:<16} {name:<14} "
                  f"{b['ret']:>7.0f}%  {a['ret']:>7.0f}%  "
                  f"{b['mdd']:>7.1f}%  {a['mdd']:>7.1f}%  {a['n']:>4}회")

    # 3) 그리드 서치 — 전 섹터 통합
    print("\n" + "═" * 72)
    print("【 파라미터 그리드 서치 — 전 섹터 통합 최적화 】")

    grid = {
        "sell":     [-1, -2, -3, -5],
        "buy_max":  [1, 3, 5, 7, 10],
        "chase_d0": [5, 7, 10, 15, 20],
        "chase_mo": [1, 2, 3, 5],
    }
    combos = list(product(
        grid["sell"], grid["buy_max"], grid["chase_d0"], grid["chase_mo"]
    ))

    # 전체 종목 평탄화
    flat: list[pd.Series] = []
    for sd in all_data.values():
        flat.extend(sd.values())

    print(f"총 {len(combos)}개 조합 × {len(flat)}개 종목 테스트 중...\n")

    results = []
    for sell, buy_max, chase_d0, chase_mo in combos:
        rets, mdds, effs, ns = [], [], [], []
        for close in flat:
            r = backtest(close, sell, buy_max, chase_d0, chase_mo)
            rets.append(r["ret"]); mdds.append(r["mdd"])
            effs.append(r["eff"]); ns.append(r["n"])
        results.append({
            "sell": sell, "buy_max": buy_max,
            "chase_d0": chase_d0, "chase_mo": chase_mo,
            "avg_ret": np.mean(rets),
            "med_ret": np.median(rets),   # 중앙값 (NVDA 왜곡 방지)
            "avg_mdd": np.mean(mdds),
            "avg_eff": np.mean(effs),
            "med_eff": np.median(effs),   # 중앙값 효율지수
            "avg_n":   np.mean(ns),
        })

    # 중앙값 효율지수 기준으로 정렬 (왜곡 방지)
    df_r = pd.DataFrame(results).sort_values("med_eff", ascending=False)

    print(f"{'순위':<4} {'매도':>6} {'매수이격':>7} {'추금이격':>7} {'연속월':>5} "
          f"{'평균수익':>8} {'중앙수익':>8} {'평균MDD':>8} {'중앙효율':>8} {'평균거래':>6}")
    print("─" * 75)

    cur_rank = None
    for rank, (_, row) in enumerate(df_r.head(20).iterrows(), 1):
        is_cur = (row["sell"]==-2 and row["buy_max"]==3
                  and row["chase_d0"]==7 and row["chase_mo"]==1)
        if is_cur:
            cur_rank = rank
        marker = " ◀현재" if is_cur else ""
        print(f"{rank:<4} {row['sell']:>5.0f}%  {row['buy_max']:>6.0f}%  "
              f"{row['chase_d0']:>6.0f}%  {row['chase_mo']:>4.0f}개월  "
              f"{row['avg_ret']:>7.0f}%  {row['med_ret']:>7.0f}%  "
              f"{row['avg_mdd']:>7.1f}%  {row['med_eff']:>7.2f}  "
              f"{row['avg_n']:>5.1f}회{marker}")

    if cur_rank is None:
        cdf = df_r[(df_r["sell"]==-2)&(df_r["buy_max"]==3)
                   &(df_r["chase_d0"]==7)&(df_r["chase_mo"]==1)]
        cur_rank = df_r.index.get_loc(cdf.index[0]) + 1 if not cdf.empty else "?"

    best = df_r.iloc[0]
    print(f"\n현재 A안 v1.1 전체 순위: {cur_rank} / {len(combos)}위")
    print(f"\n🏆 전 섹터 최적 파라미터 (중앙값 효율지수 기준):")
    print(f"   매도기준  (sell_d0_min):       {best['sell']:.0f}%")
    print(f"   매수이격  (buy_turn_d0_max):   {best['buy_max']:.0f}%")
    print(f"   추금이격  (chase_block_d0):    {best['chase_d0']:.0f}%")
    print(f"   연속월수  (chase_block_months):{best['chase_mo']:.0f}개월")
    print(f"   → 중앙수익: {best['med_ret']:.0f}%  |  MDD: {best['avg_mdd']:.1f}%  "
          f"|  중앙효율: {best['med_eff']:.2f}  |  평균거래: {best['avg_n']:.1f}회")

    # 4) 섹터별 최적 파라미터 비교
    print("\n" + "═" * 72)
    print("【 섹터별 최적 파라미터 비교 (섹터마다 다를 수 있음) 】")
    print("─" * 72)

    for sector, stocks_data in all_data.items():
        if not stocks_data:
            continue
        sec_results = []
        for sell, buy_max, chase_d0, chase_mo in combos:
            rets, effs = [], []
            for close in stocks_data.values():
                r = backtest(close, sell, buy_max, chase_d0, chase_mo)
                rets.append(r["ret"]); effs.append(r["eff"])
            sec_results.append({
                "sell": sell, "buy_max": buy_max,
                "chase_d0": chase_d0, "chase_mo": chase_mo,
                "avg_ret": np.mean(rets), "med_eff": np.median(effs),
            })
        best_s = sorted(sec_results, key=lambda x: x["med_eff"], reverse=True)[0]
        cur_s  = next(r for r in sec_results
                      if r["sell"]==-2 and r["buy_max"]==3
                      and r["chase_d0"]==7 and r["chase_mo"]==1)
        print(f"{sector}")
        print(f"  최적: 매도{best_s['sell']:.0f}% / 매수이격{best_s['buy_max']:.0f}% / "
              f"추금{best_s['chase_d0']:.0f}% / {best_s['chase_mo']:.0f}개월  "
              f"→ 평균수익 {best_s['avg_ret']:.0f}%")
        print(f"  현재: 매도 -2% / 매수이격 3% / 추금 7% / 1개월  "
              f"→ 평균수익 {cur_s['avg_ret']:.0f}%")

    print("\n" + "═" * 72)
    print("※ 중앙값(Median) 기준 = NVDA 같은 극단값 종목의 왜곡 제거")
    print("※ 섹터별 최적이 다르면 계좌별로 파라미터를 분리 적용 권장")


if __name__ == "__main__":
    main()
