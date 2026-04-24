# skill: experiment_start

목적
- 공격 루프를 시작하기 전에 C 서버와 실험 시간을 동기화한다.

주요 도구 조합
- `start_experiment`

절차
1. `start_experiment` 도구 호출
2. `experiment_id`, `started_epoch`, `duration_seconds` 확보
3. 남은 시간 계산 후 반복 루프 시작

중요 규칙
- start 성공 전에는 공격 단계로 넘어가지 않는다.
- 동일 실험에 대해 중복 start 호출을 피한다.
- 서버가 반환한 시간 정보를 실험 기준으로 사용한다.
