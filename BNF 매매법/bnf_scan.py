# -*- coding: utf-8 -*-
"""
BNF 역추세(평균회귀) 매매법 — 한국 주식 일봉 스캔
=====================================================
KOSPI 시총 상위 약 150종목 대상
조건(4단계 모두 충족):
  ① 단기 급락   : 최근 5일 누적 수익률 -8% 이하
  ② 이격도      : 25일 EMA 대비 -20% 이하 (대형) / -32% 이하 (중소형)
  ③ RSI         : 14일 RSI 30 이하 (과매도)
  ④ MACD 히스토 : 전봉 음수 → 현봉 0 이상 (0선 상향돌파) ← 최종 트리거

테스타(추세추종)와 반대 방향. 하락/급락 종목에서 반등을 노리는 단기 역추세 매매.
손절 -5%는 기계적으로 철저히 준수.

실행: 매일 장 마감 후(오후 4시) 직접 실행 또는 스케줄러.
"""
import os, sys, json, requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 콘솔 출력 UTF-8 강제 (Windows cp949 환경에서 한글/특수문자 print 오류 방지)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

try:
    from pykrx import stock as krx
except ImportError:
    print("pykrx 미설치: pip install pykrx")
    sys.exit(1)

# BNF 전략 로직(스켈레톤) 재사용 — 같은 폴더의 bnf_strategy.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bnf_strategy import (
    BNFConfig, add_indicators, entry_signal, bottom_pattern,
    disparity_threshold,
)


# ── 설정 ─────────────────────────────────────────────────────
# config.json 은 상위 폴더(주식정보)에 위치
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, '..', 'config.json')
try:
    with open(_CFG_PATH, encoding='utf-8') as _f:
        _cfg = json.load(_f)
    # BNF 전용 webhook 우선, 없으면 기본 webhook
    SLACK_URL    = _cfg.get('slack_webhook_url_bnf') or _cfg.get('slack_webhook_url', '')
    # BNF 전용 계좌 설정 없으면 testa_account 동일 사용
    BNF_ACCOUNT  = int(_cfg.get('bnf_account', _cfg.get('testa_account', 0)))
    BNF_RISK_PCT = float(_cfg.get('bnf_risk_pct', 2.0))   # BNF는 단기 역추세 → 비중 작게(2%)
except Exception:
    SLACK_URL    = os.environ.get('SLACK_WEBHOOK_URL', '')
    BNF_ACCOUNT  = 0
    BNF_RISK_PCT = 2.0

DATA_DAYS     = 90      # 데이터 조회 기간 (지표 계산용, 최근 60일 + 여유분)
MIN_BARS      = 40      # 지표 계산 최소 봉 수 (EMA25/RSI14/MACD26 안정화)
CFG           = BNFConfig()   # 전략 파라미터


# ── 유니버스: testa KOSPI 98종목 + BNF 추가 중형주 ────────────
KOSPI_UNIVERSE = [
    ('005930', '삼성전자'),    ('000660', 'SK하이닉스'),
    ('207940', '삼성바이오로직스'), ('373220', 'LG에너지솔루션'),
    ('005380', '현대차'),      ('000270', '기아'),
    ('005490', 'POSCO홀딩스'), ('028260', '삼성물산'),
    ('105560', 'KB금융'),      ('068270', '셀트리온'),
    ('055550', '신한지주'),    ('012330', '현대모비스'),
    ('006400', '삼성SDI'),     ('051910', 'LG화학'),
    ('086790', '하나금융지주'), ('035420', 'NAVER'),
    ('066570', 'LG전자'),      ('033780', 'KT&G'),
    ('017670', 'SK텔레콤'),    ('032830', '삼성생명'),
    ('010130', '고려아연'),    ('003550', 'LG'),
    ('030200', 'KT'),          ('000810', '삼성화재'),
    ('012450', '한화에어로스페이스'), ('009540', '한국조선해양'),
    ('034020', '두산에너빌리티'), ('047810', '한국항공우주'),
    ('010950', 'S-Oil'),       ('004020', '현대제철'),
    ('009150', '삼성전기'),    ('086280', '현대글로비스'),
    ('034220', 'LG디스플레이'), ('010140', '삼성중공업'),
    ('000720', '현대건설'),    ('036570', '엔씨소프트'),
    ('096770', 'SK이노베이션'), ('316140', '우리금융지주'),
    ('015760', '한국전력'),    ('011200', 'HMM'),
    ('003670', '포스코퓨처엠'), ('247540', '에코프로비엠'),
    ('086520', '에코프로'),    ('241560', '두산밥캣'),
    ('326030', 'SK바이오팜'),  ('035720', '카카오'),
    ('097950', 'CJ제일제당'),  ('011170', '롯데케미칼'),
    ('019440', '한국타이어앤테크놀로지'), ('078930', 'GS'),
    ('267250', 'HD현대'),            ('329180', 'HD현대중공업'),
    ('042660', '한화오션'),          ('079550', 'LIG넥스원'),
    ('064350', '현대로템'),          ('003490', '대한항공'),
    ('180640', '한진칼'),            ('034730', 'SK'),
    ('001040', 'CJ'),                ('000370', '한화'),
    ('009830', '한화솔루션'),        ('032640', 'LG유플러스'),
    ('024110', '기업은행'),          ('005830', 'DB손해보험'),
    ('088350', '한화생명'),          ('000100', '유한양행'),
    ('128940', '한미약품'),          ('185750', '종근당'),
    ('006280', '녹십자'),            ('018260', '삼성SDS'),
    ('004170', '신세계'),            ('139480', '이마트'),
    ('069960', '현대백화점'),        ('023530', '롯데쇼핑'),
    ('282330', 'BGF리테일'),         ('007070', 'GS리테일'),
    ('271560', '오리온'),            ('000080', '하이트진로'),
    ('084370', '한국금융지주'),      ('036460', '한국가스공사'),
    ('051600', '한전KPS'),           ('052690', '한전기술'),
    ('298040', '효성중공업'),        ('002380', 'KCC'),
    ('011780', '금호석유화학'),      ('011790', 'SKC'),
    ('010060', 'OCI홀딩스'),         ('042670', 'HD현대인프라코어'),
    ('021240', '코웨이'),            ('008770', '호텔신라'),
    ('006360', 'GS건설'),            ('000120', 'CJ대한통운'),
    ('035250', '강원랜드'),          ('055490', '제일기획'),
    ('012750', '에스원'),            ('004800', '효성'),
    ('026960', '동서'),              ('161890', '한국콜마'),
]

# 추가 종목 (BNF는 변동성 큰 중형주도 포함)
BNF_EXTRA = [
    ('000990', 'DB하이텍'),      ('047050', '포스코인터내셔널'),
    ('161390', '한국타이어앤테크'), ('016360', '삼성증권'),
    ('005940', 'NH투자증권'),    ('039490', '키움증권'),
    ('030610', '교보생명'),      ('032830', '삼성생명'),
    ('088350', '한화생명'),      ('000720', '현대건설'),
    ('047040', 'CJ CGV'),        ('036460', '한국가스공사'),
    ('015760', '한국전력'),      ('017670', 'SK텔레콤'),
]


def get_universe() -> list:
    """KOSPI 대형주 + BNF 추가 중형주 (중복 제거)"""
    seen, uni = set(), []
    for tk, nm in KOSPI_UNIVERSE + BNF_EXTRA:
        if tk in seen:
            continue
        seen.add(tk)
        uni.append((tk, nm))
    print(f'유니버스: KOSPI 대형/중형주 {len(uni)}종목')
    return uni


# ── 데이터 수집 ───────────────────────────────────────────────
def get_ohlcv(ticker: str) -> pd.DataFrame:
    """일봉 OHLCV — 최근 DATA_DAYS일. 전략 모듈용 소문자 컬럼으로 반환."""
    today  = datetime.today().strftime('%Y%m%d')
    from_d = (datetime.today() - timedelta(days=DATA_DAYS)).strftime('%Y%m%d')
    df = krx.get_market_ohlcv_by_date(from_d, today, ticker)
    if df.empty:
        return df
    df = df.rename(columns={
        '시가': 'open', '고가': 'high',
        '저가': 'low',  '종가': 'close', '거래량': 'volume'
    })
    # 전략 모듈이 요구하는 컬럼만 추출
    cols = ['open', 'high', 'low', 'close', 'volume']
    df = df[[c for c in cols if c in df.columns]]
    return df.dropna()


def is_today_data(df: pd.DataFrame) -> bool:
    """가장 최근 거래일 데이터인지 확인 (휴장일/지연 체크)"""
    if df.empty:
        return False
    last_date = pd.Timestamp(df.index[-1]).date()
    today     = datetime.today().date()
    return (today - last_date).days <= 3


# ── 종목 시가총액 기준 대형/중형 구분 ─────────────────────────
def classify_cap(close: float) -> str:
    """
    이격도 임계값 선택용 대/중소형 구분.
    BNF 유니버스는 대부분 대형주이므로 보수적으로 'large' 기본,
    저가(주가 1만원 미만) 변동성 큰 종목만 'small'로 완화 적용.
    """
    return 'large' if close >= 10000 else 'small'


# ── 매수 신호 검사 ────────────────────────────────────────────
def check_signal(df: pd.DataFrame) -> dict:
    """전략 모듈 entry_signal 을 마지막 봉(i=-1)에 적용"""
    if len(df) < MIN_BARS:
        return {'signal': False, 'reason': '데이터 부족'}

    ind = add_indicators(df, CFG)
    i   = len(ind) - 1          # 마지막(오늘) 봉
    row = ind.iloc[i]

    # 지표 NaN 방어
    if any(pd.isna(row[c]) for c in ['ema25', 'disparity', 'rsi', 'macd_hist', 'ret_n']):
        return {'signal': False, 'reason': '지표 미계산'}

    cap = classify_cap(float(row['close']))
    triggered, reasons = entry_signal(ind, i, CFG, regime='neutral', cap=cap)

    if not triggered:
        return {'signal': False, 'reason': '진입조건 미충족', 'reasons': reasons}

    # 바닥패턴 보조조건 (참고용 — 진입 강제조건 아님)
    pattern = bottom_pattern(ind, i, CFG)

    entry     = int(round(float(row['close'])))
    stop_loss = int(round(entry * (1 + CFG.stop_loss_pct / 100)))   # -5%
    thr       = disparity_threshold(CFG, 'neutral', cap)

    # 포지션 사이징: 계좌 × 리스크% ÷ 1주당 손실액
    qty, invest, max_loss = 0, 0, 0
    if BNF_ACCOUNT > 0:
        loss_per_share = entry - stop_loss
        allowed_loss   = BNF_ACCOUNT * BNF_RISK_PCT / 100
        qty            = int(allowed_loss / loss_per_share) if loss_per_share > 0 else 0
        invest         = qty * entry
        max_loss       = qty * loss_per_share

    return {
        'signal'    : True,
        'entry'     : entry,
        'stop'      : stop_loss,
        'disparity' : round(float(row['disparity']), 1),
        'disp_thr'  : thr,
        'rsi'       : round(float(row['rsi']), 1),
        'ret5'      : round(float(row['ret_n']), 1),
        'cap'       : cap,
        'pattern'   : sum(1 for v in pattern.values() if v),   # 충족 보조조건 수(0~4)
        'qty'       : qty,
        'invest'    : invest,
        'max_loss'  : max_loss,
    }


# ── Slack 전송 ────────────────────────────────────────────────
def send_slack(signals: list):
    today_str = datetime.today().strftime('%Y.%m.%d (%a)')

    if not signals:
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": f"BNF 역추세 스캔 {today_str}"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": "BNF 신호 없음 — 대기\n_급락(-8%) + 이격도(-20%) + RSI(30↓) + MACD 0선돌파 조건을 모두 충족하는 종목 없음_"}}
        ]
    else:
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"BNF 역추세 매수 신호 {today_str} — {len(signals)}종목"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": "*조건: 급락(-8%) + 이격도(-20%) + RSI(30↓) + MACD 0선 돌파*\n"
                              ">⚠️ 역추세(반등) 단기매매. 손절 -5% 철저히 준수. 다음날 시가 확인 후 진입."}},
            {"type": "divider"},
        ]
        for s in signals:
            qty_text = (f"매수수량  *{s['qty']}주*  "
                        f"(투입 {s['invest']//10000}만원 / 손절시 -{s['max_loss']//10000}만원)"
                        if s.get('qty') else "")
            fields = [
                {"type": "mrkdwn", "text": f"*{s['name']}* (`{s['ticker']}`)"},
                {"type": "mrkdwn", "text": f"현재가  *{s['entry']:,}원*"},
                {"type": "mrkdwn", "text": f"진입가  *{s['entry']:,}원*"},
                {"type": "mrkdwn", "text": f"손절가  {s['stop']:,}원  (*-5%*)"},
                {"type": "mrkdwn",
                 "text": f"이격도  *{s['disparity']}%*  (기준 {s['disp_thr']}%)"},
                {"type": "mrkdwn", "text": f"RSI  *{s['rsi']}*   5일 {s['ret5']}%"},
            ]
            if qty_text:
                fields.append({"type": "mrkdwn", "text": qty_text})
            blocks.append({"type": "section", "fields": fields})
            blocks.append({"type": "divider"})

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_BNF 평균회귀 베팅. 1회 리스크 = 계좌의 2% 이하. "
                             "익절은 모멘텀 약화(MACD↓+거래량↓) 시 / 하락장 +5% 빠른 익절._"}
        })

    # 콘솔 출력 (항상)
    for b in blocks:
        if b['type'] == 'header':
            print(f"\n{'='*60}")
            print(f"  {b['text']['text']}")
            print(f"{'='*60}")
        elif b['type'] == 'section':
            txt = b.get('text', {}).get('text', '')
            if txt:
                print(txt.replace('*', '').replace('_', '').replace('`', ''))
            for f in b.get('fields', []):
                print(f"  {f['text'].replace('*','').replace('`','')}")

    # Slack 전송
    if not SLACK_URL or SLACK_URL.startswith('여기에') or SLACK_URL == '':
        print('\n[Slack 미설정] config.json의 slack_webhook_url_bnf 또는 slack_webhook_url 확인.')
        return

    payload = json.dumps({'text': f'BNF신호 {today_str}', 'blocks': blocks},
                         ensure_ascii=False)
    try:
        r = requests.post(SLACK_URL, data=payload.encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, timeout=15)
        print(f'\nSlack 전송: {"성공" if r.text == "ok" else f"실패({r.text})"}')
    except Exception as e:
        print(f'\nSlack 전송 오류: {e}')


# ── 메인 ─────────────────────────────────────────────────────
def main():
    print(f'\n{"="*60}')
    print(f'  BNF 역추세(평균회귀) 한국주식 스캔  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  급락 + 이격도 + RSI과매도 + MACD 0선돌파')
    print(f'{"="*60}\n')

    universe = get_universe()
    if not universe:
        print('유니버스 조회 실패 — 종료')
        return

    signals = []
    skipped = 0

    for ticker, name in universe:
        try:
            df = get_ohlcv(ticker)

            if not is_today_data(df):
                skipped += 1
                continue

            if len(df) < MIN_BARS:
                continue

            result = check_signal(df)
            if result['signal']:
                result['ticker'] = ticker
                result['name']   = name
                signals.append(result)
                print(f'  ★ 신호  {name}({ticker})'
                      f'  현재 {result["entry"]:,}  손절 {result["stop"]:,}'
                      f'  이격도 {result["disparity"]}%  RSI {result["rsi"]}'
                      f'  (보조패턴 {result["pattern"]}/4)')

        except Exception as e:
            print(f'  오류: {name}({ticker}) — {e}')

    print(f'\n스캔 완료: {len(universe)}종목 중 신호 {len(signals)}건'
          + (f' (휴장/데이터없음 {skipped}건 제외)' if skipped else ''))

    # 신호를 파일로 저장 → 다음날 아침 진입 확인에 사용 (주식정보 폴더 루트)
    sig_path = os.path.join(_BASE_DIR, '..', 'bnf_signals.json')
    save_data = {
        'date': datetime.today().strftime('%Y-%m-%d'),
        'signals': [
            {k: v for k, v in s.items() if k != 'signal'}
            for s in signals
        ]
    }
    with open(sig_path, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f'신호 저장 완료: {os.path.abspath(sig_path)}')

    send_slack(signals)


if __name__ == '__main__':
    main()
