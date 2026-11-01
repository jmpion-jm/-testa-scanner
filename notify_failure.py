# -*- coding: utf-8 -*-
"""GitHub 작업 실패 알림 — 워크플로우 마지막 단계(if: failure())에서 실행, 사용자 채널로 경고.

2026-11-01: 10/11 CONFIG_JSON이 깨진 뒤 주간·월말 알림·통합 후보·매매 감지가 2주 넘게 실패했는데
아무도 몰랐다(10/31 월말 알림 미도착으로 발견). 실패하면 그날 바로 알게 한다.
config.json 자체가 깨져 웹후크를 못 읽으면 보낼 수 없다 — 그때는 GitHub 실패 표시·메일이 유일한 신호.
표준 라이브러리만 사용(패키지 설치 단계가 실패해도 돌도록).
"""
import json, os, sys, urllib.request

wf = os.environ.get('GITHUB_WORKFLOW', '(알 수 없는 작업)')
url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
       f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
try:
    hook = json.load(open('config.json', encoding='utf-8')).get('slack_webhook_url', '')
except Exception:
    hook = ''
if not hook:
    print('::error::config.json에서 사용자 채널 웹후크를 못 읽어 실패 알림을 보낼 수 없음 — GitHub CONFIG_JSON 확인')
    sys.exit(0)

msg = (f'⚠️ *자동 실행 실패* — {wf}\n'
       f'이번 알림이 오지 않았거나 일부가 빠졌을 수 있습니다. 매매 판단은 월말 종가 10이평 기준 그대로입니다.\n'
       f'Claude에게 "자동 실행 실패 알림 왔다"고 말해 주세요. 실행 기록: {url}')
try:
    req = urllib.request.Request(hook, data=json.dumps({'text': msg}).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        print('실패 알림 전송:', '완료' if r.read().decode() == 'ok' else '실패')
except Exception as e:
    print(f'실패 알림 전송 오류: {type(e).__name__}')
