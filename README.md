# agent-link

## 설치와 연결

**1. 로컬 PC와 각 서버에서 똑같이**, 설치하고 싶은 폴더에서 실행합니다. 경로는 어디든 됩니다.

```bash
git clone https://github.com/parktune/agent-link.git && cd agent-link && ./setup.sh
```

`setup.sh`가 하는 일은 다음과 같습니다.
- Python 3.11 이상으로 `.venv`를 만들고 패키지를 설치합니다. 시스템에 없으면 uv가 있을 때 uv로 받습니다.
- Claude Code·Codex에 스킬과 MCP 서버를 등록합니다.
- `~/.local/bin`에 `agent-link hub`, `agent-link`를 링크합니다.

업데이트는 로컬 PC에서 `agent-link update` 한 번이면 됩니다. 등록된 서버를 먼저 SSH로 올리고(`git pull`, `setup.sh`, daemon 재시작) 이 PC를 올린 뒤 hub를 다시 시작합니다. 서버에서 실행하면 그 서버만 올립니다. `--dry-run`으로 계획만, `--local-only`로 이 머신만 올립니다. 0.5.x에서 0.6.0으로 올릴 때는 서버를 먼저 올립니다([운영 가이드](docs/operations.md#060으로-올릴-때-hubctl-agent-hub--agent-link)).

**2. 로컬 PC에서** 서버를 연결합니다. 고르는 방식과 직접 지정하는 방식이 있습니다.

```bash
agent-link hub discover           # ~/.ssh/config 호스트 목록에서 Space로 고르고 Enter로 확정
agent-link hub add-host gpu-server-1   # 또는 alias를 직접 지정
```

`discover`는 이미 등록된 서버를 `[=] (등록됨)`으로 표시합니다. 확정하면 고른 서버마다 아래 `add-host` 과정을 차례로 실행하고 결과를 요약합니다. 터미널이 아닌 곳에서는 `--hosts gpu-server-1,gpu-server-2`으로 지정합니다.

이 한 줄이 SSH로 서버에 들어가 다음을 모두 처리합니다.
- `agent-link`를 찾습니다. 서버의 `setup.sh`가 만든 `~/.local/bin/agent-link` 링크나 PATH로 찾으므로 clone 위치는 상관없습니다.
- 서버 daemon이 꺼져 있으면 띄웁니다(systemd user 서비스, 안 되면 백그라운드).
- 1회용 초대로 호스트 토큰을 등록합니다.
- 로컬 hub가 꺼져 있으면 띄우고 연결까지 확인합니다.

호스트 이름은 alias가 됩니다(`--name`으로 변경). 서버마다 이 명령만 반복하면 됩니다.

준비되어 있어야 하는 것은 하나입니다. `ssh gpu-server-1`이 비밀번호 없이 되고 서버 host key가 known_hosts에 있어야 합니다. agent-link는 host key를 자동으로 받아들이지 않습니다.
마지막에 출력되는 `authorized_keys` 한 줄은 전용 계정을 쓸 때의 선택적 보안 강화입니다. 전용 계정, 폐기, 설정은 [운영 가이드](docs/operations.md)에 있습니다.

## 확인 명령

```bash
agent-link status          # 로컬 PC와 서버 어디서든 연결 상태 요약
agent-link status --json   # 재연결 횟수·마지막 오류까지 전부
```

- 로컬 PC에서는 hub 상태를 보여 줍니다. 연결된 서버 수와 서버별 연결 여부(끊겼으면 그 이유), 전체 세션 목록이 나옵니다.
- 서버에서는 daemon 상태를 보여 줍니다. hub 연결 여부, 연결된 hub의 이름·접속 주소, 그 서버의 세션 목록이 나옵니다.
- 더 자세히 보려면 로컬 PC에서 `agent-link hub hosts`, `agent-link hub agents`, `agent-link hub log`를 씁니다.

## 사용법

`setup.sh`가 Claude Code·Codex에 명령 세 개를 설치합니다.

| 스킬 | Claude | Codex | 하는 일 |
|---|---|---|---|
| 등록 | `/agent-link:register [이름]` | `$agent-link:register [이름]` | 이 세션을 등록합니다(이름을 안 주면 `/rename`으로 붙인 세션 이름). 플래그 없이 연 Claude 세션이면 채널 플래그를 붙여 같은 세션 이름으로 resume하는 명령을 알려 줍니다 |
| 설정 | `/agent-link:settings [동작]` | `$agent-link:settings [동작]` | 동작 없이 쓰면 현재 설정을 보여 줍니다. `register <이름>` 다른 이름으로 다시 등록, `unregister` 등록 철회, `watch on`·`watch off` 우편함 감시 시작·종료(플래그 없이 연 Claude 세션) |
| 보내기·확인 | `/agent-link <세션> <내용>` | `$agent-link <세션> <내용>` | 메시지를 보냅니다. 내용 없이 쓰면 받은 메시지(박스)를 확인합니다 |

```
/agent-link:register
/agent-link gpu-server-1의 project-head 세션에게 학습 현황 보고를 요청해
$agent-link trainer 체크포인트 경로 알려줘
/agent-link                 ← 받은 메시지(박스) 확인
```

- 등록하지 않은 세션에서 `/agent-link`를 쓰면 세션 이름(`/rename`)으로 자동 등록하고 진행합니다. 세션 이름이 없을 때만 `/agent-link:register`를 먼저 쓰라고 안내합니다. 이 세션에서 등록을 철회했으면 다시 등록하지 않습니다. 세션 ID는 `호스트:도구:이름`입니다(예: `gpu-server-1:claude:project-head`, 로컬 PC는 `local:claude:main`).
- 보낸 세션은 기다리지 않고 하던 일을 계속합니다. 회신은 나중에 도착합니다.
- **받는 쪽:** 세션이 열려 있으면 그 세션에 바로 들어갑니다.
  - Claude: 가장 쉬운 방법은 `agent-link --resume <세션 이름>`입니다. `/rename`한 이름(또는 세션 ID)으로 세션을 찾아 그 폴더에서 다시 열면서 계정에 맞는 수신 방식을 고릅니다. Channels를 쓸 수 있는 계정이면 채널 플래그를 붙여 push로 받고, 조직이 Channels를 막은 Team/Enterprise 계정이면 플래그 없이 열고 첫 입력으로 박스 감시를 켜서 메일이 오면 세션이 깨어나게 합니다. 이름 없이 `agent-link --resume`은 고르기 화면, `agent-link --continue`는 지금 폴더의 가장 최근 대화입니다. claude에 넘길 인자는 `--` 뒤에 씁니다(`agent-link --resume project-head -- --model opus`). 새 세션은 `claude --dangerously-load-development-channels server:agent-link`로 엽니다. 플래그 없이 연 세션에서 `/agent-link:register`를 쓰면 다시 열 명령을 알려 줍니다.
  - Codex: 스레드 ID 또는 `/rename` 이름으로 등록한 세션이어야 합니다(`codex queue`).
- 바로 넣을 수 없으면 박스에 저장합니다. 세션이 닫혀 있거나, push가 안 되는 세션이거나, push가 실패한 경우입니다.
  - 플래그 없이 연 Claude 세션은 등록할 때(또는 첫 `/agent-link`) 박스 감시를 백그라운드로 겁니다(`agent-link wait-box`). 메시지가 박스에 들어오면 몇 초 안에 세션이 깨어나 읽습니다. 세션이 열려 있는 동안만 동작합니다. `/agent-link:settings watch off`로 끄면 그 이름에는 다시 걸지 않고, `watch on`으로 다시 켭니다.
  - 로컬 PC: `<repo>/hub/<tool>-<name>/received/*.md`
  - 서버: `<repo>/spoke/<tool>-<name>/received/*.md`
  - 메시지 1건이 Markdown 파일 1개입니다. `/agent-link`로 확인하면 `received/read/`로 옮깁니다.
- 서버 세션도 다른 호스트의 세션을 이름으로 부를 수 있습니다(예: "agent-link-test-head에게 자료를 요청해"). hub가 전체 세션 목록을 각 서버에 알려 줍니다. 서버 세션이 받은 요청에만 회신하게 하려면 그 서버의 `config.toml`에 `allow_origination = "none"`을 둡니다. 회신에 다시 회신하는 것은 거부합니다(에이전트 간 무한 핑퐁 방지).

## 구조
여러 서버와 로컬 PC에서 도는 Claude Code·Codex 세션이 서로 메시지를 주고받게 합니다.
서로 다른 구독 계정의 세션도 함께 쓸 수 있습니다. 로컬 PC의 hub가 SSH로 각 서버에 붙는 hub-and-spoke 구조입니다.

```
[로컬 PC]  Claude/Codex 세션 ── agent-link hub serve (hub) ──SSH──┬── [서버 A] agent-link daemon ── 세션들
                                                          └── [서버 B] agent-link daemon ── 세션들
```

## 상태

- **구현:** hub, spoke daemon, SSH endpoint, add-host, 레지스트리, 같은 호스트 우선 라우팅, Claude channel 어댑터, Codex 어댑터, 박스 폴백.
- **자동 테스트:** 42개(모델 호출 없음). 명세 14장 S1–S17 중 S3·S8을 뺀 시나리오를 다룹니다. `scripts/verify_ssh_loopback.py`는 실제 OpenSSH로 add-host와 왕복을 확인합니다.
- **미검증:** 실제 Claude 세션에 channel 주입, 열린 Codex TUI에 `codex queue` 주입, 서로 다른 물리 서버. [수동 시나리오](docs/manual-scenarios.md)의 M0 항목부터 확인합니다.
- 조사 결과와 명세와 달라진 점은 [M0 보고서](docs/spike-report.md), 결정은 [ADR](docs/adr/), 프레임은 [프로토콜](docs/protocol.md)에 있습니다.

명세: [docs/spec.md](docs/spec.md). 이전 버전(SSH mailbox, `codex_bridge`)은 git 기록에만 있습니다.

Apache-2.0.
