# 검증 기록

실행일: 2026-10-30, Linux, Python 3.13.13, Codex CLI 0.166.0.

| 검증 | 결과 |
|---|---|
| editable 패키지 설치 | 성공 |
| 실제 App Server initialize / thread/start / thread/read | 성공, 모델 호출 없음 |
| 실제 독립 App Server A/B 및 서로 다른 thread | 성공 |
| B→A hello, A→B hello received 및 B 컨텍스트 반영 | 성공 |
| A/B 개별 무작위 표식 재호출, 상대 표식 미포함 | 성공 |
| A→B hello, B→A hello received 및 A 컨텍스트 반영 | 성공 |
| 결정적 프로토콜 테스트 4개 | 성공 |

단위 테스트는 turn/start 응답보다 먼저 도착하는 notification, 다른 thread/turn의 이벤트 제외,
실패한 turn, JSON-RPC 오류, subprocess 연결 종료를 다룬다. 모델을 흉내 내는 fixture와
실제 모델을 사용한 demo 결과를 구분한다.

실제 demo의 최종 출력:

```json
{
  "type": "verification",
  "sender": "bridge",
  "receiver": "user",
  "distinct_threads": true,
  "context_retained": true,
  "bidirectional": true,
  "network": "local stdio",
  "autonomous_tool_selection": false
}
```

이 결과는 두 개의 **새 테스트 대화**에 대한 로컬 검증이다.
기존 실행 중인 TUI thread 주입, 독립 서버/NAT 통신, 모델의 자율적인 tool 선택,
파일 목록 조사·파일 전송, peer 인증, reconnect는 검증하지 않았다.
PoC의 메시지 왕복을 프로그램이 중계하므로 완성된 A2A MVP로 판정하지 않는다.

원본 실행 로그는 Git에서 제외한 `.runtime/`에 보관한다. 재현:

```bash
python3 -m unittest discover -s tests -v
python3 -m codex_bridge probe
python3 -m codex_bridge demo
```

GitHub CI는 모델을 호출하지 않는 프로토콜 테스트만 실행한다.


## 클라우드 후속 검증

조회 전용 Unix WebSocket adapter를 포함한 전체 테스트 20개 통과.
기존 daemon의 relay 상태 connected, 별도 프로세스와 같은 installation 사용을 확인했다.
같은 installation의 기존 소유자가 있는 경우 cloud-probe가 enable/pairing 전에 exit 2로
생략하는 실제 동작도 확인했다. 기존 daemon의 연결·environment는 실험 전후 동일했다.
[상세 기록](relay-followup.ko.md)을 참고한다. 외부 클라이언트의 pairing 수락은 미검증이다.


## Security mode (SSH), 2026-10-30

- 실제 OpenSSH loopback, 임시 host/client key, strict known_hosts 검증: 양방향 task/result 통과.
- 사용자 SSH config/authorized_keys, 시스템 sshd 서비스, 방화벽 변경 없음. 새 수신 포트는 loopback에만 임시 생성 후 종료.
- 실제 MCP SDK stdio initialize / tools/list / tools/call 검증 통과.
- 별도 CODEX_HOME의 공식 `codex mcp add/get` 등록 검증 통과. 사용자 Codex 설정은 변경하지 않음.
- 실제 Codex 두 프로세스 + 서로 다른 thread + MCP 도구 + 실제 SSH loopback: 합성 hello와
  reverse hello 양방향 완료, 각자의 무작위 대화 표식 유지·상대 표식 미포함 검증 통과.
- 모델 시험에서는 임시 app-server의 테스트 도구 6개만 명시적으로 사전 허용했다.
  제품의 mcp-install 및 사용자 전역 승인 설정은 완화하지 않는다.
- Tailscale 실행 파일 설치·로그인·실행 없음. security mode는 OpenAI relay 진단도 호출하지 않음.
- Windows/Ubuntu CI는 별도 matrix로 구성한다. 실제 Windows sshd와 별도 회사 서버 두 대는 미검증.

실제 모델 시험의 최종 출력:

```json
{
  "transport": "real OpenSSH loopback",
  "actual_codex_processes": 2,
  "distinct_threads": true,
  "mcp_tools_called": true,
  "bidirectional_tasks_completed": true,
  "context_retained": true,
  "system_ssh_modified": false,
  "models_called": true
}
```

재현: `python scripts/verify_security_ssh.py --models` (로그인된 Codex, Linux sshd, 기존 /run/sshd 필요).
regular CI는 모델을 호출하지 않는다. 자동 테스트 42개를 구성했으며 로컬에서는 Windows 전용 ACL 검사 1개를 제외한 41개가 통과했다. 입력 위조·타 peer ack·
prune 후 재전송·실패 배치 rollback·기한 만료·정상 키 검증·Windows 명령 quoting·UTF-8 교환을 포함한다.


Windows state ACL은 기존 소유권을 변경하지 않고 DACL만 현재 사용자/SYSTEM으로 교체한다.
Windows 전용 regression은 사전에 명시적 Everyone ACE가 있던 디렉터리·DB에서도 해당 권한이
제거되는지 검사한다. SSH 동기화 도중 로컬에서 peer를 폐기하면 응답을 수신함에 반영하지 않는
경계도 테스트한다. CI의 Windows job에서는 Unix daemon socket 전용 검사 5개를 제외한다.


### 최종 지원 범위 수정

사용자 후속 지시에 따라 Windows 지원·CI 검토는 보류한다. 최종 대상은 Ubuntu ↔ Ubuntu이며
Ubuntu Python 3.11/3.13 CI를 기준으로 한다. 이전 Windows CI 실패·호환성 수정 기록이
Windows 지원 완료를 뜻하지 않는다. CLI는 POSIX remote platform만 제공한다.
