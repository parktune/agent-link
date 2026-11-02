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
4. 세션은 `agent-link:register`로 등록하고 `agent-link:settings`로 철회한다(2026-11-02). 플래그 없이 연 Claude 세션이면 `whoami.restart`의 명령을 안내한다. 세션 폴더로 이동해 원래 실행 인자에서 `-c`·`-r`·`-n`만 빼고 채널 플래그(`server:agent-link`)를 더해 `--resume <세션 이름>`으로 다시 연다. 이름이 없거나 겹치면 세션 ID로 연다(`restart.by_id`). 철회하면 daemon이 등록 정보를 지우고, 같은 세션에서는 `/agent-link`가 다시 자동 등록하지 않는다(`whoami.withdrawn`). 등록하지 않고 `/agent-link`를 쓰면 그때 자동으로 등록한다. 사용자가 이름을 따로 지정하지 않으면 `/rename`으로 붙인 세션 이름을 쓴다. Claude는 세션 기록(`~/.claude/projects/*/<세션ID>.jsonl`의 `custom-title`), Codex는 `~/.codex/session_index.jsonl`에서 읽는다. 세션 이름이 없거나 에이전트 이름 형식에 맞지 않을 때만 묻는다. 등록은 spoke daemon(hub)에 영속 저장되고, 세션이 닫혀도 오프라인 에이전트로 남아 박스로 계속 받는다.

5. 플래그 없이 연 Claude 세션은 박스 감시로 깨운다(2026-11-02 추가). Channels는 실행 중에 켤 수 없고, 세션을 깨울 수 있는 것은 그 세션의 Claude가 띄운 백그라운드 작업뿐이다. 그래서 `whoami`·`register`·`check_box` 결과에 `watch {enabled, running_pid, command}`를 싣고(`command` = `agent-hub wait-box --box <received/>`), 스킬이 켜져 있고 돌고 있지 않으면 그것을 Bash `run_in_background`로 띄운다. 사용자는 `agent-link:settings watch off|on`으로 끄고 켠다. 끄면 `<tool>-<name>/watch.off`를 남겨 그 이름에는 다시 걸지 않고, 돌고 있는 감시는 SIGTERM으로 끝낸다("stopped" 한 줄). 명령은 읽지 않은 `*.md`가 생기면 한 줄을 찍고 끝나며, 그 종료가 세션을 깨운다. 세션은 박스를 읽은 뒤 다시 건다. `<tool>-<name>/watch.pid`로 같은 박스에 감시가 둘 생기지 않게 한다. 받은 메시지는 tmux 입력과 달리 사용자 입력이 아니라 도구 결과로 들어가므로 "믿을 수 없는 입력" 전제가 유지된다.

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
