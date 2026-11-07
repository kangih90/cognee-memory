# cognee MCP 서버 배포 (HTTPS + 옵시디언 볼트)

| 컨테이너 | 역할 |
|---|---|
| `caddy` | HTTPS 인증서 자동 발급, `Authorization: Bearer <MCP_TOKEN>` 검사 |
| `cognee-mcp` | MCP 서버 (Streamable HTTP, `/mcp`) |
| `obsidian-sync` | `/vault`를 옵시디언 커넥터로 주기 동기화 (태그·폴더 구조 유지) |
| `rclone` | 구글 드라이브의 볼트 백업을 `/vault`로 주기 다운로드 |
| `postgres` | 관계형 DB + 벡터(pgvector) + 세션 캐시 |
| `neo4j` | 그래프 DB |

DB가 모두 서버형이라 `cognee-mcp`와 `obsidian-sync`가 동시에 돌아도 잠금 충돌이 없습니다.

Neo4j Community는 DB를 하나만 지원하므로 `ENABLE_BACKEND_ACCESS_CONTROL=false`로 둡니다.
모든 데이터셋이 그래프 하나를 공유하며, 사용자·데이터셋별 저장소 격리는 없습니다(개인용 전제).

빌드에는 BuildKit이 필요합니다. Docker Desktop에는 포함되어 있고, Homebrew로 설치한
Docker CLI라면 `brew install docker-buildx`가 필요합니다. 이미지는 약 4.4GB, 빌드 중에는
빌드 캐시까지 약 15GB가 필요하며, 빌드 후 `docker builder prune -af`로 캐시를 지울 수 있습니다.

## 1. 서버에 파일 배치

```bash
git clone <this repo> cognee && cd cognee
scripts/fetch-cognee-mcp.sh      # ./cognee-mcp (고정 커밋)
```

```
cognee/
├── .dockerignore
├── cognee-mcp/          # cognee-mcp 소스 (Dockerfile 포함)
├── sync_obsidian.py
└── deploy/              # 이 폴더
```

HTTPS는 두 가지 중 하나를 고릅니다.

- **공개 도메인:** 방화벽은 80, 443만 엽니다. `MCP_DOMAIN`의 DNS A 레코드를 서버 IP로 지정합니다.
- **Tailscale (도메인 없음, tailnet 기기만 접속):** 관리 화면 DNS 설정에서 HTTPS Certificates를 켜고,
  서버에서 한 번 `sudo tailscale set --operator=$USER`를 실행합니다. 아래 4단계를 Tailscale 방식으로 실행합니다.

## 2. 설정 파일

```bash
cd cognee/deploy
cp .env.example .env               # 도메인, 토큰, DB 비밀번호
cp cognee.env.example cognee.env   # LLM_API_KEY 등
chmod 600 .env cognee.env
```

## 3. 구글 드라이브 연결 (rclone, 최초 1회)

브라우저가 있는 PC에서 토큰을 받아 서버로 옮깁니다.

```bash
# 브라우저 있는 PC에서
rclone config        # n → 이름 gdrive → drive → scope 2 (drive.readonly) → 나머지 기본값
# 생성된 ~/.config/rclone/rclone.conf 를 서버의 cognee/deploy/rclone/rclone.conf 로 복사
```

"내 컴퓨터 백업" 폴더는 "내 드라이브" 밖에 있어서 경로로 지정할 수 없습니다.
웹에서 백업된 볼트 폴더를 열고, 주소 `.../folders/<폴더ID>`의 ID를 `rclone.conf`의 `[gdrive]`에 넣습니다.

```
[gdrive]
type = drive
scope = drive.readonly
root_folder_id = <볼트 폴더 ID>
token = {...}
```

확인: `docker run --rm -v $PWD/rclone:/config/rclone rclone/rclone:1.69 lsf gdrive:` 에 노트 목록이 나오면 됩니다.

## 4. 실행

```bash
docker compose up -d --build
docker compose logs -f obsidian-sync    # "SYNC STATS: {... 'failed': 0 ...}" 확인
```

Tailscale 방식:

```bash
docker compose -f docker-compose.yml -f docker-compose.tailscale.yml up -d --build
tailscale serve --bg --https=443 http://127.0.0.1:18080
# 엔드포인트: https://<기기이름>.<tailnet>.ts.net/mcp
```

로컬(Mac) 시험용: `docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build postgres neo4j cognee-mcp obsidian-sync rclone`
(caddy 없음, MCP는 `http://localhost:8000/mcp`, 볼트는 `../obs-tt`).

## 5. 클라이언트 연결

```bash
claude mcp add --transport http cognee https://<MCP_DOMAIN>/mcp \
  --header "Authorization: Bearer <MCP_TOKEN>"
```

토큰 없이 접속하면 401이 나와야 정상입니다: `curl -i https://<MCP_DOMAIN>/mcp`

## 운영

- 즉시 동기화: `docker compose restart rclone obsidian-sync`
- 볼트는 서버에서 읽기 전용 사본입니다. 노트 수정은 PC 옵시디언에서 합니다.
  반영 순서: PC → 드라이브 백업 → rclone(10분) → obsidian-sync(30분).
- 백업 대상: `postgres-data`, `neo4j-data`, `cognee-storage` 볼륨.
- cognee 업그레이드: `cognee-mcp/` 소스를 새 버전으로 바꾸고 `docker compose up -d --build`.
