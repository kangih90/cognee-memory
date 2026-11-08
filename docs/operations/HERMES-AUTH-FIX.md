# Hermes Cognee 인증 후속 검증 — 2026-11-07

기존 REST-HERMES-HANDOFF.md 작성 후 Hermes 담당자가 default 프로필을 설정했다.
이후 실제 도구 요청에서 401이 발생해 사용자의 승인으로 아래 설정 한 항목만 변경했다.

```yaml
secrets:
  command:
    override_existing: true
```

서버 키 직접 호출은 200이었고, 기존 command helper 출력도 보호된 서버 키와
일치했다. 기존 helper의 override_existing은 미지정(기본 false)이었다.
기존 환경값에 밀리지 않도록 정상 helper의 값을 우선하도록 바꾼 뒤
공식 `hermes gateway restart`를 실행했다. 서버 인증·Hermes 코드·다른 프로필은
변경하지 않았다. `.env` 내용을 직접 열람하거나 키를 출력하지 않았다.

설정 백업(맥미니):
`~/cognee/backups/hermes-config-before-command-override-20261107T071022.yaml`

실제 gateway 경로의 새 Hermes 단발 세션 두 개로 검증했다.

- 첫 세션: cognee_remember 성공, cognee_recall 성공, 고유 합성 표식 검색 성공.
- 해당 실행의 신규 Cognee 401 및 키 누락 로그: 0.
- 다른 새 세션: cognee_recall 성공, 앞서 저장한 표식과 `cobalt lantern` 내용 일치.
- 두 실행 모두 종료 코드 0. 단순 status의 API key 표시로 성공 판정하지 않았다.
- 비밀값·도구 결과 본문을 로그로 출력하지 않고 결과 상태만 추출했다.

맥미니의 `deploy/hermes-auth-validation.json`에 비밀값 없는 검증 결과를 보관한다.
이 수정은 올바른 command helper가 기존 값보다 우선하도록 한 것으로,
기존 `.env`의 값이 왜 적용되지 않았는지 파일 내용을 읽어 확정한 것은 아니다.
같은 기억 저장소와 `hermes_default_memory`를 계속 사용한다.
