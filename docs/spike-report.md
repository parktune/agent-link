# M0 조사 보고서

조사일: 2026-11-02. 대상: Claude Code 2.1.316, Codex CLI 0.166.0, MCP Python SDK 1.30.0, Ubuntu.
이번 조사는 **정적 조사**다. 공식 문서, `--help`, 스키마 생성 명령, 설치된 바이너리의 문자열을 확인했고 모델을 호출하지 않았다.
모델이 필요한 확인은 [수동 시나리오](manual-scenarios.md)의 M0 항목으로 남긴다.

판정: **확인**은 문서·help·스키마·자동 테스트로 확인한 것, **미검증**은 실제 세션에서 확인이 필요한 것이다.

## Claude Channels (11장, 15장 2번)

| 항목 | 판정 | 근거와 결과 |
|---|---|---|
| channel 계약 | 확인 | `capabilities.experimental['claude/channel'] = {}` 선언, `notifications/claude/channel` 알림, params `content`(문자열)와 `meta`(문자열 맵). meta 키는 영문자·숫자·밑줄만 허용하고 나머지 키는 조용히 버린다. 공식 문서 channels-reference |
| 구현 언어 | 확인 | 문서는 TypeScript SDK를 예로 들지만 계약은 MCP stdio 프로토콜이다. Python 저수준 서버로 같은 알림을 보내는 것을 실제 stdio 테스트로 확인했다(`tests/test_adapter.py`) |
| 커스텀 channel 로드 | 확인(문서) / 미검증(실행) | research preview 동안 커스텀 channel은 allowlist에 없어서 `claude --dangerously-load-development-channels server:<이름>`이 필요하다. 시작할 때 경고 대화상자가 뜬다. `-p`나 Agent SDK에서는 무시된다. 설치 바이너리에 플래그 문자열이 있다 |
| 미로드 시 동작 | 확인(문서) | 세션이 channel로 로드하지 않았거나 조직 정책이 막으면 **알림을 조용히 버리고 오류를 주지 않는다**. 그래서 어댑터는 상위 프로세스 명령행에서 플래그를 읽고, 없으면 박스 저장으로 전환한다 |
| 조직 정책 | 확인(문서) | Team/Enterprise는 관리자가 `channelsEnabled`를 켜야 한다. 플래그는 allowlist만 우회하고 이 정책은 우회하지 않는다 |
| `CLAUDE_CODE_SESSION_ID` | 미검증 | 바이너리에 문자열은 있다. stdio MCP 하위 프로세스에 주입되는지는 실제 세션에서 확인한다. 주입되면 세션 재시작 때 등록이 자동 복구되고, 없어도 기능에는 지장이 없다 |
| MCP 환경변수 전달 | 설계로 우회 | 세션 이름을 환경변수(`AGENT_HUB_NAME`)로 넘기지 않는다. `/agent-link:register`(또는 첫 `/agent-link`)를 쓸 때 등록하는 방식으로 바꿨다(ADR 0003) |
| 서버 이름과 플래그 | 미검증 | `claude mcp add --scope user agent-link`로 등록한 서버가 `server:agent-link`로 로드되는지 확인한다. 문서 예시는 `.mcp.json` 서버다 |

## Codex 수신 방식 (12장, 15장 1번)

| 항목 | 판정 | 근거와 결과 |
|---|---|---|
| app-server 메서드 | 확인 | `generate-json-schema --experimental` 결과 179개 메서드 중 `turn/start`, `turn/steer`(`expectedTurnId` 필수), `thread/queue/add`(`threadId`, `input`, `clientUserMessageId` 필수), `thread/loaded/list`, `thread/inject_items`가 있다. 모두 experimental이다 |
| 로컬 daemon | 확인 | `codex app-server daemon`이 사용자별 control socket(`~/.codex/app-server-control/app-server-control.sock`)을 둔다. WebSocket handshake가 필요한 unix socket이다 |
| `codex queue` | 확인(help) / 미검증(실행) | `codex queue --thread <UUID 또는 정확한 세션 이름> --message <TEXT> [--resume]`. 없는 세션이면 `No active session found matching ...`로 exit 1. **사람이 열어 둔 TUI에 들어가서 화면에 보이는지는 미검증**(12.3-2) |
| 세션 이름 | 확인 | Codex에는 시작할 때 이름을 지정하는 플래그가 없다. TUI의 `/rename`으로 이름을 붙이고 `session_index.jsonl`에 기록된다 |
| `CODEX_THREAD_ID` | 미검증 | 바이너리에 문자열이 있다. 셸 명령 환경에 들어가는 것으로 보인다. 스킬이 `printenv CODEX_THREAD_ID`로 읽고, 비어 있으면 `/rename` 이름을 쓴다 |
| 회신 방식 비교(12.3-3) | 결정 | Codex가 MCP 도구(`send_message`)로 직접 회신하는 방식을 택했다. 어댑터가 app-server 이벤트에서 최종 응답을 자동 회신하려면 세션 출력을 구독해야 하고, 사람이 보는 TUI와 충돌 가능성이 있어 MVP에서 제외했다 |

**결정:** Codex push는 `codex queue`(공식 CLI)로 한다. app-server JSON-RPC 직접 접속은 스키마가 버전마다 바뀌고 experimental이라, 같은 기능을 하는 공식 CLI 쪽이 버전 변화에 덜 민감하다.
push가 실패하거나 스레드를 모르면 박스에 저장한다. 이것이 명세 12.4 pull 폴백의 역할을 한다(ADR 0003).

## SSH endpoint 스트림 (8장)

자동 테스트와 `scripts/verify_ssh_loopback.py`로 확인했다. loopback 스크립트는 실제 OpenSSH와 임시 sshd를 쓰고 모델은 호출하지 않는다.

- 강제 명령 endpoint + JSON Lines 유지 세션으로 요청과 회신이 왕복한다.
- `agent-link hub add-host` 자동 흐름(DNS, TCP, SSH로 초대 발급, enroll, hello)이 실제 SSH에서 끝까지 진행된다.
- hub 재시작 후 outbox를 이어받고 중복이 없다(S7, S16). 한 호스트 장애가 다른 호스트에 영향을 주지 않는다(S6).
- **미측정:** 장시간 유지 시 방화벽 idle timeout, ProxyJump 경유 지연, 50대 부하(S8). M4 범위다.

## add-host (5장)

자동 흐름, 수동 흐름(`--code`), 5분 만료, 5회 오입력 폐쇄, 토큰 해시 저장, revoke 후 거부를 테스트로 확인했다.

## 명세와 달라진 점

| 명세 | 구현 | 이유 |
|---|---|---|
| 세션 이름은 실행 래퍼(`agent-run`)로 지정 | `agent-link:register`로 이름을 등록한다. 래퍼는 없다. 계정 라벨은 두지 않는다 | 사용자 결정(2026-11-02). MCP 하위 프로세스 환경변수 전달 문제도 함께 사라진다 |
| 오프라인 에이전트로 보내면 즉시 오류, 큐잉 없음 | 등록된 에이전트가 오프라인이거나 push가 안 되면 박스 폴더에 저장한다(`stored_offline`/`stored`). 호스트 자체에 연결할 수 없으면 오류다 | 사용자 결정. ADR 0003 |
| pull 폴백은 inbox + `receive` 도구 | `<repo>/hub` 또는 `<repo>/spoke` 아래 에이전트별 `received/` 폴더의 Markdown 파일 + `check_box` 도구 | 사용자 결정 |
| allowlist 프레임 | `ping`/`pong`(keepalive)을 추가했고, `ack`에 선택 필드 `result`를 둔다 | 장애 감지, 그리고 서버 세션에 라우팅 결과(오류 포함)를 돌려주기 위해서다 |
| `deliver` 응답 | `delivered {id, status}`이며 status는 `delivered`/`stored`/`stored_offline`이다 | 박스 저장을 구분하기 위해서다 |
| add-host 사전 조건: 원격 실행 경로를 사용자가 알고, daemon이 실행 중 | `agent-link hub add-host <alias>`가 SSH로 `agent-link`를 찾고, 꺼져 있으면 daemon을 systemd user 서비스(실패하면 백그라운드)로 띄운다. 로컬 hub도 같다. 패키지 설치는 여전히 하지 않는다(`setup.sh`를 사용자가 실행) | 사용자 요청(2026-11-02): alias만으로 연결 |
| 서버 세션의 `list_agents` | hub가 hub → host `directory` 프레임으로 다른 호스트의 에이전트(ID·상태·수신 방식)를 알려 주어 서버에서도 전체가 보인다(0.6.5). 기본 `allow_origination`도 `all`로 바꿨다 | 사용자 요청(2026-11-02): 서버 세션에서도 이름으로 요청 |
