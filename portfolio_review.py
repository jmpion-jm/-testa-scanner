# -*- coding: utf-8 -*-
import sys, io, warnings, importlib.util
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
warnings.filterwarnings('ignore')

spec = importlib.util.spec_from_file_location('sa', 'slack_alert.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print('포트폴리오 읽는 중...')
holdings = m.read_portfolio()
print(f'보유종목 {len(holdings)}개 로드')

print('MA10 스캔 중...\n')
rows = m.scan_portfolio(holdings)

broke = [r for r in rows if r['broke']]
fresh = [r for r in rows if r['fresh']]
above = [r for r in rows if r['above'] and not r['broke'] and not r['fresh']]
below = [r for r in rows if not r['above']]

W = 62
print('=' * W)
print('  보유종목 월봉 MA10 포트폴리오 검토')
print('=' * W)

if broke:
    print('\n  🚨 MA10 이탈경보 — 보유 주의')
    print('  ' + '-' * (W-2))
    for r in broke:
        accs = ', '.join(r['accounts'])
        print(f'  xx {r["name"]:<22} {r["pct"]:>+6.1f}%  [{accs}]')

if fresh:
    print('\n  ★ MA10 신규 돌파 — 추가매수 검토')
    print('  ' + '-' * (W-2))
    for r in fresh:
        accs = ', '.join(r['accounts'])
        print(f'  ** {r["name"]:<22} {r["pct"]:>+6.1f}%  [{accs}]')

if above:
    print('\n  ● 홀딩 유지 — MA10 위')
    print('  ' + '-' * (W-2))
    for r in sorted(above, key=lambda x: x['pct'], reverse=True):
        accs = ', '.join(r['accounts'])
        tag = '고점' if r['pct'] > 15 else ('눌림' if r['pct'] < 5 else '추세')
        print(f'  {tag} {r["name"]:<22} {r["pct"]:>+6.1f}%  [{accs}]')

if below:
    print('\n  ❌ MA10 아래 — 매수 금지')
    print('  ' + '-' * (W-2))
    for r in below:
        accs = ', '.join(r['accounts'])
        print(f'  -- {r["name"]:<22} {r["pct"]:>+6.1f}%  [{accs}]')

print()
print(f'  요약: MA10위 {len(fresh)+len(above)}개 | 이탈경보 {len(broke)}개 | MA10아래 {len(below)}개')
print('=' * W)

print()
print('  [계좌별 현황]')
acct_map = {}
for r in rows:
    for acc in r['accounts']:
        acct_map.setdefault(acc, []).append(r)
for acc, items in sorted(acct_map.items()):
    cnt_a = sum(1 for r in items if r['above'])
    cnt_b = sum(1 for r in items if not r['above'])
    names = ', '.join(r['name'][:7] for r in items)
    print(f'  {acc:<10} {len(items)}종목 (위 {cnt_a}/아래 {cnt_b}) : {names}')
