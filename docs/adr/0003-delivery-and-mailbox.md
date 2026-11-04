# ADR 0003: 열린 세션에 push, 안 되면 박스 폴더

- 상태: 채택 (2026-11-02, 사용자 결정)

## 결정

1. 받는 세션이 열려 있고 push할 수 있으면 그 세션에 바로 넣는다.
   - Claude: channel 알림(`notifications/claude/channel`). 세션을 `--dangerously-load-development-channels server:agent-link`로 시작한 경우에만 가능하다.
   - Codex: `codex queue --thread=<스레드 ID 또는 /rename 이름> --resume`.
2. push할 수 없으면 받는 세션의 **박스 폴더**에 Markdown 파일로 저장한다. 해당하는 경우는 세 가지다.
   - 세션이 닫혀 있다. 등록된 적 있는 에이전트이면 저장한다(`stored_offline`).
   - push 수단이 없다(`delivery=box`).
   - push가 실패했다(`stored`).
3. 박스 위치: 로컬 PC는 `<repo>/hub/`, 서버는 `<repo>/spoke/`. 그 아래 `<tool>-<name>/received/<YYYYMMDD-HHMMSS>_<보낸ID>_<id끝8자>.md`. 읽은 파일은 `received/read/`로 옮긴다. 두 폴더 모두 git에 올리지 않는다.
4. 세션은 처음 `/agent-link`를 쓰거나 `agent-link claude|codex resume`으로 열 때 등록한다. 이름 변경, 철회, 박스 감시 켜기·끄기도 `/agent-link`에서 말로 요청한다(MCP `register`·`unregister`·`set_watch`). 0.5~0.7의 `agent-link:register`·`agent-link:settings` 플러그인 스킬은 `agent-link --resume`과 겹쳐 2026-11-02에 없앴다. 사용자가 이름을 따로 지정하지 않으면 `/rename`으로 붙인 세션 이름을 쓴다. Claude는 세션 기록(`~/.claude/projects/*/<세션ID>.jsonl`의 `custom-title`), Codex는 `~/.codex/session_index.jsonl`에서 읽는다. 세션 이름이 없거나 에이전트 이름 형식에 맞지 않을 때만 묻는다. 철회하면 daemon이 등록 정보를 지우고 `/agent-link`가 다시 자동 등록하지 않는다(`whoami.withdrawn`). 등록은 spoke daemon(hub)에 영속 저장되고, 세션이 닫혀도 오프라인 에이전트로 남아 박스로 계속 받는다. 0.9부터 이름은 도구별로 유일하다(`claude:x`와 `codex:x`는 공존하며, 이름만 주면 `ambiguous`). 등록은 세션 ID(Claude 세션 ID, Codex thread ID)로 추적한다. MCP 서버가 몇 초마다 세션 기록에서 `/rename` 이름을 읽어(새로 붙은 부분만) 바뀌면 다시 등록하고, daemon은 같은 세션 ID의 옛 등록을 지우고 박스를 새 이름으로 옮긴다. 옛 이름은 바로 비워진다. 대기 중인 Claude hook은 박스가 옮겨지면 세션을 다시 조회해 새 박스를 기다린다. `register(name=...)`으로 직접 준 이름은 세션 이름이 다시 바뀔 때까지 유지된다. 0.10부터 닫힌 세션은 15일 미접속 또는 15일 무송수신(`last_message`, 보내거나 받은 마지막 시각. 세션을 다시 여는 것은 송수신으로 치지 않는다)이면 해제하고 기록을 남기지 않는다. `agent-link remove`는 `agent-link unregister`로 이름을 바꿨다. `agent-link claude register <이름>`은 `claude --session-id <미리 만든 UUID> --name <이름>`으로, `agent-link codex register <이름>`은 app-server의 `thread/start`·`thread/name/set`(`codex app-server proxy`의 WebSocket)으로 이름 붙은 세션을 만든 뒤 `codex resume <thread ID>`로 연다.

5. 플래그 없이 연 Claude 세션은 박스 감시로 깨운다(2026-11-02 추가, 같은 날 hook 방식으로 변경). Channels는 실행 중에 켤 수 없으므로, 세션을 깨우는 데 Claude Code hook의 `asyncRewake`를 쓴다. `setup.sh`가 `~/.claude/settings.json`에 SessionStart·Stop hook `agent-link wait-box --hook`(timeout 7일)을 다른 hook은 그대로 둔 채 추가한다. Claude Code는 이 hook을 세션 시작 때와 매 턴이 끝날 때 백그라운드로 띄운다. hook은 입력의 `session_id`로 hub/daemon에 물어(`lookup`) 박스로 받는 Claude 세션이고 감시가 켜져 있을 때만 기다리고, 아니면 바로 0으로 끝난다. 새 메일이 오면 메시지 본문(최대 5건, 건당 4000자, `[agent-link]` 수신 형식)을 stderr에 쓰고 그 메일을 `read/`로 옮긴 뒤 exit 2로 끝나며, 그 종료가 대기 중인 세션도 깨운다. 세션은 `check_box`를 부르지 않고 바로 처리하며, 메시지마다 `from <보낸 세션> (<종류>): <내용 또는 한 줄 요약>`을 사용자에게 보여 준다. 더 긴 메시지와 한도를 넘은 메시지는 읽지 않은 채로 남겨 `check_box`로 읽게 한다. 이미 알린 읽지 않은 메일은 `<tool>-<name>/watch.seen`에 남겨 같은 메일로 다시 깨우지 않는다. `watch.pid`로 같은 박스에 감시가 둘 생기지 않게 하므로, Stop hook이 매 턴 떠도 하나만 돈다. 사용자는 `/agent-link`에서 말로 끄고 켠다(`set_watch`). 끄면 `watch.off`를 남기고 돌고 있는 감시를 SIGTERM으로 끝낸다. 처음에는 `agent-link claude resume`이 첫 프롬프트로 세션에게 `wait-box`를 Bash `run_in_background`로 띄우게 했지만, 그 턴과 매번 다시 거는 지시가 토큰과 시간을 써서 hook으로 바꿨다. 받은 메시지는 사용자 입력이 아니라 hook 알림 뒤 `check_box` 도구 결과로 들어가므로 "믿을 수 없는 입력" 전제가 유지된다.

## 명세와의 차이

명세 2장은 "오프라인 에이전트로의 큐잉 전달"을 MVP 밖으로 두고 오프라인이면 즉시 오류를 요구한다.
사용자는 박스 저장을 택했다. 다만 박스는 **세션 단위**의 저장이다. 호스트 자체에 연결할 수 없을 때(hub ↔ 서버 단절)는 지금도 `offline` 오류를 돌려주고 hub가 큐에 넣지 않는다.
서버 → hub 방향만 명세 8.4대로 outbox에 버퍼링한다.

## 근거

- Claude channel은 세션이 opt-in하지 않으면 알림을 조용히 버린다. 그래서 push가 됐다고 말할 수 없는 경우가 생기고, 그때 메시지를 잃지 않을 곳이 필요하다.
- Codex는 app-server에 붙은 활성 세션에만 넣을 수 있다. 실제 TUI 주입은 아직 미검증이다.
- 사람이 폴더에서 직접 확인할 수 있어서 디버깅과 감사가 쉽다.

## 결과

- 등록 정보가 남아 있는 한 이름 해석(`list_agents`)에 오프라인 세션도 나온다. 상태 열(`status`)로 구분한다.
- push의 "읽힘" 여부는 추적하지 않는다(명세의 상태 추적은 후속 단계).

## 2026-11-04 추가

- 받은 메시지 표시: Channels 메시지는 Claude Code가 `← agent-link: <content>`로 직접 보여 주므로(모델 토큰 없음) content 앞에 `from <보낸 세션> (<종류>): `를 붙이고, 세션이 같은 내용을 다시 쓰지 않게 했다. Codex는 `codex queue`로 넣은 메시지가 화면에 보이므로 역시 다시 쓰지 않는다. hook으로 깨운 세션은 화면에 고정 문구만 보이므로(메시지별 문구 `rewakeSummary`는 공식 마켓플레이스 플러그인에만 허용) 모델이 한 줄을 쓰되 짧은 메시지는 원문 그대로 옮긴다. hook 설정에 `rewakeSummary: "agent-link: new message"`와 `rewakeMessage`를 넣어 "Stop hook feedback" 대신 이 문구가 보이게 했다(v2.1.318에서 확인).
- 백그라운드 세션: Claude Code는 `--resume`으로 연 세션을 백그라운드로 보낼 때 `--fork-session`으로 새 ID를 만들고 옛 transcript에 `continued-in`을 남긴다. `agent-link claude resume`은 백그라운드 세션(`claude agents --json --all`)을 `claude attach`로 연다. MCP 서버는 자기 ID로 등록을 못 찾으면 `continued-in`을 거슬러 올라가 옛 ID의 등록을 찾고, `register`에 `continues`로 옛 ID들을 보낸다. daemon은 그 ID의 닫힌 등록이 이름을 쥐고 있어도 넘겨준다. `<이름> (2)` 같은 사본 제목은 이름 변경으로 보지 않는다.
- 같은 세션 ID의 등록은 다른 연결이 쥐고 있어도 이름 충돌이 아니라 같은 세션이다(0.11.2). 새 연결이 넘겨받고, 쥐고 있던 연결에는 `withdrawn`을 보내 그 MCP 서버가 다시 등록하지 않게 한다. 한 세션에 MCP 서버가 둘 생기거나(옛 프로세스가 남은 경우), `name_taken`으로 실패한 MCP 서버가 재연결 때마다 등록을 다시 시도하다가 이름이 비자 등록해 버려, 도구 호출을 받는 MCP 서버는 미등록인데 daemon에는 같은 세션이 online으로 보이던 문제를 막는다.
- `agent-link claude resume <이름>`은 continuation을 따라간 최종 세션의 현재 이름으로만 찾는다(`<이름> (N)` 사본 제목은 같은 이름). 옛 이름이면 찾지 못했다고 하고 지금 이름을 알려 준다.
- 0.12: 등록의 유일성 단위를 서버·종류·이름 전체(에이전트 ID)로 바꿨다. 다른 서버는 같은 `claude:<이름>`을 쓸 수 있다. 이름(또는 `종류:이름`)만으로 보낼 때는 보내는 세션의 서버를 먼저 찾고, 없으면 hub가 다른 서버들에서 찾아 둘 이상이면 후보 전체 ID와 함께 `ambiguous`로 거부한다. `agent-link unregister`는 어느 머신에서든 hub를 통해 모든 머신을 확인하고(서버가 자기에게 하나 있다고 그것을 지우지 않는다), 전체 ID(`local:claude:<이름>` 등)를 받는다. hub에 닿지 않는 서버는 자기 등록과 hub가 마지막으로 보낸 목록으로 판단한다.
