# 설치와 운영

지원 범위는 Ubuntu(서버와 로컬 PC)와 Python 3.11 이상이다. 패키지 설치와 daemon 실행은 add-host의 사전 조건이며 자동화하지 않는다.

## 1. 설치 (로컬 PC와 각 서버 공통)

```bash
git clone https://github.com/parktune/agent-link.git && cd agent-link && ./setup.sh
```

`setup.sh`는 아래 순서로 처리한다. 이 저장소와 사용자 설정만 바꾼다. 업데이트는 `agent-link update`로 한다(로컬 PC에서 실행하면 등록된 서버까지 SSH로 올린다). 직접 하려면 `git pull` 후 다시 실행한다.

1. Python 3.11 이상으로 `.venv`를 만든다. python3.13 → 3.12 → 3.11 → python3 순으로 찾고, 없으면 uv가 있을 때 3.12를 받는다.
2. `pip install -e .`
3. `scripts/install.py --replace`로 `/agent-link` 스킬(보내기·박스 확인)을 `~/.claude/skills`, `~/.codex/skills`에 복사하고, 이 저장소를 로컬 플러그인 marketplace(`agent-link`)로 추가해 `agent-link:register`·`agent-link:settings` 스킬이 든 플러그인을 Claude Code와 Codex에 설치한다. 플러그인은 `plugin/` 폴더만 담는다(`.venv`, 박스 폴더는 복사되지 않는다). `claude mcp add --scope user`·`codex mcp add`로 MCP 서버를 등록한다.
4. `~/.local/bin`에 `agent-link hub`, `agent-link`를 링크한다.

## 2. 서버 연결 (`agent-link hub discover` 또는 `agent-link hub add-host <alias>`)

`agent-link hub discover`는 `~/.ssh/config`의 `Host` 항목을 체크리스트로 보여 준다. `Include`된 파일도 읽고, `*`·`?`·`!` 패턴과 `Match` 블록은 제외한다.
↑↓(j/k)로 이동하고 Space로 고르고 Enter로 확정한다. `q`는 아무것도 바꾸지 않고 끝낸다.
이미 등록된 서버는 고를 수 없다. 확정하면 고른 서버마다 아래 `add-host`를 실행하고 결과를 요약한다. 터미널이 없으면 `--hosts a,b`로 지정한다.

사전 조건은 다음과 같다. agent-link가 대신 만들지 않는다.
- `~/.ssh/config`에 alias가 있다(ProxyJump 포함 가능).
- 비대화형 키 인증이 된다.
- 서버의 host key fingerprint를 별도 경로로 확인해서 known_hosts에 넣어 두었다.
- 서버에 1단계가 끝나 있다.

```bash
agent-link hub add-host gpu-server-1
```

| 단계 | 내용 |
|---|---|
| 1/4 | DNS와 TCP를 점검한다. ProxyJump·ProxyCommand를 쓰면 건너뛰고 SSH 단계에서 판단한다 |
| 2/4 | SSH 한 번으로 서버의 `agent-link`를 찾는다(PATH, 그다음 `setup.sh`가 만든 `~/.local/bin/agent-link` 링크. clone 위치는 상관없다). `agent-link up`으로 daemon이 꺼져 있으면 띄우고(systemd user 서비스 `agent-link-spoke`, 실패하면 백그라운드 프로세스), 1회용 초대를 받는다. 코드는 SSH 파이프로 hub 메모리에만 들어온다 |
| 3/4 | 256-bit 토큰을 만들어 `enroll`하고 `hello`로 확인한다. 서버에는 해시만 저장된다 |
| 4/4 | 로컬 hub가 꺼져 있으면 띄우고(`agent-link-hub` 서비스, 실패하면 백그라운드) 새 호스트에 연결됐는지 확인한다 |

- 호스트 이름(에이전트 ID의 앞부분)은 페어링할 때 한 번 정해진다. 후보는 alias이고 `--name`으로 바꿀 수 있다. 서버의 `config.toml`에 `host_name`이 있으면 그것이 우선이고, 이미 돌고 있는 daemon이 있으면 그 이름을 쓴다. hub는 서버가 `enroll_ok`로 알려 준 이름을 저장한다.
- 서버는 페어링한 이름을 토큰 옆에 저장하고(0.5.7), daemon은 뜰 때마다 `config.toml`의 `host_name` → 페어링한 이름 → `--host-name` → 짧은 hostname 순으로 고른다. 그래서 `agent-link up`을 이름 없이 다시 실행해도 hub가 아는 이름으로 뜬다. 0.5.7 전에 페어링한 서버는 hub의 다음 `hello`에서 이름을 받아 채운다.
- 그래도 이름이 어긋나면(예: `config.toml`의 `host_name`을 나중에 바꿈) 연결이 `host_mismatch`로 거부되고, `agent-link hub hosts`의 `last_error_detail`과 서버 로그에 고치는 방법이 나온다. 서버에서 `agent-link up --restart`로 페어링한 이름으로 다시 띄우거나, 이름을 바꾸려면 `agent-link hub remove-host` 후 다시 `add-host`한다.
- `agent-link`가 다른 곳에 있으면 `--remote-path`로 지정한다.
- systemd user 서비스는 로그아웃 후에도 유지하려면 linger가 필요하다(`loginctl enable-linger $USER`, 관리자 정책 확인). 백그라운드 프로세스는 재부팅하면 사라지지만, 다음 `agent-link hub add-host`나 `agent-link up`이 다시 띄운다.
- 한 머신은 hub와 서버 daemon 중 하나만 맡는다(소켓이 하나다). hub가 있는 PC에서 `agent-link up`/`daemon`을, 서버로 등록된 머신에서 `agent-link hub up`/`serve`/`add-host`/`discover`를 실행하면 거부하고 맞는 명령을 안내한다. 정말 필요하면 `--force`를 붙인다.
- 수동으로 띄우려면 서버에서 `agent-link up`(또는 포그라운드 `agent-link daemon`), 로컬에서 `agent-link hub up`(또는 `agent-link hub serve`)을 쓴다.

권장 구성은 서버에 전용 OS 계정을 두고, 그 계정의 `authorized_keys`에 hub 전용 키를 강제 명령으로 등록하는 것이다.

```
restrict,command="/home/svc/agent-link/.venv/bin/agent-link endpoint" ssh-ed25519 AAAA... hub
```

이 경우 셸 접근이 되는 별도 alias로 초대만 발급한다: `--admin-alias gpu1-admin`.

일반 셸 권한이 있는 SSH 계정을 쓰면 endpoint의 허용 목록은 그 계정 전체를 sandbox하지 못한다. SSH 키 자체가 셸 접근 권한이다.

## 3. 폐기

```bash
agent-link hub remove-host gpu-server-1     # 로컬 PC: 토큰 삭제
agent-link revoke                    # 서버: 토큰 해시 삭제, 현재 연결 끊기
```

양쪽 모두 처리해야 완전히 폐기된다. SSH 키는 따로 관리한다. 인증 실패가 자동 재등록을 일으키지 않는다.

## 4. 설정 (`~/.local/state/agent-link/config.toml`)

| 키 | 기본값 | 설명 |
|---|---|---|
| `host_name` | 페어링한 이름, 없으면 짧은 hostname | 에이전트 ID의 호스트 부분. 정하면 페어링한 이름보다 우선한다 |
| `allow_origination` | `all` | 서버 세션이 먼저 보낼 수 있는 범위: `all`(모든 호스트), `host`(같은 호스트), `none`(받은 요청에 대한 회신만). 로컬 PC는 항상 `all`. 0.6.5 전에는 기본값이 `none`이었다 |
| `max_content_bytes` | 65536 | 본문 상한 |
| `max_hops` | 3 | hop 상한 |
| `outbox_max_items` / `outbox_max_bytes` | 1000 / 16 MiB | 넘으면 새 메시지를 `outbox_full`로 거부한다(오래된 것을 버리지 않는다) |
| `deliver_timeout` | 60 | 세션 주입 확인 대기(초) |
| `socket_mode` / `socket_group` | `0o600` / 없음 | 한 호스트에서 여러 OS 계정이 daemon을 함께 쓸 때 그룹 권한을 준다(15장 3번, 미검증) |

## 5. 상태 확인

```bash
agent-link status       # 어디서든: PC면 hub와 서버 연결, 서버면 daemon과 hub 연결
agent-link hub hosts            # 호스트별 연결 상태, 재연결 횟수, 마지막 오류
agent-link hub agents [필터]     # 전체 에이전트
agent-link hub log --limit 50   # 라우팅된 메시지 메타데이터(본문 없음)
agent-link status        # 서버: hub 연결과 그 hub의 이름·사용자·접속 주소, 등록된 hub 목록, outbox 길이, 에이전트
```

구조화 로그(JSON Lines)는 `~/.local/state/agent-link/hub/hub.log`와 `~/.local/state/agent-link/spoke/spoke.log`에 남는다. 메시지 본문은 기록하지 않는다.

`agent-link status`의 `hub`에는 지금 연결된 hub의 `name`(hostname), `user`, `from`(SSH 접속 주소), `connected_since`, `enrolled_at`이 나온다. `name`과 `user`는 hub가 스스로 밝힌 값이라 확인용일 뿐 인증에는 쓰지 않는다. 인증은 토큰으로만 한다. `from`은 서버의 SSH endpoint가 직접 채운다. 0.5.5 이전에 등록한 hub는 연결 뒤 `name`·`user`가 보이고, `enrolled_hubs`에는 이름 없이 등록 시각만 나온다.

## 0.6.0으로 올릴 때 (`hubctl`, `agent-hub` → `agent-link`)

0.6.0부터 명령은 `agent-link` 하나다. 로컬 PC의 hub는 `agent-link hub …`, 서버는 `agent-link …`를 쓴다. `hubctl`과 `agent-hub`는 없어졌다.

1. **서버를 먼저 올린다.** 각 서버의 clone 폴더에서 `git pull && ./setup.sh`를 실행한다. `setup.sh`는 옛 `agent-link-spoke` 서비스를 `agent-link daemon`으로 바꿔 다시 시작하고 옛 링크를 지운다. 백그라운드로 떠 있던 daemon은 그대로 두며, 다음 `agent-link up --restart` 때 바뀐다.
2. **그다음 로컬 PC를 올린다.** `git pull && ./setup.sh`가 hub 서비스를 `agent-link hub serve`로 바꿔 다시 시작한다. 서비스 파일이 이미 새 명령이면 `agent-link hub up --restart`를 직접 실행한다. hub는 다시 시작할 때 저장된 서버 경로 `…/agent-hub`를 같은 폴더의 `…/agent-link`로 바꾸므로 서버를 다시 등록하지 않아도 된다.
3. `authorized_keys`에 강제 명령을 적용했다면 `…/agent-hub endpoint`를 `…/agent-link endpoint`로 고친다.

서버를 올렸는데 hub가 아직 옛 버전이면, hub는 서버에서 `agent-hub`를 찾지 못해 연결이 끊긴다. 2단계를 마치면 다시 붙는다.

