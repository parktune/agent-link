# 후속 검증: 409 충돌과 공개 클라이언트 경로

실행일: 2026-10-30. CLI/daemon 모두 0.166.0.
공개 소스: tag `rust-v0.166.0` (`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`),
main 조회 결과 `c15d0ce323f2197da865871502dc6ea7ef5b8ddb`.

## 1. 기존 daemon과 409 충돌

이전 JSONL adapter의 proxy 초기화 timeout은 클라우드 연결 실패의 근거가 아니었다.
Unix socket에 올바른 WebSocket handshake를 하는 조회 전용 adapter로 다시 확인했다.
기존 daemon의 초기화와 `remoteControl/status/read`가 즉시 성공했다.

| 실제 관측 | 결과 |
|---|---|
| 기존 daemon relay 상태 | connected |
| daemon에 environment가 있음 | true |
| 별도 테스트 App Server 초기 상태 | disabled |
| daemon과 테스트 프로세스 installationId | 동일, 메모리에서만 비교 |
| 별도 호스트 pairing 코드 발급·claimed 조회 | 성공 / false |
| 별도 호스트 relay 접속 | HTTP 409 Conflict |
| 409 재현 전·후 기존 daemon | 둘 다 connected |
| 재현 전·후 기존 daemon environment | 동일 |
| 테스트 호스트의 임시 disable | 성공 |

**기존 daemon의 OpenAI relay 연결은 정상이다.** 이전 standalone probe의 실패를
이 컴퓨터 전체에서 Remote Control을 사용할 수 없다는 뜻으로 해석하면 안 된다.

공개 소스 `core/src/installation_id.rs`는 같은 CODEX_HOME의 `installation_id` 파일을
재사용한다. 새 App Server 프로세스를 시작해도 새 머신 신원이 되는 것은 아니다.
실제로 같은 installation으로 별도 호스트를 활성화하면 409가 재현되므로 **중복 호스트
등록/연결과 관련된 충돌이라는 해석이 강하게 뒷받침된다**. 다만 기존 daemon을 중지한
대조 시험이나 클라우드 내부 lease 확인은 하지 않았다. 서버의 정확한 충돌 조건 및
installation/environment/connection 중 어느 키가 원인인지는 확정하지 않는다.

현재 필요한 동작은 기존 daemon을 재시작하는 것이 아니라, 이미 연결된 daemon을
호스트 소유자로 유지하는 것이다. 테스트를 위해 installation ID나 인증 DB를 복제·변조하지 않았다.

### 구현한 보호

- `cloud-status`: 기존 daemon의 상태만 조회. thread 조회/입력/실행, enable/disable 없음.
- `cloud-probe`: 같은 installation의 기존 daemon이 disabled가 아니면 별도 호스트 활성화를 생략.
- 생략 결과는 `host_probe: not_run`, `reason: existing_relay_owner`, exit 2.
- 기존 daemon 조회가 실패하면 검사 실패로 중단. 추측으로 두 번째 연결을 만들지 않음.
- 신원 비교용 원문 ID, socket path, 계정 정보는 결과에 출력하지 않음.

보호 검사에는 시간차가 있으므로 다른 프로세스의 동시 활성화를 완전히 막는 lock은 아니다.

## 3. 외부 클라이언트의 공식 연결 경로

단순히 메서드 이름 검색을 반복하는 데서 범위를 넓혀 아래 공개 구현을 확인했다.

| 후보 | 실제 역할 | 판단 |
|---|---|---|
| CLI `remote-control` | 호스트 start/stop/pair | code accept/connect 명령 없음 |
| App Server 실험적 schema | 호스트 활성화·코드 발급·claimed 조회·등록 client 관리 7종 | 원격 host 선택/코드 수락/relay client attach 계약 미발견 |
| Rust `RemoteAppServerClient` | 주소로 명시한 WebSocket 또는 Unix socket 연결 | endpoint enum에 pairing code/relay discovery 변형 없음 |
| Rust `CloudClient` | worker에게 명령을 전달하는 channel abstraction | 자체 pairing/HTTP/relay 접속 구현이 아님 |
| Python/TypeScript SDK | thread/turn 및 로컬 실행·App Server wrapper | 조사 범위에서 pairing 수락 도구 미발견 |
| backend/cloud client 및 생성 OpenAPI models | 클라우드 작업·환경·계정 관련 클라이언트 | pairing 수락용 공개 메서드 미발견 |
| exec-server Remote/Noise relay | executor registry·rendezvous와 command 실행 프로토콜 | Remote Control pairing을 받는 API가 아니며 기존 Codex thread 통신을 보장하지 않음 |
| 공식 Remote connections / Developer commands | 공식 앱의 remote UX 및 호스트 명령 설명 | 외부 bridge의 클라이언트 등록/접속 문서 미발견 |

`CloudClient`라는 이름이나 exec-server의 relay 프레임 코드가 있다는 것만으로 OpenAI
Remote Control 클라이언트를 구현할 수 있다고 판단하지 않았다. Noise registry 지원과
Remote Control pairing은 다른 계약이다. exec-server는 이번에 실제 등록·실행하지 않았다.

**조사 범위에서 공식적으로 사용할 수 있는 외부 클라이언트 계약은 여전히 확인되지 않았다.**
이것은 프로토콜을 기술적으로 재현할 수 없다는 증명이나 OpenAI 전체 코드가 존재하지
않는다는 주장이 아니다. 공개 소스 구현을 읽을 수 있다는 것과 호스팅 서비스가 그 경로를
외부 개발자에게 지원한다는 것도 구분해야 한다.

## 현재 결론

호스트 측 relay 연결 문제는 기존 daemon을 조회·재사용하는 방향으로 정리할 수 있다.
하지만 이 사실은 서버 B가 pairing code를 받아 relay client로 접속할 수 있다는 증거가 아니다.
원래 요구사항을 공식 인터페이스만으로 완성하려면 **외부 클라이언트용 pairing claim,
연결 자격 발급, 호스트 발견/attach 계약**이 추가로 필요하다.

이를 확인하지 못한 상태에서 두 서버 MVP 성공을 보고하거나 Tailscale/자체 relay로
목표를 바꾸지 않는다. 기존 daemon을 중단하거나 계정 토큰을 추출해 공식 앱을 가장하는
접속 시험도 하지 않았다. OpenAI에 확인할 기술 질문은 [질문 초안](openai-client-questions.ko.md)에 정리했다.
질문을 외부에 게시하거나 전송하지 않았다.

## 재현 및 검증

```bash
.venv/bin/python -m pip install -e ".[diagnostics]"
.venv/bin/codex-bridge cloud-status
.venv/bin/codex-bridge cloud-inspect
.venv/bin/codex-bridge cloud-probe
.venv/bin/python -m unittest discover -s tests -v
```

Unix WebSocket handshake·조회, 원격 승인 거부, mutation 전송 전 거부, timeout,
비밀 오류 본문 비노출, 기존 relay owner 발견 시 enable/pairing 미실행을 자동 테스트한다.
진단 extra는 Python WebSocket 라이브러리이며 VPN이나 별도 네트워크 서비스가 아니다.

## 근거

- [공식 Remote connections](https://learn.chatgpt.com/docs/remote-connections)
- [공식 Developer commands](https://learn.chatgpt.com/docs/developer-commands)
- [공식 App Server / Unix WebSocket transport](https://learn.chatgpt.com/docs/app-server)
- [설치 ID 재사용](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/core/src/installation_id.rs)
- [Rust RemoteAppServerClient](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-client/src/remote.rs)
- [CloudClient channel](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/app-server-client/src/cloud.rs)
- [Python SDK](https://github.com/openai/codex/tree/rust-v0.166.0/sdk/python)
- [exec-server remote registry/Noise contract](https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/exec-server/README.md)
