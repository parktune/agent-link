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
4. 세션은 `agent-link:register`로 등록한다. 등록하지 않고 `agent-link:send`를 쓰면 그때 자동으로 등록한다. 사용자가 이름을 따로 지정하지 않으면 `/rename`으로 붙인 세션 이름을 쓴다. Claude는 세션 기록(`~/.claude/projects/*/<세션ID>.jsonl`의 `custom-title`), Codex는 `~/.codex/session_index.jsonl`에서 읽는다. 세션 이름이 없거나 에이전트 이름 형식에 맞지 않을 때만 묻는다. 등록은 spoke daemon(hub)에 영속 저장되고, 세션이 닫혀도 오프라인 에이전트로 남아 박스로 계속 받는다.

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
