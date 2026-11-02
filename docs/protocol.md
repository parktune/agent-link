# 프로토콜

모든 연결은 JSON Lines(한 줄에 JSON 객체 하나, UTF-8)를 쓴다. 프레임 최대 크기는 1 MiB, 메시지 본문(`content`) 기본 상한은 64 KB다.

## 메시지

```json
{"id": "uuid-v7", "from": "local:claude:main", "to": "gpu-server-1:claude:project-head",
 "type": "request", "in_reply_to": null, "content": "...", "hops": 0, "created_at": "2026-11-02T00:00:00Z"}
```

- `to` 형식: `name`, `tool:name`, `host:tool:name`. `from`은 항상 전체 ID다. 보내는 spoke가 채우며 세션이 정할 수 없다.
- `hops`: 받은 메시지에 이어서 보내면(`in_reply_to`) 그 메시지의 hops + 1이 된다. 상한(기본 3)을 넘으면 `hops_exceeded`.
- `reply` 타입 메시지에 이어서 보내면 `reply_to_reply`로 거부한다. 보내는 spoke가 검사하고, hub가 기록으로 한 번 더 검사한다.
- 같은 `id`를 다시 받으면 이전 결과만 돌려주고 다시 전달하지 않는다.

## hub ↔ host (endpoint 경유)

| 방향 | 프레임 | 설명 |
|---|---|---|
| hub → host | `hello {v, token, hub?}` | 응답 `hello_ok {v, host, caps}`. 같은 호스트에 새 연결이 오면 이전 연결을 닫는다. `hub`는 `{name, user}`로 hub가 스스로 밝히는 이름이며 표시용이다 |
| hub → host | `enroll {code, token, hub?}` | 응답 `enroll_ok {host}`. 첫 프레임으로만 허용한다. 서버는 토큰 해시 옆에 `hub` 이름을 저장한다 |
| hub → host | `deliver {msg}` | 응답 `delivered {id, status}` 또는 `error {id, code}`. status: `delivered`, `stored`, `stored_offline` |
| hub → host | `ack {seq, result?}` | outbox를 `seq`까지 처리했다는 뜻이다. `result`는 그 메시지의 라우팅 결과(`{id, status}` 또는 `{id, error}`)이며, 기다리고 있는 보낸 세션에 전달한다 |
| hub → host | `exchange {after, deliver[]}` | poll 모드. 응답 `exchange_ok {results, outbox, presence}` |
| hub → host | `ping` | 응답 `pong` (명세 추가 사항) |
| host → hub | `outbox {seq, msg}` / `outbox {seq, meta}` | 세션이 hub로 올린 메시지, 또는 같은 호스트 안에서 전달한 메시지의 메타데이터(본문 없음) |
| host → hub | `presence {agents[]}` | 변화가 있을 때와 30초마다 |

endpoint는 첫 프레임에 SSH 접속 주소(`SSH_CONNECTION`의 클라이언트 IP:포트)를 `peer`로 붙인다. hub가 보낸 `peer`는 지운다.
endpoint는 첫 프레임으로 `hello`/`enroll`만, 그다음에는 `deliver`/`ack`/`exchange`/`ping`만 통과시킨다. 그 밖의 프레임에는 `error unauthorized`를 보내고 연결을 닫는다.
토큰 검증은 daemon이 한다.

### 커서

outbox 항목은 호스트별로 단조 증가하는 `seq`를 가진다. 연결되면 daemon이 남은 항목을 모두 다시 보낸다.
hub는 호스트별로 마지막 처리 `seq`를 저장하고, 그 이하는 처리하지 않고 `ack`만 한다. 서버는 `ack`를 받으면 그 `seq`까지 지운다.
hub가 다시 시작하거나 절전에서 복귀해도 중복 전달이 없다.

## 세션 어댑터 ↔ daemon (unix socket)

요청과 응답은 `rid`로 짝을 짓는다.

| 요청 | 응답 | 설명 |
|---|---|---|
| `register {tool, name, cwd, session_id, delivery}` | `registered {id, host, box}` | delivery: `push` 또는 `box` |
| `lookup {session_id}` | `lookup {agent}` | 같은 세션의 MCP 프로세스가 다시 시작되면 이전 등록을 찾는다 |
| `send {to, content, type, in_reply_to}` | `sent {id, status, route}` | route: `local`(같은 호스트), `hub`. status에 `queued`(hub 미연결)가 추가된다 |
| `receive {limit}` | `messages {messages, box}` | 박스의 안 읽은 메시지. 읽은 파일은 `read/`로 옮긴다 |
| `list {filter}` | `agents {agents}` | 서버 daemon은 자기 호스트만, hub는 전체를 돌려준다 |
| `heartbeat`, `unregister` | `ok` | |
| (hub 전용) `hosts`, `recent {limit}`, `reload` | | `hubctl` 명령용 |
| (서버 daemon 전용) `status`, `drop_link` | | `agent-hub status`/`revoke` 명령용 |

daemon → 어댑터: `deliver {msg, rid}`. 어댑터는 세션에 넣은 뒤 `delivered {rid}`, 실패하면 `error {rid, code}`로 답한다. 실패하면 daemon이 박스에 저장한다.

## 오류 코드

`not_found`, `offline`, `ambiguous`, `too_large`, `hops_exceeded`, `unauthorized`, `reply_to_reply`, `origination_denied`, `outbox_full`, `invalid`, `timeout`, `inject_failed`, `unsupported_version`.
