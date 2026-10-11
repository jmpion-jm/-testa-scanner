# -*- coding: utf-8 -*-
"""
3개 유니버스 합친 월말 매수 후보 — 나스닥100 ∪ S&P500 ∪ 김학주 관심종목(미국)을 하나로 합쳐 1군·2군·박스권으로 정렬.

배경(2026-10-11 사용자 요청 "만들어줘"): 나스닥100·S&P500 스캔은 각자 채널로 돌파·지지만 보여줘서 어느 게 1군인지
알 수 없었고, 1군 순위는 김학주 종목(integrated_scan)에만 있었다. 10/1에 손으로 계산했던 "세 곳 합친 1군 22종목"을
매달 자동으로 만든다. 판정 기준은 integrated_scan과 동일(같은 함수 사용):
  - 신호  = book_patterns.buy_signal (월말 종가 확정 돌파·10이평 지지) — 진행 중인 달 제외(completed_index)
  - 1군   = 지금 상승이 원서 패턴 완성 후킹으로 시작(book_patterns.last_breakout_pattern)
  - 3군   = 📦박스권(book_patterns.in_box) / 나머지 2군
  - 미국 종목만(9/26 결정), 장기 보유 GOOGL 제외(10/3 결정). 매출·실적은 순위에 안 씀.
월봉은 일봉에서 만든다(monthly_data — 야후 월봉 오류, 10/1). 보유종목 정보는 넣지 않는다(공유 채널용).

실행: python universe_scan.py            (콘솔)
      python universe_scan.py slack      (김학주 채널로 전송)
      python universe_scan.py csv        (영웅문용 1군 CSV → C:\\KiwoomGlobal\\temp\\tier1_all.csv, PC에서)
"""
import sys, os, json, warnings, urllib.request, time
warnings.filterwarnings('ignore')
import pandas as pd
import yfinance as yf

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import monthly_data
import book_patterns as bkp
import market_time as mt

CFG = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8'))
STOCKS = CFG.get('stocks', {})
LONG_HOLD = {'GOOGL'}
TIER_LABEL = {1: '🥇 1군 — 원서 패턴 완성으로 시작한 상승', 2: '🥈 2군 — 원서 매수 신호', 3: '🥉 3군 — 📦박스권 안(후순위)'}


def completed_index(d) -> int:
    last = len(d) - 1
    if d.index[last].to_period('M') == pd.Period(mt.us_ref_date(), 'M') and not mt.is_monthend_after_close():
        return last - 1
    return last


def universe() -> dict:
    import sp500_scan, nasdaq100_scan
    src = {}
    # 출처 표기 순서: 김학주 → 나스닥 → S&P (사용자 요청 2026-10-11)
    for t in STOCKS:
        if not (t[0].isdigit() or '.' in t):
            src.setdefault(t, []).append('김학주')
    for t in nasdaq100_scan.get_ndx100_tickers():
        src.setdefault(t, []).append('나스닥')
    for t in sp500_scan.get_sp500_tickers():
        src.setdefault(t, []).append('S&P')
    return {t: v for t, v in src.items() if t not in LONG_HOLD}


SECTOR_KO = {'Technology': '기술', 'Communication Services': '통신·미디어', 'Consumer Cyclical': '경기소비재',
             'Consumer Defensive': '필수소비재', 'Energy': '에너지', 'Financial Services': '금융',
             'Healthcare': '헬스케어', 'Industrials': '산업재', 'Real Estate': '부동산',
             'Basic Materials': '소재', 'Utilities': '유틸리티'}


def info(t: str) -> tuple:
    """(이름, 업종) — 이름은 김학주 종목이면 config 한글명, 아니면 야후 영문. 업종은 분산 판단용이라
    모든 종목을 같은 기준(야후 sector → 한글)으로 묶는다(김학주 config 업종은 세부 테마라 섞으면 같은 업종이 갈라짐)."""
    name, sec = (STOCKS[t][0] if t in STOCKS else t), '-'
    try:
        i = yf.Ticker(t).info
        if t not in STOCKS:
            name = (i.get('shortName') or t)[:24]
        sec = SECTOR_KO.get(i.get('sector'), i.get('sector') or '-')
    except Exception:
        if t in STOCKS:
            sec = STOCKS[t][1]
    return name, sec


def scan() -> tuple:
    src = universe()
    tickers = sorted(src)
    out, err, month = [], [], None
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        try:
            raw = monthly_data.download(chunk, '3y')
        except Exception as e:
            err.append((f'{chunk[0]}~', str(e)[:60]))
            continue
        for t in chunk:
            try:
                if t not in raw.columns.get_level_values(0):
                    continue
                df = raw[t][['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
                if len(df) < 13:
                    continue
                d = bkp.prepare(df)
                k = completed_index(d)
                sig = bkp.buy_signal(d, k)
                month = d.index[k].strftime('%Y-%m')
                if not sig:
                    continue
                out.append(dict(ticker=t, signal=sig, box=bool(bkp.in_box(d, k)), src='/'.join(src[t]),
                                pct=round((d['Close'].iat[k] / d['MA'].iat[k] - 1) * 100, 1), sig_month=month))
            except Exception as e:
                err.append((t, str(e)[:60]))
    # 신호 종목만 긴 이력으로 원서 패턴(1군) 판정 — 통합 스캔과 같은 데이터·함수
    import bullish_pattern_scan as bp
    for c in out:
        try:
            full = bp.fetch_monthly(c['ticker'])
            full = full[full.index.strftime('%Y-%m') <= c['sig_month']]
            r = bkp.last_breakout_pattern(full)
            names = (([r['bull']['pattern']] if r['bull'] else []) + [x['pattern'] for x in r['composite']]) if r else []
            c['broke_up'] = r is not None
            c['pattern'] = '+'.join(names) or ('후킹' if r else '')
            c['hook'] = r['hook_date'].strftime('%Y-%m') if r else ''
        except Exception as e:
            c['broke_up'], c['pattern'], c['hook'] = False, '', ''
            err.append((c['ticker'], f'패턴 판정 실패: {str(e)[:40]}'))
        c['name'], c['sector'] = info(c['ticker'])
        c['tier'] = 3 if c['box'] else (1 if c['broke_up'] else 2)
    out.sort(key=lambda c: (c['tier'], c['sector'], c['signal'] != '돌파', c['ticker']))
    return out, err, month, len(src)


def message(out, err, month, n) -> str:
    cnt = {k: sum(1 for c in out if c['tier'] == k) for k in (1, 2, 3)}
    L = [f'*월말 매수 후보 — 나스닥100·S&P500·김학주 통합* ({month}말 확정)',
         f'_{n}종목 중 원서 매수 신호 {len(out)}종목 · 1군 {cnt[1]} / 2군 {cnt[2]} / 3군 {cnt[3]}_',
         '_1군 안에서는 데이터상 우열 없음 → 업종이 겹치지 않게 고르기. 매출·실적은 순위에 안 씀._']
    for tier in (1, 2, 3):
        rows = [c for c in out if c['tier'] == tier]
        if not rows:
            continue
        L.append(f'\n*{TIER_LABEL[tier]} ({len(rows)})*')
        sec = None
        for c in rows:
            if c['sector'] != sec:
                sec = c['sector']
                L.append(f'  _{sec}_')
            pat = f" · {c['pattern']}(후킹 {c['hook']})" if c.get('pattern') else ''
            L.append(f"  `{c['ticker']}` {c['name']}  {c['signal']} 10이평 {c['pct']:+.1f}%{pat}  [{c['src']}]")
    if err:
        L.append(f'\n_⚠️ 판정 실패 {len(err)}건: ' + ', '.join(t for t, _ in err[:10]) + '_')
    L.append('\n_원칙: 월말 종가 확정 신호만. 매수 = 다음 미국장 개장 10~30분 뒤 현재가 근처 지정가. 매도 = 월말 종가 10이평 이탈._')
    return '\n'.join(L)


def send(text: str, url: str = None) -> bool:
    url = url or CFG.get('slack_webhook_url', '')
    if not url:
        return False
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, data=json.dumps({'text': text}).encode('utf-8'),
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.read().decode() == 'ok'
        except Exception as e:
            print(f'슬랙 전송 오류({attempt}/2): {e}')
            time.sleep(3)
    return False


def write_csv(out, path=r'C:\KiwoomGlobal\temp\tier1_all.csv') -> str:
    """영웅문글로벌 관심종목 가져오기용 — 한 줄에 거래소접두어+티커(ND=나스닥, NY=뉴욕), 영어 파일명·ASCII."""
    lines = []
    for c in out:
        if c['tier'] != 1:
            continue
        ex = yf.Ticker(c['ticker']).fast_info.get('exchange', '')
        pre = 'ND' if ex in ('NMS', 'NGM', 'NCM', 'NAS') else 'NY' if ex in ('NYQ',) else None
        if pre:
            lines.append(pre + c['ticker'])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='ascii', newline='\r\n').write('\n'.join(lines) + '\n')
    return path


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'slack' and not mt.should_run_monthly_scan():
        print('말일 미국장 마감 후가 아니라 스킵 (market_time.should_run_monthly_scan)')
        sys.exit(0)
    out, err, month, n = scan()
    text = message(out, err, month, n)
    print(text)
    if mode == 'slack':
        ok = send(text)
        print(f'슬랙 전송: {"완료" if ok else "실패"}')
        # 지인 공유 채널(2026-10-11) — 이 메시지엔 보유종목 정보가 없어 그대로 보낸다
        share = CFG.get('slack_webhook_url_share') or CFG.get('slack_webhook_url_private')
        if share:
            print(f'공유 채널 전송: {"완료" if send(text, share) else "실패"}')
        if not ok:
            sys.exit(1)
    elif mode == 'csv':
        print('영웅문 CSV:', write_csv(out))
