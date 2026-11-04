# agent-link

## 설치

로컬 PC(hub)와 각 서버(spoke)에서 똑같이, 설치할 폴더에서 실행합니다. 서버에서는 이것으로 끝입니다.

```bash
git clone https://github.com/parktune/agent-link.git && cd agent-link && ./setup.sh
```

`setup.sh`는 다음을 처리합니다.
- Python 3.11+로 `.venv`를 만듭니다.
- Claude Code·Codex에 `/agent-link` 스킬과 MCP 서버를 등록합니다.
- Claude Code의 `~/.claude/settings.json`에 박스 감시 hook(SessionStart·Stop)을 추가합니다. 기존 hook은 그대로 둡니다.
- `~/.local/bin`에 `agent-link`를 링크합니다.

## 서버 연결 (로컬 PC에서)

```bash
agent-link hub discover
```

**호스트 목록의 출처**
`~/.ssh/config`에 적어 둔 `Host` 항목(alias)을 체크리스트로 보여 줍니다. `Include`된 파일도 읽고, `*` 같은 패턴 항목은 뺍니다.
- 연결할 서버를 ↑↓로 고르고 Space로 선택한 뒤 Enter로 확정합니다. `q`는 아무것도 바꾸지 않고 끝냅니다. 이미 연결된 서버는 `(등록됨)`으로 표시됩니다.
- 연결을 끊을 서버는 `(등록됨)` 서버에서 Space를 눌러 `(해제 예정)`으로 바꾸고 Enter를 누릅니다. 한 번 더 확인한 뒤 hub에서 지우고 그 서버의 토큰도 SSH로 지웁니다.
- 접속에는 그 alias에 적힌 `HostName`, `User`, `IdentityFile`, `ProxyJump`가 그대로 쓰입니다. agent-link가 따로 주소나 계정을 묻지 않습니다.
- 서버 이름(세션 ID의 앞부분)은 alias가 됩니다. 예를 들어 `Host gpu-server-1`이면 `gpu-server-1:claude:<세션>`입니다.

**고른 서버마다 일어나는 일**
SSH로 들어가 daemon을 띄우고, 1회용 초대로 토큰을 등록하고, 로컬 hub를 띄워 연결까지 확인합니다. 서버마다 한 번만 하면 되고, hub와 daemon은 systemd user 서비스로 등록돼 재부팅 후에도 다시 뜹니다.

**미리 준비할 것** (서버마다)
- 그 서버에 위의 설치가 끝나 있어야 합니다.
- `ssh <alias>`가 SSH 키로 비밀번호 입력 없이 되어야 합니다. hub는 백그라운드에서 다시 접속하므로 비밀번호를 받을 수 없습니다.
- 서버 host key가 known_hosts에 있어야 합니다. agent-link는 처음 보는 host key를 자동으로 받아들이지 않습니다.

준비 방법(키 등록, passphrase 키와 ssh-agent, host key, 오류별 해결)은 [SSH 준비](docs/ssh-setup.md)에 있습니다. `ssh -o BatchMode=yes <alias> true`가 성공하면 준비된 상태입니다.

연결되면 `agent-link status`에 `servers N/N connected`가 나옵니다. 이후 세션은 아래 사용법대로 `agent-link claude|codex resume <세션 이름>`으로 엽니다.

## 업데이트·상태

```bash
agent-link update          # 로컬 PC에서: 서버들 먼저, 그다음 이 PC를 올리고 hub 재시작 (서버에서 실행하면 그 서버만)
agent-link status          # 어디서든: PC면 hub와 서버 연결, 서버면 daemon과 hub 연결, 세션 목록
```

`update`는 `--dry-run`(계획만), `--local-only`(이 머신만), `--hosts a,b`를 받습니다. `status --json`은 전체 정보를 보여 줍니다.

## 사용법

| 할 일 | 명령 |
|---|---|
| 새 세션을 만들면서 등록 | `agent-link claude register <새 이름>` · `agent-link codex register <새 이름>` |
| 기존 세션을 열면서 등록 | `agent-link claude resume <세션 이름>` · `agent-link codex resume <세션 이름>` |
| 메시지 보내기 | Claude `/agent-link <세션> <내용>` · Codex `$agent-link <세션> <내용>` |
| 받은 메시지 확인 | `/agent-link` (내용 없이) |
| 이 세션 설정 | `/agent-link` 뒤에 말로: 이름 바꾸기, 등록 철회, 박스 감시 켜기·끄기 |
| 등록 해제 | `agent-link unregister <세션 이름 또는 전체 ID>` (어느 머신에서 실행해도 됨) |

```
agent-link claude resume project-head
/agent-link gpu-server-1의 project-head 세션에게 학습 현황 보고를 요청해
$agent-link trainer 체크포인트 경로 알려줘
```

- **세션 이름:** `/rename`으로 붙인 이름을 씁니다. 세션 ID는 `<서버>:<종류>:<이름>`이고, 서버·종류·이름이 모두 같은 등록만 거부됩니다. 다른 서버나 다른 종류(Claude·Codex)는 같은 이름을 쓸 수 있습니다.
- **같은 이름이 여럿일 때:** 이름만(또는 `claude:<이름>`만) 주고 보내면 보내는 세션과 같은 서버의 세션이 먼저입니다. 같은 서버에 없고 다른 서버 여러 곳에 있으면 `ambiguous`로 거부되며 후보의 전체 ID가 안내됩니다. 이때는 "gpu-server-2의 codex trainer"처럼 서버와 종류를 말하면 됩니다.
- **등록 해제:** `agent-link unregister <이름 | 종류:이름 | 서버:종류:이름>`(예: `local:claude:project-head`). 어느 머신에서 실행해도 모든 머신의 등록을 확인하고, 여러 개가 맞으면 지우지 않고 후보의 전체 ID를 보여 줍니다. 닫혀 있는 옛 세션이 이름을 쥐고 있을 때도 이렇게 해제합니다.
- **이름 바꾸기:** 등록은 이름이 아니라 세션 ID로 추적합니다. 열린 세션에서 `/rename`하면 몇 초 안에 등록이 새 이름으로 옮겨지고, 박스의 메시지도 따라갑니다. 옛 이름으로는 더 이상 받지 않습니다. 새 이름이 이미 쓰이고 있으면 옛 이름을 유지합니다.
- **`register`:** 그 이름으로 새 세션을 만들어 열고 바로 등록합니다. Codex 세션은 Codex app-server(`codex queue`가 쓰는 백그라운드 서비스)로 이름 붙은 세션을 만든 뒤 엽니다. 같은 종류의 세션이 이미 그 이름을 쓰고 있으면 거부하고 `resume`을 안내합니다.
- **자동 해제:** 닫힌 세션은 15일 동안 열리지 않았거나, 15일 동안 메시지를 보내거나 받지 않았으면 등록이 해제됩니다. 열려 있는 세션은 해제하지 않습니다. 해제된 세션은 기록이 남지 않으므로, 다시 쓰려면 `register`·`resume`으로 열거나 세션에서 `/agent-link`로 직접 등록합니다.
- **`resume` 옵션:** 이름 대신 세션 ID도 됩니다. `resume`만 쓰면 고르기 화면, `continue`(Codex는 `last`)는 지금 폴더의 최근 대화입니다. CLI에 넘길 인자는 `--` 뒤에 씁니다(`agent-link claude resume project-head -- --model opus`).
- **백그라운드로 보낸 Claude 세션:** Claude Code 는 세션을 백그라운드로 보낼 때 새 ID로 이어 씁니다. `agent-link claude resume <이름|옛 ID>` 는 그 이어진 최신 세션을 엽니다. 그 세션이 백그라운드 세션이면(멈춘 것 포함) `--resume` 대신 `claude attach <작업 ID>`(세션 ID 앞 8자리)로 열어 사본이 또 생기지 않게 합니다. 새 ID로 이어진 세션은 옛 ID의 닫힌 등록을 이름과 박스째 넘겨받습니다(세션 기록의 `continued-in`으로 확인). 이름으로 열 때는 세션의 현재 이름(가장 최근에 이어진 세션의 마지막 `/rename`)만 찾고, 옛 이름을 주면 지금 이름을 알려 줍니다. 세션 ID로는 언제든 열 수 있습니다. 이름 없이 `resume`만 쓰는 고르기 화면은 Claude Code 것이라 옛 ID도 그대로 보입니다.
- **비동기:** 보낸 세션은 기다리지 않고 하던 일을 계속하며, 회신은 나중에 도착합니다. 서버 세션도 다른 머신의 세션을 이름으로 부를 수 있습니다.
- **무한 핑퐁 방지:** 회신에 다시 회신하는 것은 거부됩니다.

## 받는 방식

| 세션 | 받는 방식 |
|---|---|
| Claude, Channels를 쓸 수 있는 계정 | `agent-link claude resume`이 채널 플래그를 붙여 열어 메시지가 세션에 바로 들어감 |
| Claude, 조직이 Channels를 막은 Team/Enterprise 계정 | 박스에 저장되고, agent-link hook이 몇 초 안에 세션을 깨움(대화에 프롬프트를 넣지 않음) |
| Codex | `agent-link codex resume`으로 열면 `codex queue`로 그 대화에 넣음. 안 되면 박스에 쌓이고 `$agent-link`로 확인 |
| 닫혀 있는 세션 | 박스에 저장, 다음에 열었을 때 확인 |

Channels가 막힌 Claude 세션은 hook이 깨웁니다. Claude Code가 세션 시작과 매 턴 끝에 `agent-link wait-box --hook`을 백그라운드로 띄우고, 새 메일이 오면 그 hook이 메시지 내용을 담아 끝나면서 세션이 깨어납니다. 화면에는 `agent-link: new message`가 뜨고, 세션은 메시지마다 `from <보낸 세션> (<종류>): <내용>` 한 줄을 보여 줍니다(짧은 메시지는 원문 그대로). Channels로 받는 세션은 Claude Code가 `← agent-link: from <보낸 세션> (<종류>): <내용>`을 직접 보여 주므로 세션이 다시 쓰지 않습니다. agent-link에 박스로 등록된 세션이 아니면 hook은 바로 끝납니다. 같은 메일로 두 번 깨우지 않습니다.

박스는 로컬 PC `<repo>/hub/<tool>-<name>/received/`, 서버 `<repo>/spoke/<tool>-<name>/received/`에 메시지 1건당 Markdown 파일 1개로 쌓입니다.

## 구조

```
[로컬 PC] 세션들 ── hub (agent-link hub serve) ──SSH──┬── [서버 A] daemon (agent-link daemon) ── 세션들
                                                     └── [서버 B] daemon ── 세션들
```

연결은 항상 로컬 PC에서 서버로 SSH를 겁니다. 서버에 SSH 외에 여는 포트는 없습니다. 같은 서버 안의 세션끼리는 hub를 거치지 않습니다. hub와 daemon은 systemd user 서비스로 돕니다.

## 수동 연결

`discover`를 쓸 수 없을 때(터미널이 아닌 곳 등)만 씁니다.

```bash
agent-link hub add-host gpu-server-1                  # alias 하나를 직접 연결 (--name으로 서버 이름 변경)
agent-link hub discover --hosts gpu-server-1,gpu-server-2   # 체크리스트 없이 여러 개
agent-link hub remove-host gpu-server-1               # 연결 해제 (서버에서는 agent-link revoke)
```

## 알아두기

- 열린 Codex TUI에 `codex queue`로 메시지가 들어가는지는 아직 실제 세션으로 확인하지 않았습니다.
- 받은 메시지는 다른 에이전트가 보낸 신뢰할 수 없는 입력으로 다룹니다. 세션의 권한 설정이 실제 경계입니다([SECURITY.md](SECURITY.md)).
- 운영 상세(전용 계정, 설정 키, 폐기)는 [운영 가이드](docs/operations.md), 프레임은 [프로토콜](docs/protocol.md), 결정 기록은 [ADR](docs/adr/)에 있습니다.

Apache-2.0.
