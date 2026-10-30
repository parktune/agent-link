# Phase 1: 기술 타당성

조사일: 2026-10-30. 설치 CLI: **0.166.0**.
소스 기준: `openai/codex` tag `rust-v0.166.0`, commit
`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`.
최초 main도 조사했으나 버전 차이를 피하기 위해 아래 판단은 해당 tag와 로컬 생성 JSON Schema를 기준으로 정리했다.

판정의 의미: Supported는 문서/해당 버전 인터페이스 존재, Experimental은 실험적 인터페이스,
Unsupported는 공개 지원 계약을 찾지 못해 프로젝트 백엔드로 채택하지 않음,
Unverified는 실행 검증 전이다. Unsupported가 기술적인 불가능을 뜻하지는 않는다.

| 항목 | 판정 | 근거·검증 범위 |
|---|---|---|
| App Server 초기화·thread 생성·조회 | Experimental, 로컬 검증 | CLI가 app-server를 experimental로 표시. 실제 initialize/start/read 성공 |
| 같은 thread에서 다음 turn 실행·응답 수신 | Experimental | turn/start, item/completed, turn/completed. 실제 결과는 validation 문서 참조 |
| 실행 중인 기존 TUI thread에 입력 전달 | Experimental / Unverified | queue CLI와 thread/queue/add 구현 존재. 실제 사용 중인 TUI에 주입하지 않음 |
| 저장된 thread 재개 | Experimental / Unverified | thread/resume. 재개와 활성 세션 공유는 구분 필요 |
| 다른 서버에서 App Server 접근 | Experimental / Unverified | --remote ws/wss/unix, 인증 옵션 존재. 두 물리 서버 검증 전 |
| remote-control start/pair | Experimental | CLI help와 공개 소스 확인. 이번 작업에서 daemon 변경·pair 코드 발급은 하지 않음 |
| Pairing Code로 외부 A2A 클라이언트 연결 | Unsupported | 외부 클라이언트용 공개 연결 계약을 찾지 못함. 발급 API 존재만으로 사용 가능 판정 금지 |
| 같은 계정의 원격 머신 열거·임의 relay 메시지 | Unverified / 채택 제외 | 임의 A2A용 공식 API 확인 못 함 |
| MCP 도구 통합 | Supported / 프로젝트 미구현 | codex mcp CLI 존재. 설치 설정 변경은 하지 않음 |
| Codex SDK | Supported / 미채택 | 프로그래밍용 인터페이스. 활성 TUI 세션 공유를 자동 보장하지 않음 |
| A2A 표준 호환 | Unverified / 후속 조사 | task/event 구조 매핑 후보. 현재 출력은 자체 PoC 형식이며 A2A 호환을 주장하지 않음 |

## Remote Control의 실제 구조

CLI `remote-control start`는 daemon을 준비하고 remote control을 활성화한다.
`pair`는 daemon 측 pairing 시작 함수를 호출한다. 소스상 호스트 enrollment,
인증된 서버 토큰, outbound WebSocket relay 연결이 분리되어 있다.
Pairing은 enrollment에 묶인 단기 pairing 정보와 만료 시간을 받는 절차이며,
로컬 CLI가 임의 상대 agent로 연결하는 명령과 동일하지 않다.

Pairing 요청은 enrollment의 서버 토큰으로 인증된다. 로그인 계정과 설치/서버 ID,
토큰 갱신·인증 주체 변경·클라이언트 상태 관리도 구현되어 있다.
따라서 코드를 알면 누구나 같은 프로토콜로 A2A relay를 쓸 수 있다는 결론은 성립하지 않는다.
이 프로젝트는 내부 backend endpoint를 호출하거나 토큰을 추출·재사용하지 않는다.

검증 수준은 CLI help, 공개 소스 정적 분석, 로컬 App Server 프로토콜 실행이다.
공식 relay의 패킷 캡처나 외부 클라이언트 접속 시험은 하지 않았다.

## 기존 대화 컨텍스트

가장 직접적인 경로는 **그 thread를 소유한 App Server**에 접속하는 것이다.
`codex app-server proxy --sock PATH`는 daemon control socket을 stdio로 연결한다.
`thread/resume`는 저장된 대화를 재개하는 기능이다. 별도 프로세스에서 같은 파일을
읽는 것을 활성 TUI runtime 공유로 오인하면 안 된다.

0.166.0의 `codex queue --thread UUID --message TEXT [--resume]`는
`thread/queue/add`를 사용한다. `--resume`은 실패해도 경고만 내고 명령이 성공할 수 있으므로
queue 성공을 모델 실행 완료로 취급하면 안 된다. 완료 이벤트나 task 응답을 따로 추적해야 한다.
활성 thread에는 무조건 turn/start를 넣지 말고 idle/busy와 큐 정책을 먼저 확인해야 한다.

## 재현 가능한 조사 명령

```bash
codex --version
codex --help
codex queue --help
codex remote-control --help
codex remote-control pair --help
codex app-server --help
codex app-server proxy --help
codex app-server generate-json-schema --out /tmp/codex-bridge-schema
```

CLI 업데이트 시 위 명령과 schema를 다시 확인한다. 이 프로젝트는 Codex를 자동 업데이트하지 않는다.

## 공식 문서 및 버전 고정 소스

- [OpenAI App Server](https://learn.chatgpt.com/docs/app-server)
- [OpenAI Developer commands](https://learn.chatgpt.com/docs/developer-commands)
- [OpenAI Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [CLI remote-control](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/cli/src/remote_control_cmd.rs)
- [Daemon remote-control client](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-daemon/src/remote_control_client.rs)
- [Remote transport implementation](https://github.com/openai/codex/tree/rust-v0.166.0/codex-rs/app-server-transport/src/transport/remote_control)
- [Pairing enrollment](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-transport/src/transport/remote_control/enroll.rs)
- [Queue command](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/tui/src/session_queue_commands.rs)

공식 문서는 갱신될 수 있으며 설치 버전과의 일치 여부는 위 tag와 로컬 schema로 판단한다.
