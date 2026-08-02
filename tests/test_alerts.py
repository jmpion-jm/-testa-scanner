# -*- coding: utf-8 -*-
"""
알림 시스템 회귀 테스트 — 2026-08-01 실제로 발생했던 두 버그를 재현/검증.
main에 push될 때마다 GitHub Actions에서 자동 실행됨 (.github/workflows/tests.yml).

python-경로에 이 파일이 있는 디렉터리(tests/)의 부모를 추가해서
프로젝트 루트의 slack_alert.py / sp500_scan.py를 import한다.
"""
import sys, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name} — {detail}")
        failures.append(name)


# ══════════════════════════════════════════════════════════════
# 1. send_slack() 50블록 초과 시 자동 분할 전송 검증
#    (2026-08-01: 54종목 스캔 결과가 53블록을 생성 → Slack이 400으로
#     거부했는데 예외 처리가 조용히 삼켜서 아무도 몰랐던 버그)
# ══════════════════════════════════════════════════════════════
def test_send_slack_chunking():
    print("\n[1] send_slack() 50블록 초과 분할 전송 검증")
    import slack_alert as sa

    sent_chunks = []

    def fake_send_raw(blocks, text, target):
        sent_chunks.append(blocks)
        return True

    original = sa._send_slack_raw
    sa._send_slack_raw = fake_send_raw
    try:
        # 120개짜리 더미 블록 (실제 슬랙에 안 나감 — _send_slack_raw를 가짜로 교체함)
        dummy_blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"item {i}"}}
                        for i in range(120)]
        ok = sa.send_slack(dummy_blocks, text="테스트", url="https://example.com/fake-webhook")

        check("send_slack()가 True(성공) 반환", ok is True, f"실제 반환값={ok}")
        check("50개 초과 시 여러 메시지로 분할됨", len(sent_chunks) == 3,
              f"실제 분할 개수={len(sent_chunks)} (기대: 3 = 50+50+20)")
        if len(sent_chunks) == 3:
            check("각 청크가 50블록 이하", all(len(c) <= 50 for c in sent_chunks),
                  f"청크별 크기={[len(c) for c in sent_chunks]}")
            check("전체 블록 수 보존 (정보 손실 없음)",
                  sum(len(c) for c in sent_chunks) == 120,
                  f"합계={sum(len(c) for c in sent_chunks)} (기대: 120)")

        # 50개 이하는 그대로 1번만 전송돼야 함 (불필요한 분할 방지)
        sent_chunks.clear()
        small_blocks = dummy_blocks[:30]
        sa.send_slack(small_blocks, text="테스트", url="https://example.com/fake-webhook")
        check("50블록 이하는 분할 없이 1번만 전송", len(sent_chunks) == 1,
              f"실제 전송 횟수={len(sent_chunks)}")
    finally:
        sa._send_slack_raw = original


# ══════════════════════════════════════════════════════════════
# 2. S&P500 티커 목록 실제로 가져와지는지 검증
#    (2026-08-01: requirements.txt에 lxml 누락 → pd.read_html 실패
#     → sys.exit(1)로 매번 조용히 실패했던 버그)
# ══════════════════════════════════════════════════════════════
def test_sp500_ticker_fetch():
    print("\n[2] S&P500 티커 목록 조회 검증 (실제 네트워크 호출)")
    import sp500_scan as sp

    tickers = sp.get_sp500_tickers()
    check("S&P500 티커 400개 이상 조회됨", len(tickers) >= 400,
          f"실제 조회 개수={len(tickers)} (0이면 lxml 등 파서 누락 의심)")
    check("중복 티커 없음", len(tickers) == len(set(tickers)),
          f"전체={len(tickers)}, 고유={len(set(tickers))}")


if __name__ == "__main__":
    print("=" * 60)
    print("  알림 시스템 회귀 테스트")
    print("=" * 60)

    test_send_slack_chunking()
    test_sp500_ticker_fetch()

    print("\n" + "=" * 60)
    if failures:
        print(f"  실패 {len(failures)}건: {', '.join(failures)}")
        print("=" * 60)
        sys.exit(1)
    else:
        print("  전체 통과")
        print("=" * 60)
