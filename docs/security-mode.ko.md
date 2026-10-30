# Security mode (SSH): 운영 가이드

## 모드 경계

현재 기본 구현은 사내에서 이미 승인한 SSH 연결을 사용한다. comfort/Tailscale은 명령 이름과
상태만 예약하고 설치·로그인·실행·공용 네트워크 검색을 수행하지 않는다.
Security라는 이름은 감사·인증·망분리 보장을 뜻하지 않는다. 일반 SSH의 신뢰 모델을 따른다.

## 사전 조건

- 양쪽에 Python 3.11+와 codex-bridge 설치. MCP 도구에는 `pip install -e '.[security]'` 필요.
- SSH initiator B에서 서버 A에 비대화형 공개키 인증이 가능해야 한다.
- A의 host key는 관리자가 배포하거나 별도 신뢰 경로로 fingerprint를 확인해 known_hosts에 등록한다.
  `ssh-keyscan` 결과만으로 호스트를 신뢰하지 않는다.
- ssh config와 remote Python path는 신뢰된 로컬 사용자가 선택하며, MCP나 원격 메시지가 수정할 수 없다.

`~/.ssh/config` 예시 (Windows는 사용자 프로필의 `.ssh/config`):

```sshconfig
Host server-a
    HostName approved-private-hostname
    User dedicated-bridge-user
    IdentityFile ~/.ssh/company-approved-key
    IdentitiesOnly yes
```

bridge는 alias만 받으며 사용자 이름·IP를 인수로 조합하지 않는다. 기존 회사 ProxyJump 등
승인된 경로 설정은 해당 alias를 따른다. SSH config 자체가 공인 호스트나 외부 jump host를
가리키면 데이터가 사내망에만 머문다고 보장하지 않으므로 관리자가 경로를 승인해야 한다.

강제하는 옵션: `BatchMode=yes`, `StrictHostKeyChecking=yes`, `ForwardAgent=no`,
`ClearAllForwardings=yes`, `PermitLocalCommand=no`, 별도 control socket 재사용 비활성화.
오래된 known_hosts, 키 잠금 해제 필요, 원격 Python 설치 실패 등은 자동 우회하지 않는다.

## 등록

A의 로컬 승인: `security init --name agent-a`, `security pair-code`.
B: `security init --name agent-b`, `security connect agent-a --ssh-host server-a --remote-python /absolute/venv/bin/python`.
코드는 SSH stdin으로만 전송하며 argv·환경 변수·설정 파일에는 저장하지 않는다.
서버에는 코드 hash, 5분 만료, 최대 5회 실패 횟수가 저장된다. 성공하면 삭제된다.
등록 후 256-bit token을 B의 private DB에, hash를 A의 private DB에 저장한다.
이 코드는 SSH 접속 권한이나 host key 검증을 대체하지 않는다.

A에는 B를 inbound peer로 기록하고 B에는 A로 향하는 SSH route를 기록한다.
하나의 route에서 B가 양쪽 mailbox를 교환하므로 A가 B로 SSH 접속할 필요가 없다.
A의 peers에 표시되는 B alias는 B의 init name이며, 이름 충돌 시 UUID로 표시한다.

## 프로필과 Windows

`security --profile corporate ...`로 프로필을 분리할 수 있다. 옵션은 하위 명령보다 먼저 둔다.
다른 프로필에 접속하려면 connect에 `--remote-profile corporate`를 추가한다.
신뢰된 로컬 상태 경로를 바꾸려면 `security --state-dir /trusted/parent ...`를 사용한다.
기본 경로는 Ubuntu `$XDG_STATE_HOME/codex-bridge/<profile>` 또는
`~/.local/state/codex-bridge/<profile>`, Windows `%LOCALAPPDATA%\codex-bridge\<profile>`이다.
Ubuntu 디렉터리 0700/DB 0600, Windows는 현재 사용자 SID와 SYSTEM ACL로 보호한다.
암호화 저장소는 아니므로 BitLocker/LUKS 등 회사 정책을 따로 적용한다.

Windows 서버를 대상으로는 remote Python 절대 경로와 `--remote-platform windows`를 지정한다.
고정된 endpoint 실행 명령은 PowerShell `-EncodedCommand`에 UTF-16LE로 인코딩한다.
이것은 인자 quoting을 위한 것으로 비밀 암호화가 아니다. 비밀·메시지는 SSH stdin으로 전송한다.
Windows OpenSSH Server와 PowerShell이 준비되어 있어야 한다. bridge는 서버 설치나 관리자 정책을 변경하지 않는다.

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

MCP 서버는 회사 사용자 로컬에서 stdio로 실행한다. 등록은 명시적 `mcp-install` 명령일 때만
`codex mcp add`를 호출한다. 기존 등록은 덮어쓰지 않는다. 도구 승인은 기존 Codex 설정을 따른다.
`approval_policy=never`인 환경에서 승인이 필요한 MCP 도구는 거부될 수 있다. 이때 회사가
허용한 interactive 승인 정책과 도구별 설정을 사용하며 등록기가 이를 자동 완화하지 않는다.
[공식 MCP 승인 설정](https://learn.chatgpt.com/docs/extend/mcp)을 참고한다. 제거는 `codex mcp remove codex_bridge_security`.

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
Windows unit/MCP 테스트와 명령 인코딩 테스트도 실제 Windows sshd 서버 접속을 보장하지 않는다.
