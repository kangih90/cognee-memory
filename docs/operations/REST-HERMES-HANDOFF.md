# Cognee REST → Hermes 인계

2026-11-07, 대상 `ssh memory-host`, 설치 루트 `~/cognee`.

후속 변경: default 프로필 인증 우선순위를 수정하고 실제 새 Hermes 세션의
저장·검색을 확인했다. 현재 상태는 [HERMES-AUTH-FIX.md](HERMES-AUTH-FIX.md)를 참고한다.
아래 내용은 최초 Cognee 측 인계 시점의 기록이다.

통합 회상 후속 변경과 공유 저장소에서 확인된 검색 범위 한계는
[HERMES-UNIFIED-RECALL.md](HERMES-UNIFIED-RECALL.md)를 참고한다.

## 완료 상태

**Cognee 측 준비·수명주기 검증 완료. Hermes 자동기억 활성화는 미실행.**
Hermes 코드, 설정, default 및 다른 프로필은 수정하지 않았다. 프로필 설정과
자격증명 resolver를 로드하지 않는 격리된 로더로 설치된 `http_backend.py`를
실행해 실제 HTTP 요청·응답을 검증했다. Hermes의 실제 세션 hook 검증은 후속 작업이다.

## 실제 배포

| 항목 | 확인 결과 |
|---|---|
| Cognee 코어 | 컨테이너 importlib.metadata로 1.6.7 확인 |
| MCP | 설치 패키지 및 초기화 응답 0.5.8 |
| Hermes Cognee 플러그인 | pyproject.toml 및 plugin.yaml 1.4.1 |
| 새 REST | `cognee-rest-cognee-api-1`, 공식 `cognee.api.client:app`, Uvicorn 1 worker |
| REST 기본 URL | `http://127.0.0.1:18081` (맥미니 안에서만 접근) |
| 기존 MCP | `http://127.0.0.1:18080/mcp`, Caddy Bearer 인증 유지 |
| 관계형/벡터 | 기존 Postgres + pgvector, `deploy-postgres-1` |
| 그래프 | 기존 Neo4j Community, `deploy-neo4j-1` |
| 세션 캐시 | `CACHE_BACKEND=postgres`, 파일 DB 사용 안 함 |
| 파일·모델 | 기존 `deploy_cognee-storage` 외부 볼륨 |
| 네트워크 | 기존 `deploy_default` 외부 네트워크 |

REST는 기존 MCP와 동일한 `cognee-mcp-local` 이미지의 공식 API 앱을 별도
프로세스로 실행한다. 새 빈 DB나 별도 Hermes 로컬 Cognee 런타임은 만들지 않았다.
DB 서비스들은 다중 클라이언트를 수용하고, 파일 저장소는 원본·모델 용도다.
공유 파일 DB를 서로 다른 프로세스에서 여는 구성이 아니다.

업그레이드는 하지 않았다. 관계형 Alembic 리비전은 실행 전 DB와 패키지 모두
`dd760c09e075`였다. REST에 `ENABLE_AUTO_MIGRATIONS=false`를 명시하여
기존 저장소의 자동 스키마/데이터 마이그레이션을 막았다. 기존 이미지 태그를
새 버전으로 교체하거나 REST만 독립 업그레이드하지 말 것.

## 인증과 데이터 범위

- REST: `REQUIRE_AUTHENTICATION=true`, Cognee가 발급한 독립 `X-Api-Key`.
- MCP: 기존 `Authorization: Bearer MCP_TOKEN`. Caddy 설정은 변경하지 않았다.
- REST 요청은 Caddy를 경유하지 않으므로 두 인증 방식이 충돌하지 않는다.
- 기억·데이터셋 API의 무인증/잘못된 키 요청은 401 확인.
- 공식 `/health`, `/openapi.json`은 loopback에서 인증 없이 조회 가능하다.
- 공개 인터넷 및 Tailscale Serve에 REST 포트를 추가하지 않았다.
- `.env`는 열거나 읽지 않았다. 필요한 기존 런타임 값은 Docker의 실행 중
  컨테이너 설정에서 메모리로 가져와 보호된 새 runtime 파일에 기록했다.

전용 비관리자 사용자 `hermes-default@example.invalid`는 로그인 비밀번호 없이
API 키로만 사용한다. 기존 기본 사용자를 parent로 지정하여 새 데이터셋을
기존 소유자에게도 공유한다.

| 대상 | Hermes 계정 권한 |
|---|---|
| `hermes_default_memory` | 읽기·쓰기·삭제·공유 (Hermes 자동기억 전용) |
| 기존 `obsidian` | 읽기만; 쓰기·삭제·공유 거부 확인 |

이 권한은 공식 API의 데이터셋 ACL이다. `ENABLE_BACKEND_ACCESS_CONTROL=false`를
유지하므로 Neo4j/벡터 저장소는 물리적으로 공유된다. 데이터셋별 DB 격리나
신뢰할 수 없는 다중 사용자 서비스로 간주하면 안 된다. 이 배포의 API 키는
일반 사용자이므로 임의의 다른 사용자의 전체 기억 삭제 권한을 주지 않았다.
`forget(everything=true)` 자체는 공식 API 기능이므로 Hermes 운영에서는 사용하지 말 것.

Hermes 1.4.1의 자동/명시적 일반 기억 검색은 활성 데이터셋 한 개
(`[self._dataset]`)로 제한된다. 따라서 후속 설정 뒤 자동 검색 범위는
`hermes_default_memory`이며 Obsidian을 자동으로 합쳐 검색한다고 볼 수 없다.
전용 키로 `datasets`를 생략한 공식 REST recall은 읽을 수 있는 두 데이터셋을
모두 검색함을 검증했다. Obsidian만 지정하려면 공식 REST `dataset_ids`에
`secrets/hermes-identity.json`의 `obsidian_dataset_id`를 사용한다. 다른 사용자가
소유한 공유 데이터셋의 이름은 코어가 소유자 기준으로 해석하므로 이름만
`obsidian`으로 넘기면 안 된다. 설치된 HttpBackend는 `dataset_ids` 인자를
노출하지 않는다. Hermes 코드를 수정하거나 Obsidian을 재수집하지 않았다.

## 자격증명 인계

맥미니의 다음 파일은 600, 상위 `deploy/secrets`는 700이다.

- `deploy/secrets/hermes-api-key`: 이 통합에만 발급한 Cognee API 키
- `deploy/secrets/hermes-identity.json`: 사용자·키·데이터셋 ID (비밀 키 값 없음)
- `deploy/secrets/cognee-api.runtime`: Docker 설정에서 준비한 REST 실행 설정

키를 `cat`, `echo`, 채팅, 명령 인수, 로그에 넣지 말 것. Hermes 담당 에이전트가
같은 Mac에서 키 파일을 메모리로 읽고, **default 프로필의 공식 secret 등록
절차**를 이용해 `COGNEE_API_KEY`로 등록한다. 원격으로 전달해야 하면 SSH의
표준입력으로 수신 측 secret 등록 함수에 전달하고 값을 출력하지 않는다.
기존 MCP_TOKEN이나 LLM 키를 재사용하지 않는다.

후속 Hermes 담당자가 지정할 비밀이 아닌 설정:

```text
COGNEE_BASE_URL=http://127.0.0.1:18081
COGNEE_TRANSPORT=http
COGNEE_PLUGIN_DATASET=hermes_default_memory
COGNEE_EMBEDDED=false
COGNEE_IMPROVE_ON_END=true
```

최초 새 세션 검증에는 `COGNEE_IMPROVE_BACKGROUND=false`를 권장한다.
확인 후 background 승격을 쓸 경우 실행 결과·오류까지 확인할 운영 절차가 필요하다.
`~/.hermes/cognee.json`의 service_url/transport/dataset 값이 환경보다 우선할 수
있으므로 담당자가 공식 설정 절차로 정합성을 맞춰야 한다. 이번 작업에서는
이 파일을 읽거나 변경하지 않았다. BASE_URL을 명시한 remote/HTTP 모드를
사용하며 별도 local runtime 설치를 진행하지 않는다.

## 공식 REST 계약과 실제 테스트

공식 문서, 설치된 1.6.7 서버 코드, 실제 `/openapi.json`, 설치된 Hermes
`http_backend.py`를 대조했다. OpenAPI의 info.version은 앱 명세 버전이며
실제 코어 패키지 버전의 근거로 사용하지 않았다.

| 기능 | 실제 계약 | 결과 |
|---|---|---|
| 연결 | GET /health, agents/register·unregister | 성공 |
| 데이터셋 | GET·POST /api/v1/datasets | 성공 |
| 장기 저장 | POST /remember, multipart data + datasetName + run_in_background=true | running → DATASET_PROCESSING_COMPLETED |
| 작업 추적 | GET /datasets/status?dataset=UUID&pipeline=cognify_pipeline | 완료 확인 |
| 장기 검색 | POST /recall, JSON query/search_type/datasets/scope/top_k | 표식·내용 일치 |
| 문서 조회 | GET /datasets/{id}/data 및 /data/{id}/raw | 실제 클라이언트 성공 |
| 세션 저장 | POST /remember/entry, JSON entry(type=qa)/dataset_name/session_id | session_stored |
| 세션 검색 | POST /recall, scope=[session], search_type=null | 표식·내용 일치 |
| 세션 검토 | GET /sessions 및 /sessions/{session_id} | 성공 |
| 승격 | POST /improve, JSON dataset_name/session_ids/run_in_background=false | completed |
| 승격 검증 | graph-only recall 전/후 비교 | 승격 전 없음 → 승격 후 내용 일치 |
| 항목 삭제 | POST /forget, JSON datasetId/dataId | 테스트 문서 2개 삭제, data_remaining=false |
| 삭제 검증 | 문서 목록 및 graph recall | 테스트 기억 없음 |
| 테스트 데이터셋 정리 | POST /forget, dataset 지정, everything=false | 테스트 데이터셋 없음 |
| 세션 정리 | DELETE /sessions/{session_id} | 200 → GET 404 |
| 기존 데이터 | 전용 키로 읽기, 무권한 삭제 및 쓰기 권한 해석 | 읽기 성공, 삭제 403, 쓰기·공유도 거부 |

표에서 `/remember` 등의 접두사는 모두 `/api/v1`이다. Hermes의 remember_session은
실제로 multipart /remember가 아닌 typed /remember/entry를 사용한다. 파일
업로드를 세션 기억으로 보내면 저장을 건너뛸 수 있어 이 차이가 중요하다.
recall은 search_type=null을 명시해야 자동 라우팅/세션 소스를 사용한다.

격리 테스트 데이터셋은 `hermes_rest_validation_39d9be98f54e`이며 이미 정리했다.
`improve`는 persist_session_qa를 포함해 completed, preferences/truth-subspace는
해당 입력/설정이 없어 skipped였다. 단순 HTTP 200만 성공 기준으로 쓰지 않았다.
테스트 문서를 개별 삭제하고 데이터셋을 삭제한 순서에서는 세션 기록이 남아
공식 세션 DELETE로 별도 정리했다. 문서 삭제와 세션 삭제를 동일하게 가정하면 안 된다.

마지막 MCP 초기화·인증·Obsidian recall 통과, 무인증 401 확인.
Obsidian 동기화를 실제 재실행하여 synced=2, deleted=0, failed=0, failed_read=0 확인.

검증 증거는 맥미니의 `deploy/rest-validation-report.json`,
`deploy/rest-openapi.json`에 있다. 비밀값 및 원본 노트 내용은 기록하지 않았다.

## 변경과 복구

추가 파일: `deploy/docker-compose.rest.yml`, `scripts/prepare-rest.py`,
`scripts/bootstrap-rest-identity.py`, `scripts/verify-rest.py`,
`scripts/disable-rest.py`, 이 문서. 로컬 `.gitignore`에 secrets/backups 제외 규칙 추가.
기존 `docker-compose.yml`의 sync HTTP healthcheck 비활성화는 앞선 설치 작업의 변경이다.
이번 REST 추가에서는 기존 compose와 Caddyfile을 수정하지 않았다.

백업 동안 재시작한 기존 컨테이너: MCP, obsidian-sync, Neo4j.
Postgres·Caddy·rclone은 재시작하지 않았다. 새로 추가한 컨테이너는 API 하나다.

백업 경로: `~/cognee/backups/before-rest-20261107T141153Z`.
기존 비밀 설정 파일을 읽거나 복사하지 않고 비밀이 아닌 compose/Caddy 설정,
이미지 ID, PostgreSQL pg_dump(약 760 KiB), 정지 상태 Neo4j 및 cognee-storage
스냅샷(압축 약 224 MiB)을 보관했다. 백업 자체에도 개인 데이터가 있으므로
700 디렉토리 안에 보관한다.

운영 확인/기동 (자동으로 .env를 읽지 않도록 --env-file /dev/null을 유지):

```bash
export PATH=/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:$PATH
cd ~/cognee/deploy
/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose \
  --env-file /dev/null -p cognee-rest -f docker-compose.rest.yml ps
```

추가 REST만 되돌리려면 다음 스크립트를 **사용자가 복구를 결정했을 때** 실행한다.
새 키만 폐기하고 REST 컨테이너만 내려 기존 기억·사용자·데이터셋·볼륨을 보존한다.

```bash
/usr/bin/python3 ~/cognee/scripts/disable-rest.py
```

전체 스냅샷 복원은 이번 작업에서 하지 않았다. 이후 기억을 덮어쓸 수 있으므로
별도 승인과 정지 시간 계획을 받은 뒤 수행해야 한다. 볼륨 삭제나 down -v는 필요 없다.

## Hermes 담당자의 새 세션 검증

1. default 프로필에 위 URL/HTTP transport/전용 dataset을 등록하고 키를 공식
   secret 저장소에 조용히 등록한다. 다른 프로필은 바꾸지 않는다.
2. 새 Hermes 세션을 열어 Docker REST에 연결되고 별도 local runtime을
   설치/실행하지 않는지 확인한다.
3. 식별 가능한 합성 테스트 문장을 대화에 남긴 뒤 세션을 종료한다.
   typed 세션 저장과 session_ids를 포함한 improve 완료를 확인한다.
4. 다른 새 세션에서 테스트 문장을 재검색하여 자동 컨텍스트에 나타나는지
   확인한다. 그 뒤 해당 테스트 문서/세션만 삭제한다.
5. 기본 검색은 전용 dataset 하나임을 유지한다. Obsidian 통합 검색이 필요하면
   API가 지원하는 읽기 범위와 플러그인이 노출하는 검색 범위를 구분해 결정한다.

참고: [공식 API 문서](https://docs.cognee.ai/api-reference/introduction),
[공식 Hermes 통합](https://github.com/topoteretes/cognee-integrations/blob/main/integrations/hermes-agent/README.md).
