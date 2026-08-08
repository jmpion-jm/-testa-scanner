"""
BNF 역추세(평균회귀) 단타 전략 — 신호 엔진 스켈레톤
=====================================================
영상 "4천억 천재 트레이더 BNF의 매매법" 의 규칙을 코드로 변환한 것.

핵심 아이디어:
  - 급락으로 25일 EMA에서 크게 벌어진(이격도 큰) 과매도 종목을 매수
  - "가격은 결국 평균으로 회귀한다" 는 평균회귀 베팅
  - 진입 트리거: 이격도 + RSI 과매도 + MACD 히스토그램 0선 상향돌파

주의:
  - RSI/MACD 기간 등은 영상 제작자의 재구성이며 BNF가 공식 문서화한 값이 아님.
    전부 설정값(Config)으로 빼두었으니 백테스트로 튜닝할 것.
  - 익절은 BNF가 '감각'으로 했다고 하므로 완전 자동화 불가 → 모멘텀 약화로 근사.
  - 데이터프레임 컬럼 규약: ['open','high','low','close','volume'], index=DatetimeIndex
"""

from dataclasses import dataclass
import pandas as pd
import numpy as np


# ---------------------------------------------------------------------------
# 1. 설정값 (백테스트로 튜닝)
# ---------------------------------------------------------------------------
@dataclass
class BNFConfig:
    # 지표 기간
    ema_period: int = 25
    rsi_period: int = 14
    rsi_oversold: float = 30.0          # 영상: 30~32 이하 (불일치 → 보수적으로 30)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # 이격도 임계값 (음수 = 25일선 아래로 벌어진 정도, %)
    disparity_largecap: float = -20.0   # 대형 우량주
    disparity_smallcap: float = -32.0   # 중소형/고변동 종목
    disparity_bull: float = -22.0       # 상승장 (조금만 눌려도 진입)
    disparity_bear: float = -33.0       # 하락장 (많이 눌려야 진입)

    # 급락 판단 (최근 N일 누적 수익률이 임계 이하)
    drop_lookback: int = 5
    drop_threshold: float = -8.0        # %

    # 거래량/캔들 수축 판단 (바닥패턴 보조조건)
    contract_lookback: int = 5

    # 청산
    stop_loss_pct: float = -5.0         # 손절: 기계적 고정 %
    bear_quick_tp_pct: float = 5.0      # 하락장 빠른 익절 목표 %


# ---------------------------------------------------------------------------
# 2. 지표 계산
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd_histogram(close: pd.Series, fast=12, slow=26, signal=9) -> pd.Series:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line - signal_line     # 영상: 히스토그램만 사용


def add_indicators(df: pd.DataFrame, cfg: BNFConfig) -> pd.DataFrame:
    out = df.copy()
    out["ema25"] = ema(out["close"], cfg.ema_period)
    out["disparity"] = (out["close"] - out["ema25"]) / out["ema25"] * 100  # 이격도 %
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["macd_hist"] = macd_histogram(out["close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    out["ret_n"] = out["close"].pct_change(cfg.drop_lookback) * 100   # 최근 N일 수익률
    out["range"] = out["high"] - out["low"]                          # 캔들 길이
    return out


# ---------------------------------------------------------------------------
# 3. 시장/종목 상태에 따른 이격도 임계값 선택
# ---------------------------------------------------------------------------
def disparity_threshold(cfg: BNFConfig, regime: str = "neutral", cap: str = "large") -> float:
    if regime == "bull":
        return cfg.disparity_bull
    if regime == "bear":
        return cfg.disparity_bear
    return cfg.disparity_largecap if cap == "large" else cfg.disparity_smallcap


# ---------------------------------------------------------------------------
# 4. 매수 진입 신호 (4단계 핵심 공식)
# ---------------------------------------------------------------------------
def entry_signal(df: pd.DataFrame, i: int, cfg: BNFConfig,
                 regime: str = "neutral", cap: str = "large"):
    """
    반환: (triggered: bool, reasons: dict)
      1) 단기 급락
      2) 25일선 이격도 임계 이하
      3) RSI 과매도
      4) MACD 히스토그램 0선 상향돌파 (빨강→초록)  ← 최종 트리거
    """
    row, prev = df.iloc[i], df.iloc[i - 1]
    thr = disparity_threshold(cfg, regime, cap)

    reasons = {
        "sharp_drop":    row["ret_n"] <= cfg.drop_threshold,
        "disparity":     row["disparity"] <= thr,
        "rsi_oversold":  row["rsi"] <= cfg.rsi_oversold,
        "macd_cross_up": (prev["macd_hist"] < 0) and (row["macd_hist"] >= 0),
    }
    return all(reasons.values()), reasons


# ---------------------------------------------------------------------------
# 5. 바닥패턴 보조조건 (역추세 3조건)
# ---------------------------------------------------------------------------
def bottom_pattern(df: pd.DataFrame, i: int, cfg: BNFConfig) -> dict:
    """
    ① 급락 후 거래량 감소 + 캔들 길이 축소
    ② 더 이상 신저가 없이 횡보
    ③ 첫 강한 양봉 출현
    """
    win = df.iloc[i - cfg.contract_lookback: i + 1]
    row = df.iloc[i]
    return {
        "vol_contracting":  row["volume"] < win["volume"].iloc[:-1].mean(),
        "range_shrinking":  row["range"] < win["range"].iloc[:-1].mean(),
        "no_new_low":       row["low"] >= win["low"].iloc[:-1].min(),
        "bullish_candle":   row["close"] > row["open"],
    }


# ---------------------------------------------------------------------------
# 6. 청산 신호 (손절=기계적 / 익절=모멘텀 약화로 근사)
# ---------------------------------------------------------------------------
def exit_signal(df: pd.DataFrame, i: int, cfg: BNFConfig,
                entry_price: float, regime: str = "neutral"):
    row, prev = df.iloc[i], df.iloc[i - 1]
    pnl = (row["close"] - entry_price) / entry_price * 100

    if pnl <= cfg.stop_loss_pct:
        return True, "stop_loss", pnl

    # 익절: 거래량 감소 + 히스토그램 둔화 = 모멘텀 약화
    momentum_fading = (row["macd_hist"] < prev["macd_hist"]) and (row["volume"] < prev["volume"])

    if regime == "bear" and pnl >= cfg.bear_quick_tp_pct:   # 하락장 빠른 익절
        return True, "take_profit_fast", pnl
    if momentum_fading and pnl > 0:                          # 상승 동력 소멸
        return True, "take_profit_momentum", pnl
    return False, None, pnl


# ---------------------------------------------------------------------------
# 7. 종목 스크리닝 (유니버스 필터) — 데이터 소스 연결 필요
# ---------------------------------------------------------------------------
def screen_universe(df: pd.DataFrame, i: int, vol_surge_mult: float = 3.0) -> dict:
    """
    BNF 종목 선정 3기준 중 정량화 가능한 부분:
      ① 거래량 폭증  ② 고변동성
      (③ 뉴스/테마 집중 → 별도 테마 리스트 매핑 필요, 여기선 미구현)
    """
    row = df.iloc[i]
    avg_vol = df["volume"].iloc[i - 20:i].mean()
    atr = (df["high"] - df["low"]).iloc[i - 14:i].mean()
    return {
        "volume_surge":   row["volume"] > avg_vol * vol_surge_mult,
        "high_volatility": (row["high"] - row["low"]) > atr,
    }


# ---------------------------------------------------------------------------
# 8. 사용 예시 (단일 종목 백테스트 루프 골격)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    cfg = BNFConfig()
    # df = load_ohlcv("005930")   # pykrx / KIS / yfinance 등으로 교체
    # df = add_indicators(df, cfg)
    #
    # position = None
    # for i in range(30, len(df)):
    #     if position is None:
    #         ok, why = entry_signal(df, i, cfg, regime="bear", cap="large")
    #         if ok:
    #             position = {"entry_price": df.iloc[i]["close"], "entry_idx": i}
    #             # → Slack 알림 / 주문
    #     else:
    #         done, kind, pnl = exit_signal(df, i, cfg, position["entry_price"], regime="bear")
    #         if done:
    #             # → Slack 알림 / 청산
    #             position = None
    print("BNF strategy module loaded.")
