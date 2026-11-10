# Tailscale 전용 Cognee UI

Mac mini의 기존 Cognee 1.6.7 REST API와 DB에 공식 웹 UI를 추가했습니다.
UI 소스는 MCP와 같은 공식 커밋 `dcb8d7c6bf80a097005fb3aded6cdc3599225de8`에 고정합니다.

## 접속과 계정

Tailscale에 연결된 기기에서 접속합니다.

**<https://home-mini.geep-pompano.ts.net:8443/local-login>**

이메일은 `ui@example.com`입니다. 실제 메일 발송용 주소가 아닌 로컬 로그인 ID입니다.
임의 생성 비밀번호는 서버의 `~/cognee/deploy/secrets/ui-login.json`에만 저장합니다.
본인 터미널에서 다음 명령으로 확인하고 비밀번호 관리자에 보관할 수 있습니다.

```bash
ssh mac.mini 'cat ~/cognee/deploy/secrets/ui-login.json'
```

이 파일을 Git, 채팅, 실행 보고에 복사하지 않습니다. UI 계정은 일반 사용자이며
최초 설치 시 기존 데이터셋에 read/write/delete 권한을 부여합니다.
이후 다른 계정이 만든 데이터셋은 별도의 권한 부여가 필요할 수 있습니다.
공유 graph의 물리적 격리 한계는 기존 [REST 운영 안내](rest-hermes.md)와 같습니다.

## 연결 구성

```text
Tailscale 기기 → HTTPS :8443 → Tailscale Serve
  → 127.0.0.1:18082 → UI 전용 Caddy
      /api/v1/* → 기존 cognee-api:8000 (인증 필수)
      나머지     → 공식 Next.js UI:3000
```

브라우저는 UI와 API를 같은 origin으로 사용합니다. Caddy는 브라우저 인증 쿠키에
Secure 속성을 추가하며 HttpOnly·SameSite=Lax는 API 설정을 유지합니다.
Next의 서버 프록시는 Docker 내부 API 주소를 사용합니다.

기존 MCP HTTPS :443, REST loopback :18081 및 DB 설정을 유지합니다.
새 호스트 포트는 loopback에만 바인딩합니다. Tailscale Funnel이나 공유기
포트 포워딩을 사용하지 않으며, 같은 Wi-Fi라도 Tailscale 연결은 필요합니다.

## 최초 설치

기존 `deploy` 및 `cognee-rest` 프로젝트가 실행 중인 `~/cognee`에서 실행합니다.
Docker Desktop CLI 경로가 PATH에 있어야 합니다.

```bash
cd ~/cognee
python3 scripts/fetch-cognee-ui.py
cd deploy
docker compose --env-file /dev/null -p cognee-ui -f docker-compose.ui.yml up -d --build
docker cp ../scripts/bootstrap-ui-identity.py cognee-rest-cognee-api-1:/tmp/bootstrap-ui-identity.py
docker exec --user 0 cognee-rest-cognee-api-1 python /tmp/bootstrap-ui-identity.py
tailscale serve --bg --https=8443 http://127.0.0.1:18082
```

별도 호스트에 설치하려면 Compose의 두 HTTPS 주소를 해당 호스트로 바꿉니다.
브라우저 API 주소가 빌드에 포함되므로 주소 변경 후 이미지를 다시 빌드해야 합니다.
원본 UI의 localhost 참조, 로그인 기본값, 서버 프록시 주소와 로그아웃 쿠키 이름을
준비 스크립트에서 보정합니다. 기본 데모 비밀번호를 활성화하지 않습니다.
계정 생성 스크립트는 기존 계정·자격증명을 발견하면 덮어쓰기를 거부합니다.

## 운영·검증·중지

```bash
cd ~/cognee/deploy
docker compose --env-file /dev/null -p cognee-ui -f docker-compose.ui.yml ps
docker compose --env-file /dev/null -p cognee-ui -f docker-compose.ui.yml logs --tail=50

python3 ../scripts/verify-ui.py
python3 ../scripts/verify-ui.py --regression-only
```

검증은 실제 Tailscale HTTPS로 로그인·데이터셋 조회·텍스트 파일 업로드·처리·검색·
그래프 프록시·UI 메뉴 로그아웃을 확인합니다. 합성 테스트 데이터셋을 만들고 삭제하며
LLM 호출이 발생합니다. 보고서는 Git에서 제외되는 `deploy/ui-validation-report.json`입니다.
실패 시 합성 데이터셋 잔존 여부를 확인합니다. 회귀 검증은 기존 Hermes 키와 MCP를
읽기 전용으로 확인합니다. 브라우저 화면·클릭 동작의 수동 확인은 별도입니다.

UI만 중지하려면:

```bash
tailscale serve --https=8443 off
docker compose --env-file /dev/null -p cognee-ui -f docker-compose.ui.yml down
```

기존 REST·MCP·동기화·DB·볼륨은 유지됩니다. 재시작은 `up -d`와 위 Serve 명령을
사용합니다. UI 중지는 전용 계정을 폐기하지 않으며 자격증명도 유지됩니다.
