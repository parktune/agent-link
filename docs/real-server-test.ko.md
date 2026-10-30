# 실서버 시험 절차: 서로 다른 서버의 Codex 세션 대화

목표는 서로 다른 Ubuntu 서버 A, B에서 열린 Codex 대화가 세션 이름으로 메시지를 주고받고,
받은 쪽 대화가 사람의 입력 없이 깨어나 답하는 것을 확인하는 것이다.
모델 호출이 발생하므로 사용자가 직접 실행한다. 각 단계의 기대 결과와 다르면 그 단계에서 멈추고 기록한다.

준비 조건은 [운영 가이드](security-mode.ko.md)의 SSH 경로 조건과 같다. B → A SSH가 공개키 인증으로 되고,
A의 host key가 B의 `known_hosts`에 있어야 한다. 양쪽 agent-link는 같은 버전(0.3.0 이상)이어야 한다.

아래에서 `agent-link`는 각 서버의 `<repo>/.venv/bin/agent-link`이다.

기존 설치를 업데이트했다면 양쪽에서 스킬 사본도 갱신하고 Codex를 다시 연다. MCP 등록은 그대로 둔다.

```bash
python3 scripts/install_agent_link_skill.py --replace
```

## 0단계: 한 서버에서 `codex queue` 깨우기 확인 (가장 먼저)

agent-link와 무관하게, 열린 Codex 대화에 외부에서 turn을 넣을 수 있는지 먼저 확인한다.
이것이 안 되면 이후 단계는 의미가 없다.

1. 터미널 1에서 `codex`를 열고 `/rename queue-probe`를 실행한다. 아무 질문이나 한 번 해서 세션을 기록에 남긴다.
2. 터미널 2에서 실행한다.

   ```bash
   codex queue --thread=queue-probe --message='queue 시험입니다. "받았음"이라고만 답하세요.' --resume
   echo "exit=$?"
   ```

| 결과 | 판정 |
|---|---|
| exit=0, 터미널 1 대화에 메시지가 들어가 답함 | 통과. 1단계로 |
| exit=0, 대화에 아무 변화 없음 | `--resume`이 붙은 daemon과 TUI가 다른 경우다. TUI를 닫고 다시 연 뒤 재시도하고, `codex app-server daemon` 상태를 기록한다 |
| `No active session found` | 세션 이름 불일치 또는 TUI가 daemon에 붙어 있지 않음. `~/.codex/session_index.jsonl`의 마지막 `thread_name`을 확인한다 |

작업 중(답변 생성 중)에 한 번 더 넣어 보고, 끝난 뒤 차례대로 처리되는지도 기록한다.

## 1단계: pairing

[README](../README.md)의 `$agent-link` 흐름대로 A에서 코드를 발행하고 B에서 연결한다.

- A, B 양쪽 `agent-link security peers`에 상대가 1개씩 보이면 통과.

## 2단계: 세션 이름 등록

1. A에서 Codex 대화를 열고 `/rename planner`, B에서 `/rename reviewer`.
2. 각 대화에서 `$agent-link 이 대화를 메시지 수신 세션으로 등록해줘`라고 요청한다.
   스킬이 `security session add <현재 이름>`을 실행한다. 직접 해도 된다.

   ```bash
   agent-link security session add planner    # A
   agent-link security session add reviewer   # B
   ```

3. MCP 도구가 안 보이면 Codex를 재시작하거나 도구 목록을 새로 고친다.

## 3단계: watch 실행

양쪽 서버에서 별도 터미널(또는 tmux)로 실행해 둔다. Codex와 같은 OS 사용자로 실행한다.

```bash
agent-link security watch
```

5초마다 JSON 한 줄이 나온다. B의 `sync`가 `synced`이고, 몇 초 뒤 B의 `bridge_peers`에서
A의 `sessions`에 `planner`가, A의 `bridge_peers`에서 B의 `sessions`에 `reviewer`가 보이면 통과.

## 4단계: B → A 대화, A가 자동으로 깨어남

B의 `reviewer` 대화에서:

> agent-link로 peer agent-a의 planner 세션에 "안녕, 지금 몇 시인지 알려줘"라고 보내. from_session은 reviewer야.

기대 결과:

1. B 대화가 `bridge_send_message`를 호출하고 `conversation_id`를 받는다.
2. A의 watch 출력에 `"status": "woken"`과 `"session": "planner"`가 찍힌다.
3. **사람이 아무것도 입력하지 않았는데** A의 `planner` 대화에 `[agent-link] agent_message from peer ...` 메시지가 들어오고 답을 생성한다.
4. A 대화가 `bridge_send_message(peer=..., to_session='reviewer', conversation_id=...)`로 답한다.
5. B의 다음 watch 주기에 `reviewer` 대화가 깨어나 A의 답을 받는다.

## 5단계: A → B 방향, 작업 결과 라우팅

A의 `planner` 대화에서:

> agent-link로 peer agent-b의 reviewer 세션에 작업을 보내: "이 서버의 hostname을 알려줘". from_session은 planner야.

- B의 `reviewer`가 깨어나 작업을 처리하고 `bridge_reply`로 결과를 보낸다.
- A의 `planner`가 다시 깨어나 결과(`task_completed`)를 받는다.
- 양쪽 `agent-link security task <task_id>`의 상태가 `completed`이면 통과.

## 6단계: 예외 동작

| 시험 | 방법 | 기대 결과 |
|---|---|---|
| 닫힌 세션 | A의 planner TUI를 닫고 B에서 메시지 전송 | A의 `security session status`에 `pending`·`wake_attempts` 증가·`No active session` 오류. TUI를 다시 열면(`codex resume planner`) 다음 재시도에서 전달 |
| 미등록 세션 | B에서 A의 `ghost` 세션으로 전송 | 전송 결과에 `warning`, A에서 `unrouted`. A에서 `session add ghost` 후 해당 이름 대화가 열려 있으면 전달 |
| 여러 세션 | A에 `/rename tester` 대화를 하나 더 열고 등록 | B가 `tester`와 `planner`를 이름으로 구분해 보낼 수 있음 |
| 끝없는 대화 멈추기 | 서로 계속 답하게 유도 | watch를 Ctrl+C 하거나 `session remove`로 멈춤 |
| revoke | 한쪽에서 `security revoke <peer>` | 이후 교환이 `failed`이고 메시지가 전달되지 않음 |

## 기록할 것

`docs/validation.ko.md`에 날짜, Codex CLI 버전(`codex --version`), agent-link 버전, 두 서버의 OS 버전과
단계별 통과 여부를 남긴다. 모델 대화 원문과 pairing code, 토큰은 기록하지 않는다.
