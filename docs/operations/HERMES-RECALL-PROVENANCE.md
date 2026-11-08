# 통합 회상 출처 표시 수정 — 2026-11-07

사용자가 Hermes에서 검증용 노트를 회상했을 때 원본은 Obsidian인데 답변 말미에
`dataset hermes_default_memory`라고 표시되는 문제를 확인했다.

실제 원본을 대조한 결과, `검증 문구 → 검증용 노트`가 있는 문서는
`obsidian`에 저장되어 있다. `hermes_default_memory`에는 통합 회상 검증 기록이
검증용 노트를 언급하지만 해당 원본 문구 원문은 없다. 원본 노트가 이동한 것이 아니다.

원인은 provider.py가 `memory_credit.tool_credit_instruction(self._dataset, ...)`
으로 활성 쓰기 데이터셋을 출처 문구에 고정한 것이다. 통합 회상으로 읽기 범위를
확장하면서 이 기존 문구가 남아 있었다. 공유 graph 모드의 서버 dataset 라벨도
원본 소속을 증명하는 근거로 사용할 수 없다.

맥미니의 `~/.hermes/plugins/cognee/cognee_integration_hermes/provider.py`만 추가 수정했다.

- 원본 검색 결과 metadata의 data_id/document_name을 유지한다.
- 공식 REST의 읽기 가능한 데이터셋 목록 및 해당 문서 목록으로 data_id 소속을
  검증한다. 원문 내용을 읽어서 판별하지 않는다.
- 확인된 결과에만 dataset_id/dataset_name/document_name과
  provenance_status=verified를 표시한다.
- 검증 실패·시간 초과·문서 ID 없는 합성 답변은 출처 미확인으로 남긴다.
  출처 조회는 총 3초 예산 안에서 수행하며 기억 검색 자체를 실패시키지 않는다.
- 결과 전체가 한 데이터셋으로 검증됐을 때만 그 이름을 공통 출처로 사용한다.
  혼합 결과와 자동 회상의 합성 문맥에는 일반적인 `Cognee memory` 표현을 사용한다.
- 도구 결과에는 각 사실의 검증된 원본 데이터셋/문서를 따라 인용하고 활성 쓰기
  데이터셋을 출처로 가정하지 말라는 지침을 추가했다.

서버 코드·DB·저장·삭제 정책·.env는 변경하지 않았다.

백업(맥미니):
`~/cognee/backups/hermes-before-provenance-fix-20261107T154743Z/provider.py`

재적용: `scripts/patch-hermes-recall-provenance.py`.
검증: `scripts/verify-unified-recall.py`에 실제 REST 원본 문서 소속 대조와 혼합/
미확인 출처의 잘못된 기본 데이터셋 표기를 막는 검증을 추가했다.
실제 API 기반 검증에서 원본 문구 검증용 노트 항목은 obsidian으로 검증됐고,
혼합 결과의 전체 출처가 hermes_default_memory로 고정되지 않음을 확인했다.

이 수정은 출처 표기의 정확성을 높이는 것이며 공유 저장소의 엄격한 검색 범위
격리 문제를 해결한 것은 아니다. 자동/합성 회상에서 문서별 근거가 없으면
그 사실을 숨기고 특정 데이터셋을 추정하지 않는다.

실제 새 Hermes 대화에서도 답변이 obsidian을 출처로 언급하고 활성 데이터셋
이름을 출처로 강제하지 않음을 확인했다. 후속 대화가 원문을 인용해 Hermes에
저장되면 같은 문구가 두 데이터셋에 존재할 수 있다. 이때도 문구만 보고
데이터 이동을 추정하지 않고 각 문서의 실제 소속을 확인한다. 통합 검색 결과의
모든 원본 문구 언급이 영원히 Obsidian에만 있어야 한다고 검증해서는 안 된다.
