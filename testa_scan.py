# -*- coding: utf-8 -*-
"""
테스타 매매법 — 한국 주식 일봉 스캔
KOSPI 시총 상위 약 100종목 대상
조건: 정배열(MA5>MA25>MA75) + 거래량 급등 + 눌림목 돌파
실행: 매일 장 마감 후 자동 (GitHub Actions) 또는 직접 실행
"""
import os, sys, io, json, requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    from pykrx import stock as krx
except ImportError:
    print("pykrx 미설치: pip install pykrx")
    sys.exit(1)

# ── 설정 ─────────────────────────────────────────────────────
# 슬랙 URL: config.json 우선, 없으면 환경변수
_cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
try:
    with open(_cfg_path, encoding='utf-8') as _f:
        _cfg = json.load(_f)
    SLACK_URL    = _cfg.get('slack_webhook_url', '')
    TESTA_ACCOUNT  = int(_cfg.get('testa_account', 0))
    TESTA_RISK_PCT = float(_cfg.get('testa_risk_pct', 1.0))
except Exception:
    SLACK_URL      = os.environ.get('SLACK_WEBHOOK_URL', '')
    TESTA_ACCOUNT  = 0
    TESTA_RISK_PCT = 1.0
MA_SHORT       = 5
MA_MID         = 25
MA_LONG        = 75
VOLUME_MULT    = 1.5    # 거래량 급등 기준 (20일 평균 대비)
MAX_RISK_PCT   = 15.0   # 손절폭 상한 — 15% 초과 신호 제외 (백테스트 검증)
UNIVERSE_SIZE  = 100    # KOSPI 시총 상위 N종목
DATA_DAYS      = 200    # 데이터 조회 기간 (75일선 계산 + 여유분)


# ── 유니버스: KOSPI 시총 상위 약 100 (고정 목록) ─────────────
# pykrx API 변경에 영향받지 않도록 직접 지정
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
    # 51~100
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

def get_universe() -> list[tuple[str, str]]:
    """KOSPI 대형주 목록 반환"""
    print(f'유니버스: KOSPI 대형주 {len(KOSPI_UNIVERSE)}종목 (고정)')
    return KOSPI_UNIVERSE


# ── 데이터 수집 ───────────────────────────────────────────────
def get_ohlcv(ticker: str) -> pd.DataFrame:
    """일봉 OHLCV — 최근 DATA_DAYS일"""
    today  = datetime.today().strftime('%Y%m%d')
    from_d = (datetime.today() - timedelta(days=DATA_DAYS)).strftime('%Y%m%d')
    df = krx.get_market_ohlcv_by_date(from_d, today, ticker)
    if df.empty:
        return df
    df = df.rename(columns={
        '시가': 'Open', '고가': 'High',
        '저가': 'Low',  '종가': 'Close', '거래량': 'Volume'
    })
    return df.dropna()


def is_today_data(df: pd.DataFrame) -> bool:
    """오늘 또는 가장 최근 거래일 데이터인지 확인 (휴장일 체크)"""
    if df.empty:
        return False
    last_date = pd.Timestamp(df.index[-1]).date()
    today     = datetime.today().date()
    # 오늘 포함 최근 3거래일 이내면 유효
    return (today - last_date).days <= 3


# ── 이동평균 + 추세 판단 ──────────────────────────────────────
def add_ma(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['MA5']  = df['Close'].rolling(MA_SHORT).mean()
    df['MA25'] = df['Close'].rolling(MA_MID).mean()
    df['MA75'] = df['Close'].rolling(MA_LONG).mean()
    return df


def get_slope(series: pd.Series, window: int = 5) -> float:
    """선형회귀 기울기 (정규화)"""
    y = series.dropna().tail(window).values
    if len(y) < window:
        return 0.0
    x = np.arange(len(y))
    slope = np.polyfit(x, y, 1)[0]
    return slope / y.mean()


def judge_trend(df: pd.DataFrame) -> str:
    """'BULL' | 'BEAR' | 'SIDEWAYS'"""
    last = df.iloc[-1]
    ma5, ma25, ma75 = last['MA5'], last['MA25'], last['MA75']
    if any(pd.isna(v) for v in [ma5, ma25, ma75]):
        return 'UNKNOWN'

    s5  = get_slope(df['MA5'])
    s25 = get_slope(df['MA25'])
    s75 = get_slope(df['MA75'])

    if ma5 > ma25 > ma75 and s5 > 0 and s25 > 0 and s75 > 0:
        return 'BULL'
    if ma75 > ma25 > ma5 and s5 < 0 and s25 < 0 and s75 < 0:
        return 'BEAR'
    return 'SIDEWAYS'


# ── 매수 신호: 눌림목 돌파 ────────────────────────────────────
def check_signal(df: pd.DataFrame) -> dict:
    if len(df) < MA_LONG + 5:
        return {'signal': False, 'reason': '데이터 부족'}

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # 75일선 이탈 = 추세 붕괴
    if curr['Close'] < curr['MA75']:
        return {'signal': False, 'reason': '75일선 이탈'}

    # 눌림목 조건: 전봉 종가 < MA5, 현봉 종가 > MA5
    dipped   = prev['Close'] < prev['MA5']
    breakout = curr['Close'] > curr['MA5']

    if dipped and breakout:
        # 직전 2봉 저점: 갭업 이전 저가가 포함되는 5봉 방식 대신 눌림목 구간만 사용
        stop_loss  = df['Low'].iloc[-3:-1].min()
        risk_pct   = (curr['Close'] - stop_loss) / curr['Close'] * 100

        # 손절폭 15% 초과 시 제외 (EV 검증 결과: 15% 초과는 승률 0%, EV -22.9%)
        if risk_pct > MAX_RISK_PCT:
            return {'signal': False, 'reason': f'손절폭 과다 ({risk_pct:.1f}% > {MAX_RISK_PCT}%)'}

        target1    = curr['Close'] * (1 + risk_pct / 100 * 3)   # 손익비 3:1

        # 포지션 사이징: 계좌 × 리스크% ÷ 1주당 손실액
        qty, invest, max_loss = 0, 0, 0
        if TESTA_ACCOUNT > 0:
            loss_per_share = float(curr['Close']) - float(stop_loss)
            allowed_loss   = TESTA_ACCOUNT * TESTA_RISK_PCT / 100
            qty            = int(allowed_loss / loss_per_share) if loss_per_share > 0 else 0
            invest         = qty * int(curr['Close'])
            max_loss       = qty * int(loss_per_share)

        return {
            'signal'    : True,
            'entry'     : int(curr['Close']),
            'ma5'       : int(curr['MA5']),
            'stop'      : int(stop_loss),
            'target'    : int(target1),
            'risk_pct'  : round(risk_pct, 1),
            'reward_pct': round(risk_pct * 3, 1),
            'qty'       : qty,
            'invest'    : invest,
            'max_loss'  : max_loss,
        }
    return {'signal': False, 'reason': '눌림목 조건 미충족'}


# ── 수급(거래량) 필터 ─────────────────────────────────────────
def check_volume(df: pd.DataFrame) -> bool:
    """오늘 거래량 >= 20일 평균 × VOLUME_MULT"""
    if len(df) < 22:
        return False
    avg_vol   = df['Volume'].iloc[-21:-1].mean()
    today_vol = df['Volume'].iloc[-1]
    return bool(today_vol >= avg_vol * VOLUME_MULT)


# ── Slack 전송 ────────────────────────────────────────────────
def send_slack(signals: list):
    today_str = datetime.today().strftime('%Y.%m.%d (%a)')

    if not signals:
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text", "text": f"테스타 스캔 {today_str}"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": "오늘 매수 신호 없음 — 현금 대기\n_정배열 + 거래량 + 눌림목 조건을 모두 충족하는 종목 없음_"}}
        ]
    else:
        blocks = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": f"★ 테스타 매수 신호 {today_str} — {len(signals)}종목"}},
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": "*조건: 정배열(5>25>75일) + 거래량급등 + 눌림목돌파*\n"
                              ">⚠️ 다음날 시가 확인 후 진입 결정. 손절가 반드시 확인."}},
            {"type": "divider"},
        ]
        for s in signals:
            qty_text = (f"매수수량  *{s['qty']}주*  "
                        f"(투입 {s['invest']//10000}만원 / 손절시 -{s['max_loss']//10000}만원)"
                        if s.get('qty') else "")
            fields = [
                {"type": "mrkdwn", "text": f"*{s['name']}* (`{s['ticker']}`)"},
                {"type": "mrkdwn", "text": f"진입가  *{s['entry']:,}원*"},
                {"type": "mrkdwn", "text": f"손절가  {s['stop']:,}원  (*-{s['risk_pct']}%*)"},
                {"type": "mrkdwn", "text": f"목표가  {s['target']:,}원  (*+{s['reward_pct']}%*)"},
            ]
            if qty_text:
                fields.append({"type": "mrkdwn", "text": qty_text})
            blocks.append({"type": "section", "fields": fields})
            blocks.append({"type": "divider"})

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_손익비 3:1 기준. 1회 리스크 = 계좌의 1~2% 이하로 제한_"}
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
        print('\n[Slack 미설정] SLACK_WEBHOOK_URL 환경변수를 설정하세요.')
        return

    payload = json.dumps({'text': f'테스타신호 {today_str}', 'blocks': blocks},
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
    print(f'  테스타 한국주식 스캔  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print(f'  KOSPI 시총 상위 {UNIVERSE_SIZE}종목 | MA{MA_SHORT}/{MA_MID}/{MA_LONG} | 눌림목 돌파')
    print(f'{"="*60}\n')

    universe = get_universe()
    if not universe:
        print('유니버스 조회 실패 — 종료')
        return

    signals  = []
    skipped  = 0

    for ticker, name in universe:
        try:
            df = get_ohlcv(ticker)

            if not is_today_data(df):
                skipped += 1
                continue

            if len(df) < MA_LONG + 5:
                continue

            df = add_ma(df)

            trend = judge_trend(df)
            if trend != 'BULL':
                continue

            if not check_volume(df):
                continue

            result = check_signal(df)
            if result['signal']:
                result['ticker'] = ticker
                result['name']   = name
                signals.append(result)
                print(f'  ★ 신호  {name}({ticker})'
                      f'  진입 {result["entry"]:,}  손절 {result["stop"]:,}'
                      f'  목표 {result["target"]:,}  (리스크 -{result["risk_pct"]}%)')

        except Exception as e:
            print(f'  오류: {name}({ticker}) — {e}')

    print(f'\n스캔 완료: {len(universe)}종목 중 신호 {len(signals)}건'
          + (f' (휴장/데이터없음 {skipped}건 제외)' if skipped else ''))

    # 신호를 파일로 저장 → 다음날 아침 진입 확인에 사용
    sig_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'testa_signals.json')
    save_data = {
        'date': datetime.today().strftime('%Y-%m-%d'),
        'signals': [
            {k: v for k, v in s.items() if k != 'signal'}
            for s in signals
        ]
    }
    with open(sig_path, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f'신호 저장 완료: {sig_path}')

    send_slack(signals)


if __name__ == '__main__':
    main()
