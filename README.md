# agent-link

여러 서버와 로컬 PC에서 도는 Claude Code·Codex 세션이 서로 메시지를 주고받게 합니다.
서로 다른 구독 계정의 세션도 함께 쓸 수 있습니다. 로컬 PC의 hub가 SSH로 각 서버에 붙는 hub-and-spoke 구조입니다.

```
[로컬 PC]  Claude/Codex 세션 ── hubctl serve (hub) ──SSH──┬── [서버 A] agent-hub daemon ── 세션들
                                                          └── [서버 B] agent-hub daemon ── 세션들
```

## 사용법

Claude에서는 `/agent-link`, Codex에서는 `$agent-link` 뒤에 받을 세션과 내용을 씁니다.

```
/agent-link gpu-server-1의 project-head 세션에게 학습 현황 보고를 요청해
$agent-link trainer 체크포인트 경로 알려줘
/agent-link            ← 받은 메시지(박스) 확인
```

- 세션에서 처음 쓰면 그 세션을 등록합니다. 이름은 따로 말하지 않으면 `/rename`으로 붙인 세션 이름을 쓰고, 세션 이름이 없을 때만 묻습니다. 세션 ID는 `호스트:도구:이름`입니다(예: `gpu-server-1:claude:project-head`, 로컬 PC는 `local:claude:main`).
- 보낸 세션은 기다리지 않고 하던 일을 계속합니다. 회신은 나중에 도착합니다.
- **받는 쪽:** 세션이 열려 있으면 그 세션에 바로 들어갑니다.
  - Claude: `claude --dangerously-load-development-channels server:agent-link`로 시작한 세션이어야 합니다.
  - Codex: 스레드 ID 또는 `/rename` 이름으로 등록한 세션이어야 합니다(`codex queue`).
- 바로 넣을 수 없으면 박스에 저장합니다. 세션이 닫혀 있거나, push가 안 되는 세션이거나, push가 실패한 경우입니다.
  - 로컬 PC: `<repo>/hub/<tool>-<name>/received/*.md`
  - 서버: `<repo>/spoke/<tool>-<name>/received/*.md`
  - 메시지 1건이 Markdown 파일 1개입니다. `/agent-link`로 확인하면 `received/read/`로 옮깁니다.
- 서버 세션은 기본적으로 **받은 요청에 대한 회신만** 보낼 수 있습니다(`allow_origination`). 회신에 다시 회신하는 것은 거부합니다(에이전트 간 무한 핑퐁 방지).

## 설치와 연결

**1. 로컬 PC와 각 서버에서 똑같이** 실행합니다.

```bash
git clone https://github.com/parktune/agent-link.git ~/work/agent-link && ~/work/agent-link/setup.sh
```

`setup.sh`가 하는 일은 다음과 같습니다.
- Python 3.11 이상으로 `.venv`를 만들고 패키지를 설치합니다. 시스템에 없으면 uv가 있을 때 uv로 받습니다.
- Claude Code·Codex에 스킬과 MCP 서버를 등록합니다.
- `~/.local/bin`에 `hubctl`, `agent-hub`를 링크합니다.

업데이트할 때는 `git pull` 후 다시 실행합니다.

**2. 로컬 PC에서** `~/.ssh/config`의 alias로 서버를 연결합니다.

```bash
hubctl add-host gpu-230
```

이 한 줄이 SSH로 서버에 들어가 다음을 모두 처리합니다.
- `agent-hub`를 찾습니다(`~/work/agent-link`, `~/agent-link`, PATH 순).
- 서버 daemon이 꺼져 있으면 띄웁니다(systemd user 서비스, 안 되면 백그라운드).
- 1회용 초대로 호스트 토큰을 등록합니다.
- 로컬 hub가 꺼져 있으면 띄우고 연결까지 확인합니다.

호스트 이름은 alias가 됩니다(`--name`으로 변경). 서버마다 이 명령만 반복하면 됩니다.

준비되어 있어야 하는 것은 하나입니다. `ssh gpu-230`이 비밀번호 없이 되고 서버 host key가 known_hosts에 있어야 합니다. agent-link는 host key를 자동으로 받아들이지 않습니다.
마지막에 출력되는 `authorized_keys` 한 줄은 전용 계정을 쓸 때의 선택적 보안 강화입니다. 전용 계정, 폐기, 설정은 [운영 가이드](docs/operations.md)에 있습니다.

## 확인 명령

```bash
hubctl hosts | hubctl agents | hubctl log      # 로컬 PC
agent-hub status                                # 서버
```

## 상태

- **구현:** hub, spoke daemon, SSH endpoint, add-host, 레지스트리, 같은 호스트 우선 라우팅, Claude channel 어댑터, Codex 어댑터, 박스 폴백.
- **자동 테스트:** 29개(모델 호출 없음). 명세 14장 S1–S17 중 S3·S8을 뺀 시나리오를 다룹니다. `scripts/verify_ssh_loopback.py`는 실제 OpenSSH로 add-host와 왕복을 확인합니다.
- **미검증:** 실제 Claude 세션에 channel 주입, 열린 Codex TUI에 `codex queue` 주입, 서로 다른 물리 서버. [수동 시나리오](docs/manual-scenarios.md)의 M0 항목부터 확인합니다.
- 조사 결과와 명세와 달라진 점은 [M0 보고서](docs/spike-report.md), 결정은 [ADR](docs/adr/), 프레임은 [프로토콜](docs/protocol.md)에 있습니다.

명세: [docs/spec.md](docs/spec.md). 이전 버전(SSH mailbox, `codex_bridge`)은 git 기록에만 있습니다.

Apache-2.0.
