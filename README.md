# cognee + Obsidian + MCP

옵시디언 볼트를 [cognee](https://github.com/topoteretes/cognee) 메모리로 동기화하고,
AI 클라이언트가 MCP로 검색·저장하게 하는 구성입니다.

```
Obsidian 볼트 ──(옵시디언 커넥터: 태그·폴더 구조 유지)──▶ cognee ◀──MCP── Claude Code 등
```

두 가지 방식이 있습니다.

| | 로컬 (Mac) | 서버 (Docker) |
|---|---|---|
| 저장소 | 파일 DB (`.cognee_data`, `.cognee_system`) | Postgres(pgvector) + Neo4j |
| MCP | stdio (Claude Code가 직접 실행) | HTTPS + Bearer 토큰 |
| 볼트 | PC의 볼트 폴더 | 구글 드라이브 → rclone → 서버 사본 |
| 동시 실행 | MCP가 떠 있으면 동기화 실패 (파일 DB 잠금) | 문제없음 |

## 파일

| 경로 | 내용 |
|---|---|
| `sync_obsidian.py` | 볼트 동기화 (`python sync_obsidian.py`), 검색 (`python sync_obsidian.py "질문"`) |
| `llm_check.py` | LLM 설정 확인 (`LLM RESPONSE: '5'`가 나오면 정상) |
| `install_launchd.py` | macOS에서 주기 동기화 등록 (`--dry-run`, `--uninstall`) |
| `scripts/fetch-cognee-mcp.sh` | cognee-mcp 소스를 고정 버전으로 `./cognee-mcp`에 받기 |
| `deploy/` | Docker 배포 ([deploy/README.md](deploy/README.md)) |

## 로컬 (Mac) 설정

```bash
python3 -m venv ~/.venv && ~/.venv/bin/pip install cognee==1.6.7
cp .env.example .env            # LLM_API_KEY, 경로 입력
~/.venv/bin/python llm_check.py
~/.venv/bin/python sync_obsidian.py
~/.venv/bin/python install_launchd.py   # 30분 주기 동기화 (선택)
```

`.env`는 스크립트 옆의 파일을 읽으므로 어느 폴더에서 실행해도 됩니다.

### Claude Code에 MCP 연결 (stdio)

```bash
scripts/fetch-cognee-mcp.sh
(cd cognee-mcp && uv sync)
claude mcp add cognee -e COGNEE_ENV_FILE=$PWD/.env -- $PWD/cognee-mcp/.venv/bin/cognee-mcp --transport stdio
```

`COGNEE_ENV_FILE`이 필요합니다. `cognee-mcp/pyproject.toml` 때문에 cognee가 상위 폴더의 `.env`를 찾지 않습니다.

## 서버 (Docker) 설정

[deploy/README.md](deploy/README.md)를 보세요.

## 주의

- **비밀 정보는 커밋하지 않습니다.** `.env`, `deploy/.env`, `deploy/cognee.env`, `deploy/rclone/`은 `.gitignore`에 들어 있습니다.
- **cognee 버전을 맞춥니다.** 로컬 venv와 cognee-mcp의 cognee 버전이 다르면 같은 저장소를 공유할 수 없습니다.
- **볼트 노트는 LLM 제공자로 전송됩니다.** 그래프를 만들 때와 검색 답변을 만들 때 쓰입니다. 임베딩은 로컬에서 처리합니다.
- **옵시디언 커넥터가 읽지 않는 것:** `[[위키링크]]`(텍스트로만 저장), 본문 `#태그`, 첨부 파일.
- **rclone 공용 client_id는 2026년 중 종료됩니다.** 계속 쓰려면 Google Cloud에서 OAuth 클라이언트를 직접 만들어야 합니다.
