# Agent Link

서로 다른 서버의 Codex 세션이 pairing 후 **세션 이름으로 메시지를 주고받습니다.**
메시지를 받은 대화는 자동으로 깨어나 답하고, 각 대화는 자기 컨텍스트를 유지합니다.

| 모드 | 전송 | 현재 상태 |
|---|---|---|
| **security mode** (SSH mode) | 사용자가 승인한 기존 OpenSSH 경로 | 구현됨: 영속 수신함·양방향 작업·MCP 도구 |
| **comfort mode** (Tailscale mode) | 편의 연결과 사설망 discovery | 예약됨, 이번 범위에서 미구현·미설치 |

Security라는 이름은 회사 보안 승인을 대신하지 않습니다. SSH 경로는 사내에서 관리하고,
클라우드 모델 입력에 포함할 업무 데이터 범위는 별도로 통제해야 합니다.

## 설치

**현재 지원 범위는 Ubuntu ↔ Ubuntu입니다.** Windows 지원은 추후 검토합니다.
양쪽 PC에 Python 3.11+, Codex, SSH가 필요합니다. 이번 pairing 흐름에서는 B가 A로 SSH 접속합니다.
회사에서 승인한 패키지 설치 절차를 따르세요. 기본 전송 CLI는 표준 라이브러리만 사용하며
MCP 통합을 위해 아래 extra를 설치합니다. Tailscale이나 별도 relay는 설치하지 않습니다.

Ubuntu:

```bash
git clone https://github.com/parktune/agent-link.git
cd agent-link
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[security]'
python3 scripts/install_agent_link_skill.py
```

이후 `agent-link` 명령은 `.venv/bin/agent-link`를 뜻합니다.
`agent-link modes`로 모드를 확인할 수 있습니다.

## `$agent-link`로 연결: 사용자 PC A ↔ 서버 PC B

양쪽 PC에서 저장소를 clone하고 위 설치 절차로 스킬을 등록합니다. Codex를 새로 열거나 skill 목록을 새로 고친 뒤, 사용자가 각 PC의 **이미 열려 있는 Codex 대화에서 `$agent-link` 스킬을 실행**합니다.
A는 pairing code와 접속에 필요한 host 정보를 발행하고, B는 받은 정보를 입력해 A에 연결합니다.
pairing code를 생성하기 위해 사용자가 직접 CLI 명령을 입력할 필요는 없습니다.

### PC A: 코드 발행

A의 Codex에서:

> `$agent-link` 이 PC를 A로 설정하고 pairing code를 발행해줘.

스킬은 A의 identity를 준비하고 5분 유효·1회용 코드를 발행합니다. 함께 전달할 사내 IP와 공인 주소 후보를 모두 포함한 접속 카드, SSH 계정·포트와
A에 설치된 agent-link Python 경로를 안내합니다. 해당 정보를 B의 Codex 세션에 안전하게 전달합니다.

### PC B: 코드 입력 및 연결

B의 Codex에서:

> `$agent-link` A가 발행한 pairing code와 hostname을 입력할게. 연결해줘.

스킬은 코드를 안전 입력 프롬프트로 받고 B에서 A로 SSH 연결을 등록합니다. B는 사내·공인 경로를 각각 점검하고 A의 host key와 인증을 검증한 경로를 선택합니다. 코드 값을 shell 명령,
argv, 환경 변수, 파일에 넣지 않습니다. 연결 뒤 peer와 MCP 설정을 확인합니다.

### 필요한 네트워크 조건

현재 경로는 **B → A 방향의 기존 OpenSSH 접속**입니다. 두 PC가 사내망에 있더라도 A의 SSH 서비스가
B에서 접근 가능해야 하며, A의 host key는 사전에 B의 `known_hosts`에서 신뢰되어야 합니다.
코드 발행은 sshd 설치·실행, 방화벽 개방, SSH key 배포를 대신하지 않습니다. 스킬은 이런 시스템 설정을
자동 변경하지 않습니다. hostname이 B에서 해석되지 않으면 회사에서 승인한 SSH alias를 전달하세요.

중앙 relay, 웹 서버, Tailscale은 사용하지 않습니다. B가 A로 SSH 접속할 때 양쪽 SQLite mailbox를
교환하므로 A에서 B로 역방향 SSH 연결을 열 필요는 없습니다.

자세한 스킬 절차는 [agent-link 스킬](.agents/skills/agent-link/SKILL.md),
보안 설정과 저장 구조는 [Ubuntu 운영 가이드](docs/security-mode.ko.md)를 참고하세요.

## 세션끼리 대화하기

한 서버에 여러 Codex 대화가 있을 수 있으므로 대화는 **세션 이름**(Codex `/rename`)으로 부릅니다.

1. 각 대화에서 `/rename <이름>`으로 이름을 정하고, `$agent-link 이 대화를 수신 세션으로 등록해줘`라고 요청합니다.
   스킬이 `security session add <이름>`을 실행합니다. 등록된 이름은 상대에게 자동으로 알려집니다.
2. 양쪽 PC에서 `agent-link security watch`를 터미널(tmux 등)에 띄워 둡니다.
   새 메시지가 오면 `codex queue`로 해당 이름의 대화에 넣어 그 대화를 깨웁니다.
3. 대화에서 보냅니다.

B의 `reviewer` 대화:

> agent-link로 agent-a의 planner 세션에 "설계 검토 부탁해"라고 보내. 내 세션은 reviewer야.

A의 `planner` 대화가 저절로 깨어나 메시지를 받고, 같은 `conversation_id`로 `reviewer`에 답합니다.
작업 단위로 주고받으려면 `bridge_send_task`를 쓰며, `bridge_reply` 결과는 요청한 세션으로 자동으로 돌아갑니다.

| 도구 | 용도 |
|---|---|
| `bridge_peers` | peer 목록과 각 peer가 받는 세션 이름, 이 PC의 등록 세션 |
| `bridge_send_message` | 세션 이름으로 대화 메시지 전송 (`conversation_id`로 이어가기) |
| `bridge_send_task` / `bridge_reply` / `bridge_task_status` | 작업 요청·결과·상태 |
| `bridge_receive` / `bridge_ack` | 깨우지 않은 메시지(세션 미지정·미등록)를 직접 확인 |

깨어난 대화는 그 세션의 user 메시지로 원격 내용을 받습니다. 봉투에 비신뢰 데이터라고 표시하지만 권한 경계는 아니므로,
등록하는 세션의 sandbox/승인 정책을 원격 peer 기준으로 정하세요. 두 대화가 서로 답하는 횟수에 제한은 없습니다.
멈추려면 watch를 끄거나 `security session remove <이름>`을 실행합니다. 자세한 동작은
[운영 가이드](docs/security-mode.ko.md#세션-주소와-자동-깨우기)에 있습니다.

## CLI 운영 및 상태 확인

Pairing은 `$agent-link` 스킬로 진행합니다. 그 후 운영자는 상태 확인이나 mailbox 관리를 위해
`agent-link security peers`, `watch`, `session add|remove|list|status`, `message`, `sync`, `receive`, `task`,
`revoke`, `prune` CLI 명령을 사용할 수 있습니다.
작업 요청/응답은 기존 Codex 대화에서 MCP 도구로 수행하는 것을 기본 흐름으로 합니다.

요청마다 task ID, 이벤트마다 message ID가 있으며 완료 상태와 배달 상태를 구분합니다.
`ack`는 로컬 읽음 확인이며 실행 승인이 아닙니다. `revoke`는 로컬 자격을 폐기하므로 양방향 폐기에는
양쪽에서 처리해야 합니다.

## 범위와 검증

- 프로필별 SQLite 수신함, 만료·1회용 등록 코드, peer 토큰, ID 중복 방지, task 소유권·완료 상태 검증
- SSH stdin/stdout만 사용. 임의 명령·파일 읽기·Codex turn 실행을 제공하는 원격 endpoint 없음
- 기본 메시지 한도 8 KiB, 교환당 16개, live mailbox 10,000개, 제한된 subprocess 출력·실행 시간
- 등록된 세션 이름으로만 `codex queue` 깨우기. 비활성 세션은 backoff 재시도, 미등록 세션 앞 메시지는 보류
- 파일 전송, Tailscale mode는 아직 미구현
- 실제 OpenSSH loopback의 세션 주소 양방향 왕복과 MCP stdio handshake 검증.
  열린 TUI에 대한 실제 `codex queue` 깨우기와 별도 서버 두 대 실측은 [실서버 시험 절차](docs/real-server-test.ko.md)로 진행
- 양쪽 agent-link는 같은 버전(0.3.0 이상)을 사용합니다. 이전 버전과는 exchange가 거부됩니다

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

## 이름 변경 호환성

저장소·패키지·CLI·스킬의 기본 이름은 `agent-link`입니다. 기존 설치를 위해 `codex-bridge` CLI 별칭과
`codex_bridge` Python 모듈은 유지합니다. 기존 `codex-bridge` 상태 디렉터리가 있으면 그 데이터를 계속
사용하며, 새 설치는 `agent-link` 상태 디렉터리를 사용합니다. 기존 `codex_bridge_security` MCP 등록도
계속 사용할 수 있고 새 등록의 기본 이름은 `agent_link_security`입니다.

`agent-link security connection-info`는 사내 IP와 공인 주소 후보를 함께 출력합니다. 공인 IP 자동 조회에는
`https://api.ipify.org`를 사용하며 이 서비스는 통신 중계 서버가 아닙니다. 조회 결과는 NAT/HTTPS proxy의
출구 주소일 수 있어 A로의 입장 경로를 보장하지 않습니다. 승인된 공인 접속 주소가 있다면
`connection-info --public-host <hostname-or-ip>`로 함께 전달하세요. 조회에 실패하면 누락 상태를 명시하고
승인된 공인 주소를 받아야 합니다. B에서 두 경로의 DNS·TCP·SSH를 각각 확인하며, 둘 다 실패하면
코드 재발급 대신 네트워크 경로를 먼저 해결합니다.
