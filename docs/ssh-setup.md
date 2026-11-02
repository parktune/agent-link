# SSH 준비

agent-link의 hub는 서버마다 SSH 연결을 계속 열어 두고, 끊기면 백그라운드 서비스가 알아서 다시 붙습니다. 이때 비밀번호를 입력할 사람이 없으므로 SSH를 `BatchMode=yes`로 부릅니다. 비밀번호를 물어야 하는 서버는 기다리지 않고 바로 실패합니다.

이 문서의 준비는 로컬 PC(hub)에서 연결할 서버마다 한 번씩 하면 됩니다.

## 확인

```bash
ssh -o BatchMode=yes gpu-server-1 true && echo ok
```

`ok`가 나오면 준비가 끝난 상태입니다. 바로 [`agent-link hub discover`](../README.md#서버-연결-로컬-pc에서)로 넘어가면 됩니다. 아래는 실패할 때만 필요합니다.

## 1. ~/.ssh/config에 alias 적기

`discover`는 이 파일의 `Host` 항목을 목록으로 보여 줍니다. alias가 곧 agent-link의 서버 이름이 됩니다.

```
Host gpu-server-1
    HostName 10.0.0.11
    User alice
    IdentityFile ~/.ssh/id_ed25519

Host gpu-server-2
    HostName 10.0.0.12
    User alice
    IdentityFile ~/.ssh/id_ed25519
    ProxyJump bastion          # 점프 호스트를 거쳐야 하면
```

## 2. SSH 키 만들기 (없을 때만)

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519
```

passphrase는 비워 두거나, 정해 두고 4번처럼 ssh-agent에 올립니다.

## 3. 서버에 공개키 등록

```bash
ssh-copy-id gpu-server-1     # 이때 한 번만 서버 비밀번호를 입력
```

`ssh-copy-id`를 쓸 수 없으면 `~/.ssh/id_ed25519.pub` 내용을 서버의 `~/.ssh/authorized_keys`에 한 줄로 붙입니다. 서버에서 `~/.ssh`는 `700`, `authorized_keys`는 `600` 권한이어야 합니다.

## 4. passphrase가 있는 키라면 ssh-agent에 올리기

hub 서비스가 키를 쓰려면 키가 ssh-agent에 올라가 있고, 서비스가 그 agent를 알아야 합니다.

- **데스크톱 로그인 환경:** 대부분 키링이 agent 역할을 합니다. `systemctl --user show-environment | grep SSH_AUTH_SOCK`에 값이 나오면 서비스도 그 agent를 씁니다.
- **처음 쓸 때 자동으로 올리기:** `~/.ssh/config` 맨 위에 다음을 추가합니다.
  ```
  Host *
      AddKeysToAgent yes
  ```
- **서비스가 agent를 못 찾을 때:** 터미널에서 다음을 실행해 알려 주고 hub를 다시 띄웁니다.
  ```bash
  systemctl --user import-environment SSH_AUTH_SOCK
  agent-link up --restart
  ```

재부팅 직후처럼 키가 아직 agent에 없으면 그동안은 연결이 실패합니다. 키가 올라가면 hub가 다시 붙습니다. 이게 번거로우면 agent-link 전용으로 passphrase 없는 키를 따로 만들어 그 alias에만 `IdentityFile`로 지정합니다.

## 5. host key 등록

agent-link는 처음 보는 host key를 자동으로 받아들이지 않습니다(`StrictHostKeyChecking=yes`). 서버마다 한 번 직접 접속해 fingerprint를 확인하고 `yes`로 받아 둡니다.

```bash
ssh gpu-server-1 true
```

서버를 재설치해 host key가 바뀌었다면, 바뀐 이유를 확인한 뒤 옛 항목을 지우고 다시 접속합니다.

```bash
ssh-keygen -R 10.0.0.11
ssh gpu-server-1 true
```

## 자주 보는 오류

| `discover`·`add-host`의 메시지 | 원인과 해결 |
|---|---|
| `SSH key authentication failed (BatchMode, no passwords).` | 키 로그인이 안 됨. 3번(공개키 등록) 또는 4번(agent)을 확인 |
| `host key is not trusted ...` | known_hosts에 없거나 바뀜. 5번 |
| `DNS failed` / `TCP ... failed` | `HostName`·포트·네트워크(VPN 등) 확인 |
| `agent-link is not installed on ...` | SSH는 정상. 그 서버에서 [설치](../README.md#설치)를 먼저 실행 |

서버가 키 로그인을 허용하지 않는다면(관리 정책 등) 지금 구조로는 그 서버를 연결할 수 없습니다.
