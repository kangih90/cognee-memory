# 공식 REST와 Hermes

기본 [Docker 서비스](../deploy/README.md)가 `deploy` 프로젝트로 실행 중인 Mac에서
작업합니다. 소스는 `~/cognee`에 배치하며 기존 MCP는 유지합니다.

## REST 추가

```bash
cd ~/cognee
python3 scripts/prepare-rest.py
cd deploy
docker compose --env-file /dev/null -p cognee-rest -f docker-compose.rest.yml up -d --no-build
docker cp ../scripts/bootstrap-rest-identity.py cognee-rest-cognee-api-1:/tmp/bootstrap-rest-identity.py
docker exec --user 0 cognee-rest-cognee-api-1 python /tmp/bootstrap-rest-identity.py
```

CLI 플러그인을 찾지 못하면 Docker Desktop의
`/Applications/Docker.app/Contents/Resources/bin`을 PATH에 추가하거나
`/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose`를 사용합니다.

준비 스크립트는 기존 환경 파일을 열지 않고 실행 중 MCP 설정을 사용합니다.
일관된 백업을 위해 MCP·Neo4j·동기화를 잠시 정지·재개합니다. 기존 runtime이
있으면 덮어쓰지 않습니다. 다른 프로젝트·볼륨 이름의 배포에는 그대로 실행하지 마세요.

공식 `cognee.api.client:app`을 동일 이미지에서 실행하고, loopback 포트 18081에만
바인딩합니다. `REQUIRE_AUTHENTICATION=true`, 자동 마이그레이션 비활성화입니다.
기존 Caddy/MCP 인증은 변경하지 않습니다.

`deploy/secrets/`는 700, 키와 runtime은 600입니다. 전용 사용자는
`hermes_default_memory`를 소유하고 `obsidian`에는 읽기 권한만 받습니다.
이 ACL과 공유 graph의 물리적 격리 한계는 구분해야 합니다.

## Hermes 설정

별도 Cognee 로컬 runtime을 설치하지 않고 기존 REST를 사용합니다.
default 프로필의 공식 설정 절차에서 다음 값을 지정합니다.

```text
COGNEE_BASE_URL=http://127.0.0.1:18081
COGNEE_TRANSPORT=http
COGNEE_PLUGIN_DATASET=hermes_default_memory
COGNEE_EMBEDDED=false
COGNEE_IMPROVE_ON_END=true
```

`cognee.json` 값이 환경값보다 우선할 수 있으므로 서로 맞춥니다. 키는 공식 secret
등록 절차 또는 command source로 공급합니다. 키 이름은 `COGNEE_API_KEY`입니다.
그 값에 파일 경로만 넣으면 파일이 읽히지 않습니다.

`config.yaml`의 기존 설정에 병합하는 command source 예시:

```yaml
secrets:
  command:
    enabled: true
    command: "python3 ~/cognee/scripts/hermes-secret-helper.py"
    override_existing: true
```

helper의 출력은 Hermes가 캡처하는 파이프 전용입니다. 상태 확인에는
`python3 scripts/hermes-secret-helper.py --check`를 사용하세요. 키를 화면·채팅·로그에
출력하지 않습니다. 기존 helper가 다른 키도 공급한다면 예제로 대체하지 말고 병합합니다.

## 통합 회상과 출처 패치

```bash
cd ~/cognee
python3 scripts/patch-hermes-unified-recall.py --check
python3 scripts/patch-hermes-unified-recall.py --apply
python3 scripts/patch-hermes-recall-provenance.py
hermes gateway restart
```

Hermes 1.4.1에 대한 로컬 패치입니다. 예상한 소스와 다르면 적용을 거부합니다.
default의 `recall_all_readable_datasets`만 켜고 다른 프로필 설정은 유지합니다.
저장·삭제 대상도 유지합니다. 새 대화에서 검증해야 합니다.

출처는 실제 문서 ID를 공식 문서 목록과 대조해 확인합니다. 혼합·합성·미확인
문맥을 활성 저장 데이터셋의 출처로 잘못 표시하지 않습니다. 공유 저장소의
엄격한 검색 격리 한계는 [운영 기록](operations/HERMES-UNIFIED-RECALL.md)에 설명했습니다.

## 검증과 복구

기존 Obsidian 노트와 Hermes 기억에서 비밀이 아닌 검색어·확인 문구를 고릅니다.
개인 노트 내용은 코드에 넣지 않고 실행 시 입력합니다.

```bash
# 합성 데이터셋을 만들고 테스트 항목만 삭제하는 수명주기 검사 (LLM 호출 포함)
python3 scripts/verify-rest.py \
  --obsidian-query 'chosen term' --obsidian-needle 'expected note text'

# 읽기 전용 검색·출처 검사
python3 scripts/verify-unified-recall.py \
  --obsidian-query 'chosen term' --obsidian-needle 'expected note text' \
  --hermes-query 'another term' --hermes-needle 'expected Hermes text'
```

health 200만으로 성공 판정하지 않습니다. 비동기 저장 완료, 내용 일치 검색,
typed 세션 기억, improve 승격, 개별 삭제·세션 삭제를 검증합니다.
strict filter 결과가 false이면 격리 성공으로 해석하지 않습니다.

패치 원본은 `~/cognee/backups/`에 있습니다. 해당 파일을 원래 경로로 복원하고
gateway를 재시작하면 됩니다. 볼륨을 삭제할 필요는 없습니다.

REST만 종료하고 전용 키를 폐기하려면 `python3 scripts/disable-rest.py`를 실행합니다.
기존 기억·사용자·데이터셋·볼륨은 유지하며 전체 DB 복원/초기화는 수행하지 않습니다.

참고: [공식 API](https://docs.cognee.ai/api-reference/introduction),
[공식 Hermes 통합](https://github.com/topoteretes/cognee-integrations/tree/main/integrations/hermes-agent).
