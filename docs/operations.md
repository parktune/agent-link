# 설치와 운영

지원 범위는 Ubuntu(서버와 로컬 PC)와 Python 3.11 이상이다. 패키지 설치와 daemon 실행은 add-host의 사전 조건이며 자동화하지 않는다.

## 1. 설치 (로컬 PC와 각 서버 공통)

```bash
git clone https://github.com/parktune/agent-link.git ~/agent-link
cd ~/agent-link
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/install.py            # 스킬과 MCP 서버 등록(--dry-run으로 계획만 확인)
```

`install.py`가 바꾸는 것은 사용자 설정뿐이다.
- `~/.claude/skills/agent-link`, `~/.codex/skills/agent-link`에 스킬을 복사한다.
- `claude mcp add --scope user agent-link`, `codex mcp add agent-link`로 MCP 서버를 등록한다.

이미 있는 항목은 그대로 둔다. 업데이트하려면 `--replace`를 붙인다.

## 2. 서버: spoke daemon

```bash
~/agent-link/.venv/bin/agent-hub daemon --host-name gpu-server-1
```

호스트 이름은 에이전트 ID의 앞부분이 된다. `~/.local/state/agent-link/config.toml`의 `host_name`으로도 정할 수 있다.
상시 실행하려면 systemd user 서비스로 등록한다.

```ini
# ~/.config/systemd/user/agent-link-spoke.service
[Unit]
Description=agent-link spoke daemon
[Service]
ExecStart=%h/agent-link/.venv/bin/agent-hub daemon --host-name gpu-server-1
Restart=on-failure
[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now agent-link-spoke
loginctl enable-linger "$USER"   # 로그아웃 후에도 유지하려면(관리자 정책 확인)
```

## 3. 로컬 PC: hub

```bash
~/agent-link/.venv/bin/hubctl serve
```

서버와 같은 방식으로 systemd user 서비스(`ExecStart=%h/agent-link/.venv/bin/hubctl serve`)로 둔다.

## 4. 서버 등록 (`hubctl add-host`)

사전 조건은 다음과 같다. agent-link가 대신 만들지 않는다.
- `~/.ssh/config`에 alias가 있다(ProxyJump 포함 가능).
- 비대화형 키 인증이 된다.
- 서버의 host key fingerprint를 별도 경로로 확인해서 known_hosts에 넣어 두었다.
- 서버에 위 1, 2단계가 끝나 있다.

```bash
hubctl add-host gpu1 --remote-path /home/me/agent-link/.venv/bin/agent-hub
```

1. DNS와 TCP를 점검한다. ProxyJump를 쓰면 이 단계는 건너뛰고 SSH 단계에서 판단한다.
2. `ssh gpu1 <remote-path> invite --json`으로 1회용 초대를 받는다. 코드는 SSH 파이프로 hub 메모리에만 들어온다.
3. 256-bit 토큰을 만들고 `enroll`한다. 서버에는 토큰의 해시만 저장된다.
4. `hello`로 토큰을 확인하고 hub에 반영한다.
5. 서버 관리자가 적용할 `authorized_keys` 한 줄을 출력한다.

권장 구성은 서버에 전용 OS 계정을 두고, 그 계정의 `authorized_keys`에 hub 전용 키를 강제 명령으로 등록하는 것이다.

```
restrict,command="/home/svc/agent-link/.venv/bin/agent-hub endpoint" ssh-ed25519 AAAA... hub
```

이 경우 셸 접근이 되는 별도 alias로 초대만 발급한다: `--admin-alias gpu1-admin`.
SSH로 명령을 실행할 수 없으면 서버에서 `agent-hub invite`로 코드를 받아 신뢰된 경로로 옮기고 `hubctl add-host gpu1 --remote-path ... --code`의 비표시 프롬프트에 입력한다.
스트림을 유지할 수 없는 네트워크에서는 `--mode poll`을 쓴다.

일반 셸 권한이 있는 SSH 계정을 쓰면 endpoint의 허용 목록은 그 계정 전체를 sandbox하지 못한다. SSH 키 자체가 셸 접근 권한이다.

## 5. 폐기

```bash
hubctl remove-host gpu-server-1     # 로컬 PC: 토큰 삭제
agent-hub revoke                    # 서버: 토큰 해시 삭제, 현재 연결 끊기
```

양쪽 모두 처리해야 완전히 폐기된다. SSH 키는 따로 관리한다. 인증 실패가 자동 재등록을 일으키지 않는다.

## 6. 설정 (`~/.local/state/agent-link/config.toml`)

| 키 | 기본값 | 설명 |
|---|---|---|
| `host_name` | 짧은 hostname | 에이전트 ID의 호스트 부분 |
| `allow_origination` | `none` | 서버 세션이 먼저 보낼 수 있는 범위: `none`(받은 요청에 대한 회신만), `host`(같은 호스트), `all`. 로컬 PC는 항상 `all` |
| `mirror_local_metadata` | `true` | 같은 호스트 안의 전달을 메타데이터만 hub에 보고 |
| `max_content_bytes` | 65536 | 본문 상한 |
| `max_hops` | 3 | hop 상한 |
| `outbox_max_items` / `outbox_max_bytes` | 1000 / 16 MiB | 넘으면 새 메시지를 `outbox_full`로 거부한다(오래된 것을 버리지 않는다) |
| `deliver_timeout` | 60 | 세션 주입 확인 대기(초) |
| `socket_mode` / `socket_group` | `0o600` / 없음 | 한 호스트에서 여러 OS 계정이 daemon을 함께 쓸 때 그룹 권한을 준다(15장 3번, 미검증) |

## 7. 상태 확인

```bash
hubctl hosts            # 호스트별 연결 상태, 재연결 횟수, 마지막 오류
hubctl agents [필터]     # 전체 에이전트
hubctl log --limit 50   # 라우팅된 메시지 메타데이터(본문 없음)
agent-hub status        # 서버: hub 연결, outbox 길이, 에이전트
```

구조화 로그(JSON Lines)는 `~/.local/state/agent-link/hub/hub.log`와 `~/.local/state/agent-link/spoke/spoke.log`에 남는다. 메시지 본문은 기록하지 않는다.
