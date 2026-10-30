# Codex Bridge

각자의 대화 컨텍스트를 유지하는 Codex 세션 사이에서 작업 요청·결과를 교환합니다.

| 모드 | 전송 | 현재 상태 |
|---|---|---|
| **security mode** (SSH mode) | 사용자가 승인한 기존 OpenSSH 경로 | 구현됨: 영속 수신함·양방향 작업·MCP 도구 |
| **comfort mode** (Tailscale mode) | 편의 연결과 사설망 discovery | 예약됨, 이번 범위에서 미구현·미설치 |

Security라는 이름은 회사 보안 승인을 대신하지 않습니다. SSH 경로는 사내에서 관리하고,
클라우드 모델 입력에 포함할 업무 데이터 범위는 별도로 통제해야 합니다.

## 설치

**현재 지원 범위는 Ubuntu ↔ Ubuntu입니다.** Windows 지원은 추후 검토합니다.
양쪽 PC에 Python 3.11+, 기존 OpenSSH 접속 환경, Codex가 필요합니다.
회사에서 승인한 패키지 설치 절차를 따르세요. 기본 전송 CLI는 표준 라이브러리만 사용하며
MCP 통합을 위해 아래 extra를 설치합니다. Tailscale이나 별도 relay는 설치하지 않습니다.

Ubuntu:

```bash
git clone https://github.com/parktune/codex-bridge.git
cd codex-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[security]'
```

아래 `codex-bridge`는 `.venv/bin/codex-bridge`를 뜻합니다.
`codex-bridge modes`로 모드를 확인할 수 있습니다.

## SSH mode: Ubuntu A ↔ Ubuntu B

예시는 **B에서 A로 SSH 접속 가능한 경우**입니다. A에는 별도 웹 서버나 추가 수신 포트를 열지 않습니다.
서버가 다르면 먼저 회사에서 승인한 SSH config 별칭 `server-a`를 준비하고, 키 인증과
호스트 키 fingerprint를 확인하세요. bridge는 최초 호스트 키를 자동 수락하지 않으며
`BatchMode=yes`, `StrictHostKeyChecking=yes`, agent/port forwarding 비활성화를 강제합니다.
사내 SSH 설정 예시는 [상세 가이드](docs/security-mode.ko.md)에 있습니다.

A에서:

```bash
codex-bridge security init --name agent-a
codex-bridge security pair-code
# 5분 유효, 1회용 코드를 B 사용자에게 안전하게 전달
```

B에서:

```bash
codex-bridge security init --name agent-b
codex-bridge security connect agent-a \
  --ssh-host server-a \
  --remote-python /absolute/path/to/codex-bridge/.venv/bin/python
# A의 코드를 비표시 입력
codex-bridge security peers
codex-bridge security sync --watch
```

`--remote-python`은 **A에 설치한 venv Python의 절대 경로**입니다. 사용자 이름이나 홈 경로를
하드코딩하지 않습니다. 현재 connect 명령은 Ubuntu/POSIX 서버만 대상으로 합니다.

한 번 등록하면 **B→A 요청뿐 아니라 A→B 요청도 같은 SSH 경로로 전달**합니다.
B의 sync가 A의 수신함에 배달하고 A의 발신함을 가져오는 방식이므로 A가 B로 SSH 접속할 필요는 없습니다.
B에서 지속적으로 `sync --watch`를 실행하면 양방향 메시지가 주기적으로 교환됩니다.
기존 SSH 설정·서비스·방화벽·Codex daemon은 자동 변경하지 않습니다.

## 기존 Codex 대화에서 사용

각 PC에서 해당 프로필을 MCP 도구로 등록합니다.

```bash
codex-bridge security mcp-install
```

이 명령을 사용자가 실행하면 `codex mcp add`로 현재 venv의 stdio 도구를 등록합니다.
기존 같은 이름의 설정은 덮어쓰지 않습니다. Codex에서 MCP 목록을 새로 고치거나 재시작한 뒤 사용하세요.

B의 Codex:

> bridge 도구로 agent-a에게 hello 작업을 요청하고 task ID를 알려줘.

A의 Codex:

> bridge 수신함을 확인해. hello 요청에는 hello received로 응답해.

도구는 `bridge_peers`, `bridge_send_task`, `bridge_receive`, `bridge_reply`,
`bridge_task_status`, `bridge_ack`입니다. **현재 대화 안에서 도구를 호출하므로 thread를 새로
만들거나 기존 세션을 다른 에이전트로 바꾸지 않습니다.**

요청 도착만으로 Codex를 자동 실행하거나 사용자 명령을 주입하지 않습니다.
수신 측이 `bridge_receive`를 호출해야 하며, 원격 내용은 비신뢰 도구 출력으로 전달됩니다.
`bridge_receive(wait_seconds=...)`는 최대 30초 대기를 요청할 수 있습니다. SSH 연결 자체의
timeout은 별도로 적용됩니다. 대기·polling 주기는 회사 운영 정책에 맞추세요.

## CLI로 통신 확인

B에서:

```bash
codex-bridge security send agent-a 'hello'
```

A에서:

```bash
codex-bridge security receive
codex-bridge security reply <task-id> 'hello received'
```

B에서:

```bash
codex-bridge security task <task-id>
```

요청마다 task ID, 이벤트마다 message ID가 있으며 완료 상태와 배달 상태를 구분합니다.
연결이 끊기면 동일한 저장된 message ID로 재시도합니다. `security ack <message-id>`는
로컬 읽음 확인이며 실행 승인이 아닙니다. `security prune`은 배달·읽음 조건을 만족한
본문을 제거하고 재전송 방지용 tombstone을 남깁니다. `security revoke <peer>`는
로컬 자격을 폐기합니다. 양방향 폐기에는 상대 PC에서도 revoke가 필요합니다.

## 범위와 검증

- 프로필별 SQLite 수신함, 만료·1회용 등록 코드, peer 토큰, ID 중복 방지, task 소유권·완료 상태 검증
- SSH stdin/stdout만 사용. 임의 명령·파일 읽기·Codex turn 실행을 제공하는 원격 endpoint 없음
- 기본 메시지 한도 8 KiB, 교환당 16개, live mailbox 10,000개, 제한된 subprocess 출력·실행 시간
- 파일 전송, 자동 background Codex 실행, Tailscale mode는 아직 미구현
- 실제 OpenSSH loopback의 양방향 작업 왕복과 MCP stdio handshake 검증. 별도 회사 서버 두 대 실측은 아직 미실시

```bash
python -m unittest discover -s tests -v
# Linux, sshd와 기존 /run/sshd가 있는 환경의 임시 loopback 시험
python scripts/verify_security_ssh.py
# 실제 Codex 두 프로세스+MCP+SSH 통합 시험: 모델 사용량 발생
python scripts/verify_security_ssh.py --models
```

모델 통합 시험은 임시 작업 디렉터리와 합성 hello만 사용하며, 시스템 SSH 설정이나 사용자 SSH 키를 변경하지 않습니다.
생성된 Codex 테스트 대화 기록은 계정 로컬 기록에 남을 수 있습니다.
[검증 기록](docs/validation.ko.md), [보안 경계](SECURITY.md)를 참고하세요.

## 이전 조사와 PoC

`probe` / `demo`는 기존 로컬 App Server PoC입니다. `cloud-*`는 OpenAI relay 조사용이며,
security mode 실행 시 호출하지 않습니다. [기술 조사](docs/feasibility.ko.md),
[클라우드 후속 조사](docs/relay-followup.ko.md), [원본 요구사항](docs/requirements.ko.md)을 보존합니다.
OpenAI relay의 외부 pairing client 계약은 확인되지 않았고 현재 제품 경로가 아닙니다.

Apache-2.0.
