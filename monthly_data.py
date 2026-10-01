# -*- coding: utf-8 -*-
"""월봉은 야후 월봉(interval='1mo')을 쓰지 말고 일봉에서 직접 만든다 — 2026-10-01 발견.

야후 월봉은 새 달이 시작되면 직전 달 봉을 그 달 하순 어느 날 값에서 멈춘 채로 준다
(예: AMAT 9월 종가 실제 511.38 → 야후 월봉 474.25, 고가 515.42 → 474.90 / IONQ 43.86 → 44.98 /
META 725.18 → 777.59). 9월 마지막 거래일 저녁(새 달 시작 전)에는 맞게 나오다가, 10/1 미국장이
열리자 1y~max 모든 기간에서 틀린 값으로 바뀌었다. period='max'는 그 전부터 틀렸다.
일봉은 정확하다 → 일봉을 월 단위로 묶어 월봉을 만든다(시가=첫날 시가, 고가=최고, 저가=최저, 종가=마지막 날 종가, 거래량=합).
월봉MA10 판단(매수 신호·매도·패턴·검증기록)은 전부 이 함수를 거칠 것.
"""
import pandas as pd
import yfinance as yf

AGG = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}


def to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    """일봉 → 월봉(인덱스 = 그 달 1일, 원래 시간대 유지). 거래 없는 달은 제외."""
    if daily is None or len(daily) == 0:
        return pd.DataFrame(columns=list(AGG))
    cols = [c for c in AGG if c in daily.columns]
    daily = daily[cols].dropna(subset=['Close'])
    if len(daily) == 0:
        return pd.DataFrame(columns=cols)
    return daily.resample('MS').agg({c: AGG[c] for c in cols}).dropna(subset=['Close'])


def history(ticker: str, period: str = '3y') -> pd.DataFrame:
    """yf.Ticker(t).history(period, interval='1mo') 대신 쓰는 함수."""
    return to_monthly(yf.Ticker(ticker).history(period=period, interval='1d', auto_adjust=True))


def download(tickers, period: str = '3y', threads: bool = True) -> pd.DataFrame:
    """yf.download(tickers, period, interval='1mo', group_by='ticker') 대신 — 같은 모양(MultiIndex 열)으로 반환."""
    tickers = [tickers] if isinstance(tickers, str) else list(tickers)
    raw = yf.download(tickers, period=period, interval='1d', auto_adjust=True,
                      group_by='ticker', progress=False, threads=threads)
    out = {}
    for t in tickers:
        try:
            sub = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        m = to_monthly(sub)
        if len(m):
            out[t] = m
    return pd.concat(out, axis=1) if out else pd.DataFrame()
