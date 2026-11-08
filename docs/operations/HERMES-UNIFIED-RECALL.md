# Hermes 통합 회상 로컬 수정 — 2026-11-07

후속 출처 표시 수정은 [HERMES-RECALL-PROVENANCE.md](HERMES-RECALL-PROVENANCE.md)를 참고한다.

사용자 정책: 기억은 출처별 데이터셋에 저장하되, 기본 회상은 읽기 가능한
모든 데이터셋을 검색한다. Hermes는 `hermes_default_memory`, Obsidian은
`obsidian`에 계속 저장한다. 데이터를 합치거나 옮기는 작업은 하지 않았다.

수정 대상은 맥미니의 Hermes Cognee 플러그인 1.4.1이다.

- `~/.hermes/plugins/cognee/cognee_integration_hermes/provider.py`:
  명시적 회상 및 자동 prefetch의 graph memory lane에 기본 전체 검색 옵션 적용.
  코드 검색과 세션 guidance의 데이터셋 범위는 유지한다.
- 같은 디렉토리 `config.py`: `recall_all_readable_datasets` 옵션 추가.
  기본값 false로 두어 다른 프로필의 기존 요청 정책을 유지한다.
- `schemas.py`: `cognee_recall`의 선택적 `datasets` 목록 추가.
- `http_backend.py`: 명시한 이름/UUID를 GET datasets로 확인하여 공식 REST의
  `dataset_ids`로 전달한다. 공유 데이터셋의 이름은 코어의 소유자 전용 이름
  해석과 다르므로 UUID를 사용한다. 모르는 이름·중복된 이름은 오류로 처리한다.
- `~/.hermes/cognee.json`: default 프로필에만
  `"recall_all_readable_datasets": true` 설정.

현재 REST는 그대로 `http://127.0.0.1:18081`이다. 서버 코드·API 스키마·인증·DB
구성·저장/삭제 코드·다른 프로필 설정은 변경하지 않았다. `.env`도 직접 열람하지
않았다. 별도 기억 저장소를 만들지 않았다. 통합 회상은 읽기 가능한 데이터셋의
검색을 의미하며, 등록되지 않은 다른 사용자의 기억을 무조건 읽겠다는 정책은 아니다.

백업은 맥미니의
`~/cognee/backups/hermes-before-unified-recall-20261107T153130Z`이다.
대상 파일별 원본 및 SHA-256 manifest가 있으며, 이 원본을 원래 경로에 복원한 뒤
gateway를 재시작하면 변경 전으로 돌아갈 수 있다. 기억 볼륨 복원/삭제는 필요 없다.

재적용 스크립트: `scripts/patch-hermes-unified-recall.py --check` / `--apply`.
검증 스크립트: `scripts/verify-unified-recall.py`.
플러그인 공식 업데이트 시 로컬 수정이 덮어써질 수 있다. 스크립트는 예상한
소스와 다르면 부분 적용을 거부하므로, 버전 변경 시 다시 검토한다.

실제 설치된 검색 메서드와 실제 REST를 이용한 검증에서 기본 전체 회상으로
Obsidian의 검증용 노트 노트 및 Hermes의 인증 수정 기억이 각각 검색됐다.
자동 prefetch의 graph lane은 데이터셋 제한 없이 호출되고 실제 Obsidian 문맥을
반환했으며, session guidance는 Hermes 데이터셋 UUID로 제한된 요청을 유지했다.
다른 프로필 설정 파일 해시도 변경되지 않았다.

## 확인된 서버 한계

현재 `ENABLE_BACKEND_ACCESS_CONTROL=false` 공유 graph/vector 모드에서는
`CHUNKS_LEXICAL`에 Obsidian UUID만 지정해도 Hermes 문서 청크가 섞였다.
원본 파일 2개의 Obsidian 데이터에는 해당 고유 표식이 없고, Hermes 원본
문서 1개에만 있는 것을 대조했다. 따라서 클라이언트가 올바른 공식 범위를
전송하더라도 **엄격한 데이터셋 검색 격리는 현재 서버에서 검증되지 않았고,
오히려 범위를 넘는 결과를 확인했다.** 결과의 dataset_name 라벨만으로 실제
원본 소속을 단정하지 않는다.

이는 통합 회상이라는 개인용 목적에는 영향을 주지 않지만, 특정 데이터셋만
읽도록 보장하거나 신뢰할 수 없는 다중 사용자를 격리하는 용도로 사용하면 안 된다.
이 문제를 감추기 위해 서버를 수정하거나 자동 마이그레이션을 켜지 않았다.
엄격한 제한이 필요하면 별도 저장소/접근 제어 구성과 공식 코어 동작을 재검토해야 한다.
이전 인계에서 명시적 dataset 필터만으로 검색 격리가 보장되는 듯 설명한 부분은
이 실제 검증 결과를 기준으로 정정한다. 저장 원본과 API 쓰기/삭제 ACL은 별개다.

비밀 없는 검증 보고는 맥미니의 `deploy/unified-recall-validation.json`,
실제 새 Hermes 세션 결과는 `deploy/hermes-unified-live-validation.json`에 보관한다.
