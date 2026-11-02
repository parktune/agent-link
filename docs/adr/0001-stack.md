# ADR 0001: 구현 스택은 Python 3.11+

- 상태: 채택 (2026-11-02)

## 결정

hub, spoke daemon, endpoint, 세션 어댑터(MCP 서버)를 모두 Python 3.11+ 하나의 패키지(`agent_link`)로 구현한다.
런타임 의존성은 공식 MCP Python SDK(`mcp`) 하나다. 나머지는 표준 라이브러리(asyncio, sqlite3, tomllib)만 쓴다.

## 근거

| 기준 | Python | TypeScript (Node/Bun) |
|---|---|---|
| MCP SDK | 공식 SDK. 저수준 `Server`로 실험 capability 선언과 임의 알림 전송이 가능하다(테스트로 확인) | 공식 SDK. Channels 문서 예제가 이것이다 |
| Channels 호환 | 계약이 MCP 프로토콜 수준이라 언어와 무관하다 | 예제를 그대로 쓸 수 있다 |
| 서버 배포 | Ubuntu 기본 Python + venv. GPU 서버에 거의 항상 있다 | 서버마다 Node/Bun 런타임을 설치해야 한다 |
| 기존 자산 | 이전 agent-link의 pairing, peer token, SSH endpoint 경험을 그대로 옮길 수 있다 | 새로 작성해야 한다 |

## 결과

- 서버 설치는 `git clone` + `python3 -m venv .venv` + `pip install -e .`이다.
- Channels 예제 코드와 직접 호환되지는 않는다. 알림 형식은 `agent_link/inject.py`에 따로 구현했다.
