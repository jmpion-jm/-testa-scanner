# -*- coding: utf-8 -*-
"""GitHub 비밀설정(CONFIG_JSON) 확인 — 값은 출력하지 않고 필요한 키가 있는지만 보고, 비공개 채널로 확인 메시지 전송.
수동 실행 전용(.github/workflows/config_check.yml). 2026-10-11 CONFIG_JSON 갱신 확인용으로 만듦."""
import json, sys, urllib.request

need = ['slack_webhook_url', 'slack_webhook_url_private', 'slack_webhook_url_pension', 'slack_webhook_url_tracker',
        'slack_webhook_url_ndx100', 'slack_webhook_url_sp500', 'slack_webhook_url_discovery', 'stocks', 'ma_period']
try:
    cfg = json.load(open('config.json', encoding='utf-8'))
except Exception as e:
    print(f'::error::config.json을 읽지 못함 — CONFIG_JSON 값이 비었거나 JSON 형식이 아님 ({type(e).__name__})')
    sys.exit(1)
missing = [k for k in need if not cfg.get(k)]
same = cfg.get('slack_webhook_url_private') and cfg.get('slack_webhook_url_private') == cfg.get('slack_webhook_url')
print(f"키 {len(cfg)}개 | 관심종목 {len(cfg.get('stocks', {}))}개 | GEV 포함 {'GEV' in cfg.get('stocks', {})}")
print('누락:', missing or '없음', '| 비공개=공유 채널 같음:', bool(same))
if missing or same:
    print('::error::설정 이상')
    sys.exit(1)
msg = (f"[GitHub 설정 확인] CONFIG_JSON 정상 — 필요 항목 {len(need)}개 모두 있음, 관심종목 {len(cfg['stocks'])}개"
       f"(GE버노바 {'포함' if 'GEV' in cfg['stocks'] else '없음'}). 이 채널은 지인 공유용 — 월말 종목 신호만 옵니다(보유종목·매매 내역은 안 옴).")
r = urllib.request.urlopen(urllib.request.Request(cfg['slack_webhook_url_private'], data=json.dumps({'text': msg}).encode('utf-8'),
                                                  headers={'Content-Type': 'application/json'}), timeout=20)
ok = r.read().decode() == 'ok'
print('비공개 채널 전송:', '성공' if ok else '실패')
sys.exit(0 if ok else 1)
