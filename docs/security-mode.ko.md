# Security mode (SSH): Ubuntu 운영 가이드

현재 지원 범위는 Ubuntu ↔ Ubuntu이며 Windows 지원은 추후 검토한다.

## 모드 경계

현재 기본 구현은 사내에서 이미 승인한 SSH 연결을 사용한다. comfort/Tailscale은 명령 이름과
상태만 예약하고 설치·로그인·실행·공용 네트워크 검색을 수행하지 않는다.
Security라는 이름은 감사·인증·망분리 보장을 뜻하지 않는다. 일반 SSH의 신뢰 모델을 따른다.

## `$agent-link` pairing 흐름: 사용자 PC A ↔ 서버 PC B

양쪽 Codex에서 저장소의 `.agents/skills/agent-link` 스킬을 사용할 수 있어야 한다.
사용자는 각자의 이미 열린 Codex 대화에서 `$agent-link`을 실행한다.

1. PC A에서 코드 발행 역할을 선택한다. 스킬이 A의 identity를 준비하고 5분짜리 1회용 pairing code,
   사내·공인 주소 후보 두 목록, SSH 계정·포트와 원격 실행에 필요한 A의 Python 절대 경로를 표시한다.
2. 사용자는 이 정보를 신뢰된 별도 경로로 B Codex 세션에 전달한다.
3. B에서 두 주소 경로의 DNS·TCP·SSH를 각각 확인한다. 공인 출구 IP가 실제 A를 가리키는지 host key를 확인한다. 검증된 경로가 없으면 중단하고, 성공한 경로가 있으면 코드 입력 역할을 선택한다. 스킬은 pairing code를 CLI의 비표시 입력으로 전달해 B에서 A로
   SSH 연결을 등록한다. 코드는 shell 명령행·환경변수·파일에 기록하지 않는다.
4. 각 스킬 실행은 현재 Codex 설치에 MCP 서버가 없는 경우에만 등록을 요청한다. 승인 정책은 바꾸지 않는다.

사용자는 pairing 명령을 직접 입력할 필요가 없다. 스킬 내부에서는 기존 CLI의 `security init`,
`pair-code`, `connect` 동작을 사용한다. 지원 범위는 Ubuntu ↔ Ubuntu다.

### SSH 경로 조건

- **B에서 A로** 비대화형 공개키 인증이 가능한 기존 OpenSSH 연결이어야 한다.
- A의 SSH 서비스가 이미 실행 중이고 B에서 접근 가능해야 한다. pairing 스킬은 sshd를 설치·실행하거나
  방화벽/라우터를 변경하지 않는다. 이 설정이 안 되어 있으면 회사 관리자 절차로 먼저 준비한다.
- A의 host key는 회사에서 배포하거나 별도 신뢰 경로로 fingerprint를 확인한 뒤 B의 known_hosts에 등록한다.
  `ssh-keyscan`만으로 호스트를 신뢰하지 않는다.
- B에서 쓸 SSH alias/hostname과 A의 remote Python 절대 경로는 사용자가 확인한다.
  hostname이 사내 DNS에서 해석되지 않으면 승인된 SSH config alias를 전달한다.
- 사내망에 함께 있다는 사실만으로 SSH 접근성이나 데이터의 사내망 잔류가 보장되지는 않는다.
  SSH config의 HostName, ProxyJump 등 실제 경로를 회사 정책에 따라 확인한다.

B의 `~/.ssh/config` 예시:

```sshconfig
Host workstation-a
    HostName approved-private-hostname
    User dedicated-bridge-user
    IdentityFile ~/.ssh/company-approved-key
    IdentitiesOnly yes
```

bridge는 호스트 키 자동 수락, agent forwarding, port forwarding, 임의 SSH 명령 실행을 허용하지 않는다.
알 수 없는 host key, SSH 인증 실패, 원격 Python 경로 오류를 자동 우회하지 않는다.

## Pairing 내부 동작

A는 code hash와 5분 만료 시간을 저장하고, 최대 5회 입력 오류 뒤 초대를 닫는다. 성공 시 초대 코드를
삭제하고 B의 256-bit peer token을 hash 형태로 보관한다. B는 token을 private local DB에 저장한다.
Pairing code는 SSH 권한 또는 host key 검증을 대체하지 않는다.

A에는 B가 inbound peer로, B에는 A가 SSH route peer로 저장된다. 따라서 B가 양쪽 mailbox를 동기화하며
A가 B로 접속할 필요는 없다. A의 peer 표시 이름은 B에서 생성한 agent 이름이다.

## 프로필과 상태 경로

`security --profile corporate ...`로 프로필을 분리할 수 있다. 옵션은 하위 명령보다 먼저 둔다.
다른 프로필에 접속하려면 connect에 `--remote-profile corporate`를 추가한다.
신뢰된 로컬 상태 경로를 바꾸려면 `security --state-dir /trusted/parent ...`를 사용한다.
기본 경로는 `$XDG_STATE_HOME/agent-link/<profile>` 또는
`~/.local/state/agent-link/<profile>`이다. 디렉터리 0700/DB 0600으로 보호한다.
암호화 저장소는 아니므로 LUKS 등 회사 정책을 따로 적용한다.
Windows 관련 후보 구현은 현재 지원·운영 검증 범위가 아니며 connect CLI에서 Windows 대상을 제공하지 않는다.

## 배달과 작업 실행

로컬 outbox 저장 → SSH exchange → 상대 inbox 저장 → 상대 Codex의 MCP receive →
로컬 권한에 따른 작업 → MCP reply → 다음 exchange에서 결과 회수.

- `security sync --watch`는 현재 profile의 outbound SSH route를 5초마다 순차 교환한다.
- 도구의 send/receive/task-status/reply도 필요한 sync를 수행한다.
- A의 inbound peer는 `awaiting_remote_poll`이다. queued는 전달·모델 실행·완료를 뜻하지 않는다.
- 응답 소실 시 같은 message UUID로 재시도하며 DB transaction/deduplication으로 중복 저장·상태 변경을 막는다.
- task의 requester/peer와 상태를 검증하며 terminal task의 progress 회귀를 거부한다.
- inbox를 읽은 것만으로 실행되지 않는다. 원격 내용을 사용자 권한으로 승격하지 않는다.
- background push/TUI 자동 메시지 주입이 없으므로 활성 Codex가 도구를 호출하지 않으면 처리는 대기한다.

MCP 서버는 회사 사용자 로컬에서 stdio로 실행한다. 사용자가 `$agent-link`을 실행하면 스킬이 등록 상태를 확인하고, 없을 때만 `mcp-install`을 통해 `codex mcp add`를 호출한다. 기존 등록은 덮어쓰지 않는다. 도구 승인은 기존 Codex 설정을 따른다.
`approval_policy=never`인 환경에서 승인이 필요한 MCP 도구는 거부될 수 있다. 이때 회사가
허용한 interactive 승인 정책과 도구별 설정을 사용하며 등록기가 이를 자동 완화하지 않는다.
[공식 MCP 승인 설정](https://learn.chatgpt.com/docs/extend/mcp)을 참고한다. 제거는 `codex mcp remove agent_link_security`.

## SSH 권한과 endpoint 제한

Endpoint 허용 operation은 `hello`, `enroll`, `exchange`뿐이다. shell 실행, 파일 읽기,
thread 선택, turn 시작 API가 없다. 토큰은 전송 받은 sender ID와 peer 권한을 검증한다.
다만 SSH 계정 자체에 일반 쉘 권한이 있다면 그 자격으로 다른 SSH 명령을 실행할 수 있다.
Bridge의 endpoint allowlist가 SSH 계정 전체를 sandbox하는 것은 아니다.

강한 분리가 필요하면 관리자가 전용 OS 계정과 별도 SSH 키를 만들고 authorized_keys의
`restrict,command="/absolute/venv/bin/python -m codex_bridge security endpoint"` 같은 강제 명령을
운영 정책에 맞춰 설정할 수 있다. remote profile도 고정해야 한다. 이 설정은 관리자가 검토할
예시이며 bridge가 자동 적용하지 않는다.

MCP tools는 peer 등록·SSH route 변경·임의 OS 명령 실행 도구를 제공하지 않는다.
권한이 필요한 작업은 수신 측 Codex의 기존 sandbox/승인 정책을 유지한다.
기밀 데이터가 모델 context에 포함되면 모델 제공자로 전송될 수 있으므로 사내 SSH와 별도 통제한다.

## 한도·보관·폐기

Message text 최대 8192 UTF-8 bytes, 교환 16개, wire 256 KiB, live mailbox 10,000개.
SSH 기본 timeout 30초이며 실패한 outbox는 유지한다. 여러 peer를 순차 sync하면 총 대기 시간은
그보다 길어질 수 있다. receive의 wait_seconds는 polling 대기 예산이며 SSH timeout 전체를 제한하지 않는다.

`ack`는 로컬 읽음 확인이다. `prune`은 배달된 발신 메시지와 읽고 원격 ack가 확인된 수신 본문을
삭제한다. replay 방지를 위해 UUID/hash/route tombstone과 task/audit metadata가 남는다.
완료 task의 관련 본문을 모두 지우면 task 결과 text도 비운다. SQLite free page나 OS backup에서
보안 삭제까지 보장하지는 않는다. 보관·디스크 quota는 회사 정책으로 관리한다.

`revoke`는 로컬 peer를 차단하고 로컬 token을 지운다. 상대도 폐기해야 양방향 권한이 모두 제거된다.
SSH 키와 기존 SSH session은 별도로 관리한다. 인증 실패가 자동으로 재등록·권한 확대를 일으키지 않는다.

## 실제 두 서버 검증

현재 자동 테스트와 실제 OpenSSH **loopback** 검증은 서로 다른 물리 서버 검증을 대체하지 않는다.
실제 A/B에서는 위 절차대로 등록한 후 양방향 hello task/result, 재연결, revoke 거부를 확인한다.
Windows 지원은 이번 작업 범위에서 제외했다. CI는 Ubuntu의 Python 3.11/3.13을 검증한다.

## 주소 후보 발급

`security connection-info`는 실제 인터페이스의 사내 IPv4와 공인 주소 후보를 함께 발급한다.
공인 주소는 인터페이스 주소, `--public-host`로 전달한 승인된 주소, `https://api.ipify.org`가 보고한
공인 출구 IPv4를 구분한다. 자동 조회는 HTTPS proxy 설정을 따르므로 회사 gateway나 proxy 주소일 수 있다.
모든 항목은 B의 검증 전까지 미검증이다. 공인 IP가 응답해도 그 서버가 A인지 host key를 확인해야 한다.
사내·공인 후보가 누락되면 그 상태를 표시하고 승인된 주소를 받아야 한다. 로컬 hostname 문자열이나
A에서 22번 포트가 열려 있다는 사실로 B의 접근 가능성을 추정하지 않는다.
