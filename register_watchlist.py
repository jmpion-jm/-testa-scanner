# -*- coding: utf-8 -*-
"""
슬랙 알림 미국 종목 → 영웅문 관심종목 자동 등록

첫 실행 (위치 설정):
    python register_watchlist.py --setup

이후 자동 등록:
    python register_watchlist.py

등록 대상 (신규돌파 + 지지권만):
  - us_signals.json      ← slack_alert.py 가 저장 (김학주 추천)
  - ndx100_signals.json  ← nasdaq100_scan.py 가 저장
  - sp500_signals.json   ← sp500_scan.py 가 저장
"""
import sys, io, os, json, time, argparse
from datetime import date, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    import pyautogui
    import pygetwindow as gw
except ImportError:
    print('패키지 설치 필요: pip install pyautogui pygetwindow')
    sys.exit(1)

BASE     = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE, 'watchlist_config.json')

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.4


# ── 설정 ──────────────────────────────────────────────────────
def load_cfg():
    if os.path.exists(CFG_FILE):
        with open(CFG_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_cfg(cfg):
    with open(CFG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── 신호 종목 수집 ────────────────────────────────────────────
def load_us_signals() -> list:
    """슬랙 알림 미국 종목 수집 (신규돌파 + 지지권)"""
    today   = date.today()
    stocks  = []
    seen    = set()

    sources = [
        ('us_signals.json',     35, lambda s: True,              'MA10'),
        ('ndx100_signals.json', 35, lambda s: s.get('priority', 9) <= 2, 'NDX100'),
        ('sp500_signals.json',  35, lambda s: s.get('priority', 9) <= 2, 'SP500'),
    ]

    for fname, max_days, filt, label in sources:
        path = os.path.join(BASE, fname)
        if not os.path.exists(path):
            continue
        try:
            data     = json.load(open(path, encoding='utf-8'))
            sig_date = datetime.strptime(data.get('date', '2000-01-01'), '%Y-%m-%d').date()
            if (today - sig_date).days > max_days:
                continue
            for s in data.get('signals', []):
                if filt(s) and s['ticker'] not in seen:
                    seen.add(s['ticker'])
                    stocks.append({
                        'ticker': s['ticker'],
                        'name':   s.get('name', s['ticker']),
                        'pct':    s.get('pct', 0),
                        'src':    label,
                    })
        except Exception as e:
            print(f'  {fname} 읽기 오류: {e}')

    return stocks


# ── 위치 설정 마법사 ──────────────────────────────────────────
def setup_wizard():
    print('=' * 50)
    print('  영웅문 미국 종목 입력창 위치 설정')
    print('=' * 50)
    print()
    print('준비사항:')
    print('  1. 영웅문을 열고 해외주식 관심종목 패널 열기')
    print('  2. 미국 신호 종목을 넣을 그룹 선택')
    print('     (예: "미국신호" 그룹 미리 만들어두기)')
    print()
    print('종목코드 입력창 위에 마우스를 올려두세요.')
    input('준비되면 Enter ▶ ')

    for i in range(3, 0, -1):
        print(f'  {i}초 후 저장...', end='\r')
        time.sleep(1)

    x, y = pyautogui.position()
    cfg = load_cfg()
    cfg['us_input_x'] = x
    cfg['us_input_y'] = y
    save_cfg(cfg)
    print(f'  저장완료: ({x}, {y})          ')
    print()
    print('설정 완료! 이제 python register_watchlist.py 로 자동 등록하세요.')


# ── 영웅문에 등록 ─────────────────────────────────────────────
def register_to_hts(stocks: list, x: int, y: int):
    win = None
    for title in gw.getAllTitles():
        if '영웅문' in title:
            wins = gw.getWindowsWithTitle(title)
            if wins:
                win = wins[0]
                break

    if not win:
        print('  영웅문 창을 찾지 못했습니다.')
        return

    win.activate()
    time.sleep(1.0)

    for s in stocks:
        ticker = s['ticker']
        pyautogui.click(x, y)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        pyautogui.write(ticker, interval=0.06)
        time.sleep(0.2)
        pyautogui.press('enter')
        time.sleep(0.6)
        print(f'    ✅ {ticker:6}  {s["name"]}  ({s["pct"]:+.1f}%)  [{s["src"]}]')


# ── 메인 ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', action='store_true')
    args = parser.parse_args()

    if args.setup:
        setup_wizard()
        return

    cfg = load_cfg()
    if 'us_input_x' not in cfg:
        print('처음 실행 시 위치 설정이 필요합니다:')
        print('  python register_watchlist.py --setup')
        return

    print(f'\n영웅문 미국 관심종목 자동 등록  {datetime.now().strftime("%Y-%m-%d %H:%M")}')
    print('=' * 50)

    stocks = load_us_signals()

    if not stocks:
        print('등록할 미국 신호 종목이 없습니다.')
        return

    print(f'\n등록 대상 {len(stocks)}종목:')
    for s in stocks:
        print(f'  {s["ticker"]:6}  {s["name"]}  {s["pct"]:+.1f}%  [{s["src"]}]')

    print()
    input('영웅문 미국 관심종목 그룹을 열고 Enter ▶ ')

    register_to_hts(stocks, cfg['us_input_x'], cfg['us_input_y'])
    print('\n등록 완료!')


if __name__ == '__main__':
    main()
