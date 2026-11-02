# 설치와 운영

지원 범위는 Ubuntu(서버와 로컬 PC)와 Python 3.11 이상이다. 패키지 설치와 daemon 실행은 add-host의 사전 조건이며 자동화하지 않는다.

## 1. 설치 (로컬 PC와 각 서버 공통)

```bash
git clone https://github.com/parktune/agent-link.git ~/work/agent-link && ~/work/agent-link/setup.sh
```

`setup.sh`는 아래 순서로 처리한다. 이 저장소와 사용자 설정만 바꾼다. 업데이트할 때는 `git pull` 후 다시 실행한다.

1. Python 3.11 이상으로 `.venv`를 만든다. python3.13 → 3.12 → 3.11 → python3 순으로 찾고, 없으면 uv가 있을 때 3.12를 받는다.
2. `pip install -e .`
3. `scripts/install.py --replace`로 `~/.claude/skills`, `~/.codex/skills`에 스킬을, `claude mcp add --scope user`·`codex mcp add`로 MCP 서버를 등록한다.
4. `~/.local/bin`에 `hubctl`, `agent-hub`를 링크한다.

## 2. 서버 연결 (`hubctl discover` 또는 `hubctl add-host <alias>`)

`hubctl discover`는 `~/.ssh/config`의 `Host` 항목을 체크리스트로 보여 준다. `Include`된 파일도 읽고, `*`·`?`·`!` 패턴과 `Match` 블록은 제외한다.
↑↓(j/k)로 이동하고 Space로 고르고 Enter로 확정한다. `q`는 아무것도 바꾸지 않고 끝낸다.
이미 등록된 서버는 고를 수 없다. 확정하면 고른 서버마다 아래 `add-host`를 실행하고 결과를 요약한다. 터미널이 없으면 `--hosts a,b`로 지정한다.

사전 조건은 다음과 같다. agent-link가 대신 만들지 않는다.
- `~/.ssh/config`에 alias가 있다(ProxyJump 포함 가능).
- 비대화형 키 인증이 된다.
- 서버의 host key fingerprint를 별도 경로로 확인해서 known_hosts에 넣어 두었다.
- 서버에 1단계가 끝나 있다.

```bash
hubctl add-host gpu-230
```

| 단계 | 내용 |
|---|---|
| 1/4 | DNS와 TCP를 점검한다. ProxyJump·ProxyCommand를 쓰면 건너뛰고 SSH 단계에서 판단한다 |
| 2/4 | SSH 한 번으로 서버의 `agent-hub`를 찾는다(PATH, `~/work/agent-link`, `~/agent-link`, `~/.local/bin` 순). `agent-hub up`으로 daemon이 꺼져 있으면 띄우고(systemd user 서비스 `agent-link-spoke`, 실패하면 백그라운드 프로세스), 1회용 초대를 받는다. 코드는 SSH 파이프로 hub 메모리에만 들어온다 |
| 3/4 | 256-bit 토큰을 만들어 `enroll`하고 `hello`로 확인한다. 서버에는 해시만 저장된다 |
| 4/4 | 로컬 hub가 꺼져 있으면 띄우고(`agent-link-hub` 서비스, 실패하면 백그라운드) 새 호스트에 연결됐는지 확인한다 |

- 호스트 이름(에이전트 ID의 앞부분)은 alias다. `--name`으로 바꿀 수 있다. 서버의 `config.toml`에 `host_name`이 있으면 그것이 우선이고, 이미 돌고 있는 daemon이 있으면 그 이름을 쓴다.
- `agent-hub`가 다른 곳에 있으면 `--remote-path`로 지정한다.
- systemd user 서비스는 로그아웃 후에도 유지하려면 linger가 필요하다(`loginctl enable-linger $USER`, 관리자 정책 확인). 백그라운드 프로세스는 재부팅하면 사라지지만, 다음 `hubctl add-host`나 `agent-hub up`이 다시 띄운다.
- 수동으로 띄우려면 서버에서 `agent-hub up`(또는 포그라운드 `agent-hub daemon`), 로컬에서 `hubctl up`(또는 `hubctl serve`)을 쓴다.

권장 구성은 서버에 전용 OS 계정을 두고, 그 계정의 `authorized_keys`에 hub 전용 키를 강제 명령으로 등록하는 것이다.

```
restrict,command="/home/svc/agent-link/.venv/bin/agent-hub endpoint" ssh-ed25519 AAAA... hub
```

이 경우 셸 접근이 되는 별도 alias로 초대만 발급한다: `--admin-alias gpu1-admin`.
SSH로 명령을 실행할 수 없으면 서버에서 `agent-hub invite`로 코드를 받아 신뢰된 경로로 옮기고 `hubctl add-host gpu1 --remote-path ... --code`의 비표시 프롬프트에 입력한다.
스트림을 유지할 수 없는 네트워크에서는 `--mode poll`을 쓴다.

일반 셸 권한이 있는 SSH 계정을 쓰면 endpoint의 허용 목록은 그 계정 전체를 sandbox하지 못한다. SSH 키 자체가 셸 접근 권한이다.

## 3. 폐기

```bash
hubctl remove-host gpu-server-1     # 로컬 PC: 토큰 삭제
agent-hub revoke                    # 서버: 토큰 해시 삭제, 현재 연결 끊기
```

양쪽 모두 처리해야 완전히 폐기된다. SSH 키는 따로 관리한다. 인증 실패가 자동 재등록을 일으키지 않는다.

## 4. 설정 (`~/.local/state/agent-link/config.toml`)

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

## 5. 상태 확인

```bash
hubctl hosts            # 호스트별 연결 상태, 재연결 횟수, 마지막 오류
hubctl agents [필터]     # 전체 에이전트
hubctl log --limit 50   # 라우팅된 메시지 메타데이터(본문 없음)
agent-hub status        # 서버: hub 연결, outbox 길이, 에이전트
```

구조화 로그(JSON Lines)는 `~/.local/state/agent-link/hub/hub.log`와 `~/.local/state/agent-link/spoke/spoke.log`에 남는다. 메시지 본문은 기록하지 않는다.
