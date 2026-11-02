# 수동 시나리오

자동 테스트(`python -m unittest discover -s tests`)와 `scripts/verify_ssh_loopback.py`는 모델을 호출하지 않는다.
아래 항목은 실제 Claude Code·Codex 세션이 필요해서 사용자가 직접 확인한다. 결과는 표 아래 기록란에 남긴다.

## M0: 실제 세션 확인 (먼저)

| ID | 확인 | 방법 | 기대 |
|---|---|---|---|
| M0-1 | Claude channel 주입 | 로컬에서 `hubctl serve`를 실행한다. 터미널 A에서 `claude --dangerously-load-development-channels server:agent-link`를 열고 `/agent-link`로 `main` 등록, 터미널 B에서 일반 `claude`를 열고 `/agent-link`로 `other`를 등록한 뒤 `/agent-link main 안녕` | A 화면에 `← agent-link: 안녕`이 나타나고 A가 응답한다. `whoami`의 `push.available=true` |
| M0-2 | 플래그 없는 Claude | M0-1의 B 세션(플래그 없음)으로 보낸다 | `stored`, `<repo>/hub/claude-other/received/`에 파일이 생긴다 |
| M0-3 | `CLAUDE_CODE_SESSION_ID` | M0-1 세션에서 `/mcp`로 agent-link를 다시 연결한다 | 다시 묻지 않고 같은 이름으로 복구된다(주입될 때만) |
| M0-4 | Codex `codex queue` 주입 | Codex를 열고 `$agent-link`로 등록한다. 스킬이 `printenv CODEX_THREAD_ID`를 시도한다. 다른 세션에서 그 Codex로 보낸다 | Codex TUI에 `[agent-link] request ...` 메시지가 들어가 처리된다. 안 되면 `stored` + 박스 파일 |
| M0-5 | Codex 스레드 미상 | `CODEX_THREAD_ID`가 비어 있으면 `/rename trainer` 후 이름으로 등록한다 | M0-4와 같다 |
| M0-6 | 조직 정책 | Team/Enterprise 계정이면 M0-1에서 `blocked by org policy`가 나오는지 본다 | 나오면 관리자가 `channelsEnabled`를 켜야 한다. 그동안은 박스로 동작한다 |

## M2·M3·M4: 14장 시나리오

| ID | 시나리오 | 자동 테스트 | 수동 확인 |
|---|---|---|---|
| S1 | 로컬 Claude → 서버 Claude → 회신 | `test_s1_round_trip...`, loopback 스크립트 | 로컬 `main`에서 "gpu-server-1의 project-head 세션에게 현황 보고를 요청해". 서버 세션이 처리하고 회신이 로컬에 push된다. 로컬 세션은 기다리지 않고 작업을 이어간다 |
| S2 | 같은 서버 Claude 2개 | `test_s2_s10...` | 지정한 세션에만 도착한다 |
| S3 | 같은 서버, 다른 계정 | 없음 | 서로 다른 구독 계정으로 로그인한 두 Claude를 같은 OS 사용자로 연다. 하나의 daemon으로 동작한다. 다른 OS 사용자면 `socket_group` 구성이 필요하다(15장 3번) |
| S4 | Codex 대상 S1 | 어댑터 테스트 | M0-4 확인 후 수행 |
| S5 | 세션 종료 후 전송 | `test_s5_s17...` | 오류 대신 `stored_offline`이고 박스에 저장된다(ADR 0003). `hubctl agents`에 offline으로 표시된다 |
| S6 | 한 호스트 단절 | `test_s6...` | 서버 하나의 네트워크를 끊는다. `hubctl hosts`에서 그 호스트만 백오프하고 복구 후 재연결된다 |
| S7 | hub 재시작·절전 복귀 | `test_s7_s16...` | hub를 끈 사이 서버 세션이 회신(`queued`). hub를 켜면 한 번만 도착한다 |
| S8 | 부하 50대 | 미구현(M4) | |
| S9 | reply에 다시 회신, hop 초과 | `test_s9_*` | 서버 세션에 "받은 회신에 다시 답해"라고 시킨다. `reply_to_reply`로 거부된다 |
| S10 | 같은 호스트 두 세션(`allow_origination=host`), hub 중단 | `test_s2_s10...` | hub를 끈 상태에서 같은 서버의 두 세션 사이 전송이 된다 |
| S11 | 호스트 생략 이름이 로컬에 없음 | `test_s11...` | hub로 올라가 다른 호스트로 간다 |
| S12 | 자기 호스트 명시 + 없음, 동명 2개 | `test_s12...` | `not_found`, `ambiguous` |
| S13 | `mirror_local_metadata` | `test_s13...` | `hubctl log`에 본문 없이 기록된다 |
| S14 | add-host 실패 경로 | `test_s14...` | known_hosts에서 서버 키를 빼고 add-host를 실행한다. host key 오류로 중단된다 |
| S15 | 토큰 없음·오류·revoke·허용 외 프레임 | `test_s15_*` | `agent-hub revoke` 후 `hubctl hosts`에 `unauthorized` |
| S16 | 스트림 끊김 후 재연결 | `test_s7_s16...` | |
| S17 | 박스 세션으로 전송 | `test_s5_s17...` | `stored`, `check_box`로 읽힌다 |

## 기록

| 날짜 | Claude Code | Codex | 항목 | 결과 | 비고 |
|---|---|---|---|---|---|
| | | | | | |
