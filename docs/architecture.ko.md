# 아키텍처 결정

## 대안 비교

| 대안 | 장점 | 단점 | 판단 |
|---|---|---|---|
| OpenAI Remote Control relay 재사용 | 기존 NAT 접근 인프라 | 외부 A2A 클라이언트 공개 계약 미확인, 계정·인증 종속 | 채택 제외 |
| Tailscale 등 사설망 + 호스트별 Bridge + 로컬 App Server | 공인 IP·포트 포워딩 없이 연결 가능하도록 구성 가능, 개발 범위 작음 | 사설망 가입·ACL 설정 필요, 환경별 검증 필요 | 첫 두 서버 MVP 추천 |
| 자체 WSS relay + 호스트별 outbound Bridge | 양쪽 모두 outbound, agent ID 기반 라우팅, 사설망 제품 의존 감소 | relay 운영, 인증·저장·재전송·용량 제한 구현 필요 | 장기 확장 목표 |

추천은 **호스트별 Bridge + 공식 로컬 App Server adapter**다. 처음에는 사설망을 사용하고,
이후 자체 relay로 전송 계층을 교체한다. NAT 독립성을 PoC 단계의 성과로 주장하지 않는다.
초기 subprocess 경계는 현재 코드에 있으며 네트워크 Transport 인터페이스는 다음 단계에서 정의한다.

```mermaid
flowchart LR
  A[Codex A / thread A] <--> LA[로컬 App Server adapter A]
  LA <--> BA[Bridge A: 인증·권한·작업 큐]
  BA <-->|사설망 또는 WSS relay| BB[Bridge B: 인증·권한·작업 큐]
  BB <--> LB[로컬 App Server adapter B]
  LB <--> B[Codex B / thread B]
```

MCP는 Codex가 Bridge에 작업을 요청하고 결과를 조회하는 도구 면이다.
수신 push는 별도 worker가 로컬 허용 정책과 승인 상태를 확인한 뒤 해당 thread의 큐에 넣는다.
MCP만 등록해도 실행 중인 TUI에 자동으로 push되는 것은 아니다.
긴 작업의 MCP 호출은 task ID를 즉시 반환해야 한다. 결과를 기다리며 turn을 계속 점유하면
A→B→A 재귀 요청에서 교착될 수 있다.

## 작업 모델 (설계, 아직 미구현)

Envelope: protocol version, message ID, task ID, parent task ID, from/to agent ID,
타임스탬프, 만료, nonce, payload, 인증 정보. task ID와 message ID는 구분한다.
메시지 유형은 원본 요구사항의 8종을 유지한다.

상태는 requested → accepted → running → completed/failed이며 progress는 terminal 상태가 아니다.
인증된 송신자와 task 소유권을 확인하고 중복 message ID와 불법 상태 전이를 거부한다.
서명 만료+nonce+영속 deduplication으로 재전송 공격을 막고, 재연결 시 ack와 미완료 큐를 복구한다.
정확히 한 번 실행을 일반적으로 보장한다고 주장하지 않고 task handler의 멱등성을 설계한다.

## 권한과 파일 전송

호스트별 stable agent ID와 허용 peer 목록을 유지한다. relay의 주소 정보와 agent 신원은 분리한다.
TLS/mTLS 또는 검증된 메시지 서명, 짧은 등록 코드, 갱신·폐기 가능한 장기 키를 사용한다.
비밀은 저장소 밖의 권한 제한 저장소에 둔다. 현재 PoC에는 peer나 키 저장이 없다.

원격 자연어를 사용자 명령으로 그대로 실행하지 않는다. 허용된 operation과 workspace만
호스트 소유자가 지정하고, 원격 내용은 비신뢰 입력으로 전달하며 권한은 로컬 정책으로 강제한다.
프롬프트에 경고문을 붙이는 것만으로 권한 경계가 생기지는 않는다.
위험 작업은 로컬 승인으로 승격하고, 원격 peer가 approval/sandbox 설정을 고르지 못하게 한다.

파일은 별도 data plane에서 스트리밍한다. export/import root, 경로 정규화·symlink 탈출 방지,
디렉터리 manifest, chunk offset·재시도·이어받기, 크기 한도·quota, SHA-256 최종 검증 후
원자적 publish가 필요하다. 파일 본문은 LLM 컨텍스트에 넣지 않는다.
감사 기록에는 task ID·peer·권한 결정·상태·파일 해시를 남기고 토큰과 민감한 본문은 제거한다.

## 우선순위 및 완료 기준

1. **현재:** 로컬 실제 두 프로세스/thread, 양방향 hello, 컨텍스트 유지 검증.
2. 기존 daemon의 별도 테스트 TUI thread를 명시적으로 연결해 idle/busy queue와 완료 이벤트를 검증.
3. 두 호스트에서 인증된 Bridge + task 상태·재전송 방지·영속 큐 구현. 각 호스트 실제 Codex가 처리해야 MVP 통과.
4. MCP request/status/reply 도구와 비동기 응답 반영. 명시적으로 허용된 작업 디렉터리 파일 목록 예제 추가.
5. 파일 전송, 중단/재접속, 대용량·디렉터리·권한 회귀 테스트.
6. 자체 relay adapter, pair/connect UX, opt-in MCP 등록.

2~6은 계획이며 현재 구현된 기능이 아니다. 특히 파일 목록을 Python이 대신 읽어서
Codex가 조사한 것처럼 보고하거나, 단일 세션 원격 제어로 MVP를 대체하지 않는다.
