# OpenAI에 확인할 클라이언트 지원 질문 — 미전송 초안

목적: 독립된 두 Codex CLI 호스트가 기존 thread 컨텍스트를 유지하며 OpenAI relay를 통해
작업 요청/응답을 주고받는 오픈소스 bridge를 개발하고 있다. VPN·SSH·자체 relay 없이
서로 outbound 연결하고 수동 pairing code로 등록하려 한다.

현재 확인한 것은 CLI 0.166.0의 호스트 코드 발급과 기존 daemon의 relay 연결이다.
App Server의 공개 Remote Control 메서드는 호스트 측 동작으로 보이며, 공식 앱 역할을
외부 bridge가 수행하는 공개 인터페이스를 찾지 못했다.

1. 외부 프로그램이 manual pairing code를 claim/redeem할 수 있는 지원 API/SDK가 있는가?
2. 외부 클라이언트의 등록·권한 동의·인증 scope·연결 자격 발급 절차는 무엇인가?
3. paired host 목록, relay client attach, 메시지 프레이밍에 대한 지원 문서가 있는가?
4. 이 인터페이스가 외부 개발자에게 제공되지 않는다면 공식 지원 예정이나 신청 절차가 있는가?
5. 동일 installation의 managed daemon과 별도 app-server가 동시에 호스트 연결을 시도할 때
   HTTP 409가 발생하는 정확한 소유권/충돌 규칙은 무엇인가?
6. 연결 후 기존 active thread의 queue 입력과 완료 이벤트 구독을 사용하는 A2A 작업이
   지원 범위에 포함되는가? 권한 분리를 위해 권장되는 연동 방식은 무엇인가?

이 문서는 공개 계정 정보·호스트 식별자·토큰·실제 pairing code를 포함하지 않는다.
질문은 아직 외부에 게시하거나 전송하지 않았다.
