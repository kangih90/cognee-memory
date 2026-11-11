# MCP OAuth 로그인

공개 MCP 주소는 `https://cognee.wegle.com/mcp`입니다. OAuth 게이트웨이가
인증 코드를 발급하고, 기존 Cognee UI 계정으로 로그인한 소유자가 연결 앱의
접근을 승인합니다. 기존 `Authorization: Bearer <MCP_TOKEN>`도 지원합니다.

## ChatGPT / Claude 설정

- 서버 URL: `https://cognee.wegle.com/mcp`
- 인증: OAuth (Claude에서는 **지금 로그인**)
- OAuth 클라이언트: **자동으로 등록 / DCR**. CIMD는 지원하지 않습니다.
- 클라이언트 ID·시크릿과 사용자 지정 Authorization 헤더는 입력하지 않습니다.
- 전송: 스트리밍 HTTP
- 로그인: Cognee UI의 `ui@example.com`과 기존 UI 비밀번호
- 로그인 화면에서 연결 앱과 돌아갈 주소를 확인하고 승인합니다.

UI 비밀번호는 서버 `~/cognee/deploy/secrets/ui-login.json`에 보관되어 있습니다.
토큰, 비밀번호, 이 파일의 내용을 채팅이나 Git에 복사하지 않습니다.

OAuth의 `memory` 권한은 기존 개인용 MCP와 같은 검색·저장·삭제 권한입니다.
데이터셋별 사용자 격리는 제공하지 않으며, 지정된 소유자 계정 하나만 로그인할
수 있습니다. 로그인은 기존 REST API에서 검증하지만 REST 로그인 쿠키나 토큰은
저장하거나 MCP에 전달하지 않습니다.

## 구성

```text
HTTPS cognee.wegle.com → Cloudflare Tunnel → Caddy :18080
  → cognee-oauth:8000 → cognee-mcp:8000
                     → cognee-api:8000 (로그인 검증에만 사용)
```

OAuth 코드는 `mcp-oauth/`에 있으며 공식 MCP SDK 1.30.0의 인증·등록·토큰·폐기
핸들러를 사용합니다. 게이트웨이는 기존 `cognee-mcp-local` 이미지를 재사용하고
별도 포트를 호스트에 공개하지 않습니다. 코드·토큰·클라이언트 등록은 별도 Docker
볼륨 `cognee-oauth_oauth-state`에 보관되어 컨테이너 재시작 후에도 유지됩니다.
접근 토큰·갱신 토큰·코드는 해시로 조회하며 원문을 저장하지 않습니다. DCR의
클라이언트 시크릿은 SDK 검증을 위해 DB에 보관되므로 볼륨과 백업도 비밀값으로
취급해야 합니다.

S256 PKCE와 등록된 redirect URI, client ID, resource, scope를 검증합니다.
로그인 요청은 10분, 인증 코드는 2분, 접근 토큰은 1시간, 갱신 권한은 최초 발급부터
30일 후 만료됩니다. 갱신할 때 토큰을 교체하며 교체된 갱신 토큰이 재사용되면
해당 연결의 남은 토큰도 폐기합니다. 승인 페이지는 Origin·Secure/HttpOnly 쿠키를
확인하고 iframe 삽입과 캐시를 차단합니다. 공개 로그인 시도에는 전역 속도 제한이
적용됩니다. 앱 이름은 등록 클라이언트가 제공한 문자열이며 신원 보증이 아닙니다.

## 배포와 검증

Mac mini에서 기존 DB·REST·MCP가 실행 중인 상태로 적용합니다.

```bash
cd ~/cognee/deploy
docker compose -p cognee-oauth -f docker-compose.oauth.yml up -d
# 먼저 게이트웨이가 healthy인지 확인합니다.
docker compose -p cognee-oauth -f docker-compose.oauth.yml ps
python3 ../scripts/enable-mcp-oauth-ingress.py
# 현재 Mac mini의 기존 Cloudflare Tunnel 프로세스에 변경을 반영합니다.
launchctl kickstart -k gui/501/com.wegle.paperclip-cloudflared
docker compose -f docker-compose.yml -f docker-compose.tailscale.yml \
  -f docker-compose.oauth-proxy.yml up -d --no-deps --force-recreate caddy
python3 ../scripts/verify-mcp-oauth.py
```

`MCP_TOKEN`은 기존 `deploy/.env`에서 Compose가 읽습니다. 기본 issuer는
`https://cognee.wegle.com`, 소유자 이메일은 `ui@example.com`이며 필요하면
`MCP_OAUTH_ISSUER`, `MCP_OAUTH_OWNER_EMAIL`을 설정할 수 있습니다. issuer를
바꾸면 기존 발급 토큰이 새 resource에 사용될 수 없으므로 다시 연결해야 합니다.
게이트웨이는 단일 worker로 실행합니다. SQLite와 속도 제한은 이 구성을 전제로 합니다.

`verify-mcp-oauth.py`는 실제 공개 HTTPS에서 DCR→로그인→PKCE→MCP 초기화·도구 목록→
갱신→폐기와 기존 토큰 연결을 확인합니다. 개인 메모리를 쓰거나 삭제하지 않습니다.
검증용 클라이언트 등록 하나가 남으며 검증 토큰은 마지막에 폐기합니다.
가짜 계정과 MCP upstream을 사용하는 보안 회귀 테스트는 다음과 같습니다.

```bash
docker compose -p cognee-oauth -f docker-compose.oauth.yml exec -w /app/oauth \
  cognee-oauth python -m unittest -v test_gateway
```

컨테이너 로그에 요청 URL·코드·토큰을 남기지 않도록 Uvicorn access log를 끕니다.
UI와 REST API 공개 범위는 기존 구성을 유지합니다.

## 되돌리기

게이트웨이를 중지하기 **전에** Caddy를 기존 토큰 설정으로 되돌립니다.

```bash
cd ~/cognee/deploy
docker compose -f docker-compose.yml -f docker-compose.tailscale.yml \
  up -d --no-deps --force-recreate caddy
docker compose -p cognee-oauth -f docker-compose.oauth.yml stop
```

이 작업은 OAuth 상태 볼륨을 삭제하지 않습니다. 공개 도메인에서 Caddy가 직접 TLS를
종료하는 구성은 OAuth 프록시 파일 대신 `Caddyfile.oauth`를 마운트합니다.
