# 문서 목차

- [Docker·Google Drive·MCP 배포](../deploy/README.md)
- [공식 REST와 Hermes 설정](rest-hermes.md)

## 스크립트

| 파일 | 역할 |
|---|---|
| `scripts/fetch-cognee-mcp.sh` | 고정된 공식 MCP 소스 다운로드 |
| `scripts/prepare-rest.py` | 기존 DB·볼륨 백업 후 REST runtime 생성. 기존 컨테이너 잠시 정지·재개 |
| `scripts/bootstrap-rest-identity.py` | API 컨테이너에서 전용 사용자·키·데이터셋 최초 생성 |
| `scripts/hermes-secret-helper.py` | Hermes의 captured pipe에 키 공급. 단독 표시 거부, `--check` 제공 |
| `scripts/patch-hermes-unified-recall.py` | Hermes 1.4.1 통합 회상 로컬 패치 |
| `scripts/patch-hermes-recall-provenance.py` | 위 패치 이후 출처 표시 수정 |
| `scripts/verify-rest.py` | 합성 데이터셋에서 저장·검색·승격·삭제 검증. LLM 호출 발생 |
| `scripts/verify-unified-recall.py` | 지정한 기존 문구로 읽기 전용 검색·출처 검증 |
| `scripts/disable-rest.py` | 전용 키 폐기 후 REST만 종료. 볼륨과 기억은 유지 |

스크립트는 `~/cognee`의 Mac mini Docker Desktop 구성을 전제로 합니다.
실행 보고는 `deploy/`에 생성되고 Git에서 제외됩니다.

## 운영 이력

개인 경로와 노트 예시는 일반화했습니다. 문서 간 차이는 후속 수정 기록을 우선합니다.

1. [REST 최초 구성](operations/REST-HERMES-HANDOFF.md)
2. [command helper 인증 우선순위](operations/HERMES-AUTH-FIX.md)
3. [통합 회상과 공유 저장소 한계](operations/HERMES-UNIFIED-RECALL.md)
4. [문서 ID 기반 출처 표시](operations/HERMES-RECALL-PROVENANCE.md)
