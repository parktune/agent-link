# OpenAI 클라우드만 사용하는 pairing 경로 검증

조사·실행: 2026-10-30. 설치 CLI 0.166.0.

## 목표와 합격 기준

양쪽 서버에 Codex와 codex-bridge만 설치한다. 양쪽은 OpenAI 클라우드에 outbound로
접속한다. 상대 IP/hostname 공유, inbound 포트 개방, Tailscale/SSH/자체 relay 없이
짧은 pairing code만 입력해 연결한다. 각 서버의 기존 대화 컨텍스트를 유지하며
A→B 요청/결과와 B→A 요청/결과를 실제 모델로 검증해야 최종 합격이다.

공용 중계자가 필요하다는 점과 그 중계자가 반드시 OpenAI여야 한다는 점은 구분한다.
현재 프로젝트의 사용자 지정 목표는 **OpenAI의 기존 인프라 활용**이다.

## 조사 범위

- 설치 CLI의 help, 실험적 옵션을 포함한 직접 생성 schema
- `openai/codex` `rust-v0.166.0`, commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`
- 조사 시점 main `c15d0ce323f2197da865871502dc6ea7ef5b8ddb`의 관련 공개 소스
- 공식 OpenAI 문서
- 별도 stdio App Server의 실제 Remote Control RPC

해당 tag와 조사한 main의 remote-control CLI 및 v2 타입 정의 사이에는 diff가 없었다.
따라서 이 두 파일에 대해서는 최신 소스로 바꾸기만 하면 클라이언트 기능이 생긴다고
볼 근거가 없다. main 전체를 실행하거나 빌드한 것은 아니다.

## 역할별 인터페이스

설치 버전의 `generate-json-schema --experimental` 결과에는 다음 7개만 있다.

| RPC | 역할 |
|---|---|
| remoteControl/enable | 현재 호스트의 Remote Control 활성화 |
| remoteControl/disable | 현재 호스트의 Remote Control 비활성화 |
| remoteControl/status/read | 현재 호스트의 relay 연결 상태 조회 |
| remoteControl/pairing/start | 현재 호스트용 pairing 코드 발급 |
| remoteControl/pairing/status | 발급된 코드가 claimed 되었는지 조회 |
| remoteControl/client/list | 특정 environment의 등록된 제어 클라이언트 목록 |
| remoteControl/client/revoke | 해당 클라이언트 등록 취소 |

`pairing/status`가 코드를 인자로 받는다고 해서 다른 서버의 코드를 수락하는 것은 아니다.
응답은 `claimed: bool`이며 소스에서 발급 호스트 enrollment의 토큰으로 조회한다.
`client/list`도 연결 가능한 다른 호스트를 찾는 목록이 아니다.

CLI `remote-control` 하위 명령은 start/stop/pair이다. 클라이언트 역할의 코드 redeem/claim,
원격 호스트 발견, relay client attach용 공개 메서드는 조사 범위에서 찾지 못했다.
`--remote`는 명시적인 ws/wss/unix 주소를 받으므로 pairing code를 넣는 대체 수단이 아니다.
공식 앱에 클라이언트 역할이 존재한다는 점과 외부 bridge에서 그 역할을 쓸 수 있다는 점은 별개다.

## 실제 실행 결과

별도 App Server에 `experimentalApi: true`로 초기화한 뒤 공개 RPC만 사용했다.
초기 상태 disabled, enable/disable은 모두 `ephemeral: true`로 실행했다.

- 호스트용 **실제 manual pairing code 발급 성공**. 코드와 토큰은 출력·커밋하지 않았다.
- `pairing/status` 조회 성공, `claimed: false`.
- relay WebSocket 연결은 **HTTP 409 Conflict**, 상태 `errored`로 관측되었다.
  기존 연결과의 충돌 여부는 확정하지 않았다. 코드 발급만으로 연결 성공이라고 판정하지 않았다.
- 종료 시 임시 Remote Control disable 응답 `disabled` 확인.
- 모델 호출·기존 TUI thread 주입·daemon 재시작·영구 enable 설정 변경은 수행하지 않았다.
- 외부 서버의 client claim/attach는 실행하지 못했으며 A2A 성공을 주장하지 않는다.

호스트 enrollment는 Codex 자체에서 로컬 DB와 클라우드에 저장할 수 있다.
ephemeral은 활성화 선호 설정의 비영속성을 뜻하며 모든 enrollment 흔적 삭제를 뜻하지 않는다.

## 현재 결론과 다음 검증

호스트 코드 발급 경로는 실제 동작한다. 다음 두 문제는 서로 독립적이다.

1. 현재 환경에서 relay WebSocket이 HTTP 409 Conflict를 반환하는 원인.
   기존 daemon은 실행 중이지만, 이것이 충돌 원인이라는 인과관계는 검증되지 않았다.
2. 외부 bridge가 공식적으로 코드를 수락하고 relay에 클라이언트로 붙을 수 있는 인터페이스.

첫 문제를 해결해도 두 번째가 자동으로 해결되지는 않는다. 공개된 클라이언트 SDK/API,
공식 CLI 지원 또는 확장 계약이 확인되면 client adapter를 구현해 두 서버로 시험한다.
확인 전에는 host 상태와 client 미구현 상태를 구분해 보고한다. 임의 private endpoint를
추정 호출하거나 공식 앱의 토큰을 추출해 외부 클라이언트로 가장하지 않는다.

Tailscale/자체 relay로 자동 전환하지 않는다. 공식 경로가 확인되지 않으면 사용자가
목표나 허용 범위를 판단할 수 있도록 근거와 제한을 명시한다.

## 재현

```bash
.venv/bin/codex-bridge cloud-inspect
.venv/bin/codex-bridge cloud-probe --timeout 30
```

호스트 측 검사만 모두 성공하면 exit 0, 검사/정리 실패 시 exit 1이다.
코드 수락 기능이 구현되거나 A2A 왕복이 성공했다는 뜻이 아니다.

## 근거

- [공식 Remote connections](https://learn.chatgpt.com/docs/remote-connections)
- [공식 Developer commands](https://learn.chatgpt.com/docs/developer-commands)
- [공식 App Server](https://learn.chatgpt.com/docs/app-server)
- [v0.166.0 Remote Control 타입](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-protocol/src/protocol/v2/remote_control.rs)
- [v0.166.0 pairing 조회 구현](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-transport/src/transport/remote_control/enroll.rs)
- [v0.166.0 ephemeral 처리](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server/src/request_processors/remote_control_processor.rs)


## 로컬 adapter 추가 확인

기존 daemon을 건드리지 않고 `app-server proxy`로 초기화를 시험했으나 JSONL adapter의
initialize가 timeout 되었다. 소스상 proxy는 `codex_stdio_to_uds::run`으로 원시 socket bytes를
전달한다. daemon의 Unix socket은 WebSocket handshake를 요구하므로 JSONL을 직접 쓰는
현재 adapter와 호환되지 않는다. 이전 README의 proxy 호환 가능 표현을 수정했다.
기존 thread에는 메시지를 보내지 않았고 daemon 재시작도 하지 않았다.

## 자동 테스트

기존 RPC 테스트와 신규 클라우드 테스트 총 13개 통과. 신규 테스트는 코드·토큰 비노출,
호스트 성공과 A2A 성공의 구분, 실패/timeout 후 임시 disable, cleanup 실패 보고,
pairing 성공+relay 실패의 구분, 구조화된 로그에서 HTTP 상태만 추출하는 동작을 검증한다.
외부 서버나 공식 앱의 코드 수락을 mock 성공으로 대체하지 않는다.
