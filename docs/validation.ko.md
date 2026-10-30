# 검증 기록

실행일: 2026-10-30, Linux, Python 3.13.13, Codex CLI 0.166.0.

| 검증 | 결과 |
|---|---|
| editable 패키지 설치 | 성공 |
| 실제 App Server initialize / thread/start / thread/read | 성공, 모델 호출 없음 |
| 실제 독립 App Server A/B 및 서로 다른 thread | 성공 |
| B→A hello, A→B hello received 및 B 컨텍스트 반영 | 성공 |
| A/B 개별 무작위 표식 재호출, 상대 표식 미포함 | 성공 |
| A→B hello, B→A hello received 및 A 컨텍스트 반영 | 성공 |
| 결정적 프로토콜 테스트 4개 | 성공 |

단위 테스트는 turn/start 응답보다 먼저 도착하는 notification, 다른 thread/turn의 이벤트 제외,
실패한 turn, JSON-RPC 오류, subprocess 연결 종료를 다룬다. 모델을 흉내 내는 fixture와
실제 모델을 사용한 demo 결과를 구분한다.

실제 demo의 최종 출력:

```json
{
  "type": "verification",
  "sender": "bridge",
  "receiver": "user",
  "distinct_threads": true,
  "context_retained": true,
  "bidirectional": true,
  "network": "local stdio",
  "autonomous_tool_selection": false
}
```

이 결과는 두 개의 **새 테스트 대화**에 대한 로컬 검증이다.
기존 실행 중인 TUI thread 주입, 독립 서버/NAT 통신, 모델의 자율적인 tool 선택,
파일 목록 조사·파일 전송, peer 인증, reconnect는 검증하지 않았다.
PoC의 메시지 왕복을 프로그램이 중계하므로 완성된 A2A MVP로 판정하지 않는다.

원본 실행 로그는 Git에서 제외한 `.runtime/`에 보관한다. 재현:

```bash
python3 -m unittest discover -s tests -v
python3 -m agent_bridge probe
python3 -m agent_bridge demo
```

GitHub CI는 모델을 호출하지 않는 프로토콜 테스트만 실행한다.
