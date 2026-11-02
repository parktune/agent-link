# agent-link

## 설치

로컬 PC와 각 서버에서 똑같이, 설치할 폴더에서 실행합니다.

```bash
git clone https://github.com/parktune/agent-link.git && cd agent-link && ./setup.sh
```

`setup.sh`가 Python 3.11+ `.venv`를 만들고, Claude Code·Codex에 `/agent-link` 스킬과 MCP 서버를 등록하고, `~/.local/bin`에 `agent-link`를 링크합니다.

## 서버 연결 (로컬 PC에서)

```bash
agent-link hub discover              # ~/.ssh/config 호스트 목록에서 Space로 고르고 Enter
agent-link hub add-host gpu-server-1 # 또는 alias를 직접 지정
```

SSH로 서버에 들어가 daemon을 띄우고, 1회용 초대로 토큰을 등록하고, 로컬 hub를 띄워 연결까지 확인합니다. 호스트 이름은 alias가 됩니다(`--name`으로 변경).
준비할 것은 하나입니다. `ssh gpu-server-1`이 비밀번호 없이 되고, 서버 host key가 known_hosts에 있어야 합니다.

## 업데이트·상태

```bash
agent-link update          # 로컬 PC에서: 서버들 먼저, 그다음 이 PC를 올리고 hub 재시작 (서버에서 실행하면 그 서버만)
agent-link status          # 어디서든: PC면 hub와 서버 연결, 서버면 daemon과 hub 연결, 세션 목록
```

`update`는 `--dry-run`(계획만), `--local-only`(이 머신만), `--hosts a,b`를 받습니다. `status --json`은 전체 정보를 보여 줍니다.

## 사용법

| 할 일 | 명령 |
|---|---|
| 세션을 열면서 등록 | `agent-link claude resume <세션 이름>` · `agent-link codex resume <세션 이름>` |
| 메시지 보내기 | Claude `/agent-link <세션> <내용>` · Codex `$agent-link <세션> <내용>` |
| 받은 메시지 확인 | `/agent-link` (내용 없이) |
| 이 세션 설정 | `/agent-link` 뒤에 말로: 이름 바꾸기, 등록 철회, 박스 감시 켜기·끄기 |
| 등록 지우기 | `agent-link remove <세션 이름>` (어느 머신에서 실행해도 됨) |

```
agent-link claude resume project-head
/agent-link gpu-server-1의 project-head 세션에게 학습 현황 보고를 요청해
$agent-link trainer 체크포인트 경로 알려줘
```

- **세션 이름:** `/rename`으로 붙인 이름을 씁니다. 모든 머신에서 Claude·Codex 통틀어 하나뿐이어야 하고, 겹치면 등록이 거부됩니다. 닫혀 있는 옛 세션이 이름을 쥐고 있으면 `agent-link remove <이름>`으로 지웁니다. 14일 동안 열리지 않은 세션의 등록은 자동으로 지워지고, 다시 열면 다시 등록됩니다.
- **`resume` 옵션:** 이름 대신 세션 ID도 됩니다. `resume`만 쓰면 고르기 화면, `continue`(Codex는 `last`)는 지금 폴더의 최근 대화입니다. CLI에 넘길 인자는 `--` 뒤에 씁니다(`agent-link claude resume project-head -- --model opus`).
- **비동기:** 보낸 세션은 기다리지 않고 하던 일을 계속하며, 회신은 나중에 도착합니다. 서버 세션도 다른 머신의 세션을 이름으로 부를 수 있습니다.
- **무한 핑퐁 방지:** 회신에 다시 회신하는 것은 거부됩니다.

## 받는 방식

| 세션 | 받는 방식 |
|---|---|
| Claude, Channels를 쓸 수 있는 계정 | `agent-link claude resume`이 채널 플래그를 붙여 열어 메시지가 세션에 바로 들어감 |
| Claude, 조직이 Channels를 막은 Team/Enterprise 계정 | 박스에 저장되고, 세션이 띄운 박스 감시가 몇 초 안에 세션을 깨움 |
| Codex | `agent-link codex resume`으로 열면 `codex queue`로 그 대화에 넣음. 안 되면 박스에 쌓이고 `$agent-link`로 확인 |
| 닫혀 있는 세션 | 박스에 저장, 다음에 열었을 때 확인 |

박스는 로컬 PC `<repo>/hub/<tool>-<name>/received/`, 서버 `<repo>/spoke/<tool>-<name>/received/`에 메시지 1건당 Markdown 파일 1개로 쌓입니다.

## 구조

```
[로컬 PC] 세션들 ── hub (agent-link hub serve) ──SSH──┬── [서버 A] daemon (agent-link daemon) ── 세션들
                                                     └── [서버 B] daemon ── 세션들
```

연결은 항상 로컬 PC에서 서버로 SSH를 겁니다. 서버에 SSH 외에 여는 포트는 없습니다. 같은 서버 안의 세션끼리는 hub를 거치지 않습니다. hub와 daemon은 systemd user 서비스로 돕니다.

## 알아두기

- 열린 Codex TUI에 `codex queue`로 메시지가 들어가는지는 아직 실제 세션으로 확인하지 않았습니다.
- 받은 메시지는 다른 에이전트가 보낸 신뢰할 수 없는 입력으로 다룹니다. 세션의 권한 설정이 실제 경계입니다([SECURITY.md](SECURITY.md)).
- 운영 상세(전용 계정, 설정 키, 폐기)는 [운영 가이드](docs/operations.md), 프레임은 [프로토콜](docs/protocol.md), 결정 기록은 [ADR](docs/adr/)에 있습니다.

Apache-2.0.
