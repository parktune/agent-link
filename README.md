# Codex Bridge

독립적인 Codex 대화 세션 사이의 작업 요청·응답을 위한 오픈소스 프로젝트입니다.
현재 범위는 **Phase 1 기술 조사와 로컬 대화 유지 PoC**입니다. 인터넷 A2A 프레임워크 완성본은 아닙니다.

## 시작

Python 3.11+와 로그인된 Codex CLI가 필요합니다. 검증 기준 버전은 `codex-cli 0.166.0`입니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/codex-bridge probe
.venv/bin/codex-bridge demo
```

설치 없이 저장소 루트에서 `python3 -m codex_bridge probe` 또는 `demo`도 가능합니다.
`probe`는 별도 stdio App Server에서 thread를 생성·조회하며 모델을 호출하지 않습니다.
`demo`는 실제 모델을 호출하므로 계정 사용량이 발생합니다. 두 App Server와 별도 임시 작업 디렉터리에서 A/B thread를 만들고, 각자의 무작위 기억 표식을 저장합니다. B→A hello/응답을 B에 반영한 뒤 각 thread의 기억을 확인하고, A→B 역방향 왕복도 수행합니다. 임시 작업 디렉터리는 삭제되지만 Codex에 저장된 테스트 대화 기록은 남을 수 있습니다.

출력은 task ID, 송수신자, thread ID를 포함하는 JSON Lines입니다. 마지막 `verification` 이벤트가 있어야 성공입니다. 실제 Codex 사용이 없는 테스트는 다음과 같습니다.

```bash
python3 -m unittest discover -s tests -v
```

## 현재 구현과 한계

- App Server 초기화, thread 생성, turn 실행, 완료 이벤트 수신, 오류·연결 종료·시간 제한 처리
- 두 독립 프로세스의 서로 다른 thread에서 대화 유지와 양방향 왕복 확인
- 모델 응답과 프로토콜 오류를 구분하며, 서버 승인 요청은 자동 승인하지 않음
- 표준 라이브러리 기반 런타임, GitHub Actions 테스트

현재 메시지는 Python 프로그램이 중계합니다. Codex가 MCP 도구를 골라 자율적으로 요청하는 기능, 기존 사용 중인 TUI에 접속하는 기능, NAT 통과, 파일 전송, 영속 큐, 상호 인증은 아직 구현하지 않았습니다. 로컬 데모 성공을 두 서버 간 MVP 성공으로 간주하지 않습니다.

`AppServer(argv=[...])`는 subprocess 전송 경계를 제공합니다. 기존 daemon 연결을 위한 `codex app-server proxy --sock PATH`를 넣을 수 있지만, 실제 사용 중인 thread의 동시 실행·승인·이벤트 처리는 별도 검증 대상입니다. 임의 peer에게 App Server 연결이나 thread ID 선택 권한을 직접 제공하지 마세요. PoC의 read-only sandbox는 신뢰할 수 없는 peer를 위한 완성된 격리 수단이 아닙니다.

## 문서

- [기술 타당성·근거·판정](docs/feasibility.ko.md)
- [아키텍처 비교 및 다음 단계](docs/architecture.ko.md)
- [검증 결과](docs/validation.ko.md)
- [원본 요구사항](docs/requirements.ko.md)
- [개발 및 GitHub push](CONTRIBUTING.md)
- [보안 경계](SECURITY.md)

## 라이선스

Apache-2.0. 원본 요구사항은 프로젝트 기획 문서이며, 외부 Codex 소스는 이 저장소에 포함하지 않습니다.
