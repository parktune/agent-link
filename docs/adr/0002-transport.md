# ADR 0002: 전송은 시스템 OpenSSH 하위 프로세스 + JSON Lines 유지 스트림

- 상태: 채택 (2026-11-02)

## 결정

- hub는 호스트마다 시스템 `ssh`를 하위 프로세스로 실행하고 `<절대경로>/agent-hub endpoint`와 stdin/stdout으로 JSON Lines를 주고받는다.
- 한 호스트에 SSH 세션 하나를 오래 유지한다. 요청마다 새로 연결하지 않는다. SSH 수준에서는 `ServerAliveInterval=15`, 앱 수준에서는 15초 `ping`으로 연결을 감시하고, 45초 동안 아무 프레임도 없으면 다시 연결한다.
- 다시 연결할 때는 1초에서 시작해 최대 60초까지 지수 백오프에 ±20% 지터를 더한다. 호스트마다 독립된 asyncio 태스크로 돈다.
- `ControlMaster`는 끈다(`ControlMaster=no`, `ControlPath=none`). 유지 세션 자체가 연결 재사용을 대신하고, 다른 SSH 세션과 섞이지 않게 하기 위해서다.
- 전송은 `Transport` 인터페이스(`agent_link/hub.py`)로 추상화했다. 테스트는 같은 인터페이스로 endpoint를 SSH 없이 직접 실행한다.
- 스트림을 유지할 수 없는 환경을 위해 `mode=poll`(짧은 `exchange` 세션, 활동 중 1초·유휴 시 최대 10초)을 둔다.

## 근거

시스템 OpenSSH는 known_hosts, ProxyJump, 키, 회사 SSH 정책을 그대로 따른다. Python SSH 라이브러리를 쓰면 이것들을 다시 구현해야 하고 정책과 어긋날 위험이 있다.
강제 명령 `restrict,command=...`와도 바로 맞물린다.

## 강제하는 SSH 옵션

`BatchMode=yes`, `StrictHostKeyChecking=yes`, `ForwardAgent=no`, `ClearAllForwardings=yes`, `PermitLocalCommand=no`,
`RemoteCommand=none`, `ConnectTimeout=5`. 알 수 없는 host key와 인증 실패는 원인을 보고하고 중단한다.
