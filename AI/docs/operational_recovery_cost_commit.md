# 선정 RF 비용 검증 단계 커밋 제안

## 제목

```text
feat(ai): 선정 RF 비용 검증 및 독립 Test 전 후보 잠금 구현
```

## 본문

```text
[선정 정책과 측정 protocol]
- 새 Validation에서 이미 선정한 rf_raw7·raw7_v1·threshold 0.7만 비용 검증
- 후보 교체, 추가 threshold 탐색, 모델·scaler 재학습 없이 기존 정책 유지
- 원본 feature 7개·identity 전처리·추가 Python prefix 상태 불필요 조건 확인
- 독립 프로세스 3회, 동일 요청 300개, seed 42, 워밍업 20회, 배치 10회 고정
- RF n_jobs=1·계산 라이브러리 스레드 1개·worker timeout 180초 고정
- operational_recovery_cost.json과 전용 비용 실행기 추가

[기존 실험 보존과 provenance 검증]
- 기존 학습 완료 manifest의 모델·schema·예측·보고서 보호 해시 확인
- Train 7,976건·Validation 2,737건의 원본 입력·metadata·계약·분할 대조
- 기존 사전 선정 규칙과 protocol 해시를 다시 확인해 원래 선정 결과 유지
- 저장 모델의 전체 RF 설정, 클래스 0/1 순서, 입력 열, scaler 부재 검사
- 같은 Validation의 확률·정답·행 식별자와 전체·시나리오·첫 메시지 지표 재현
- 원본 후보 기본 threshold 0.5와 학습 selection을 수정하지 않음
- 원본 C++ 서버와 FastAPI dummy 구현을 변경하지 않음

[미승인 후보 묶음과 비용 측정]
- pipeline·원본 feature schema·모델 입력 schema·label map을 별도 후보 묶음으로 복사
- 선정 threshold 0.7 정책을 별도 decision_policy.json에 저장
- 비용 실행 전에 모델·정책·입력 행렬·측정 설정·승인 기준을 protocol로 잠금
- 기존 독립 비용 worker와 FrozenRuntime을 변경 없이 재사용
- 원본 배열→DataFrame→RF→threshold 경로와 사전 준비된 모델 단독 경로 분리
- 전체 Validation 배치 처리량과 실제 단일 요청 지연시간을 구분
- 단일·배치 확률 절대 오차 1e-5 이내 및 threshold 판단 완전 일치 검증
- RSS·USS·네이티브 peak·로딩 전후 차이·주요 배열·파일 저장 비용을 구분
- 다른 모델 프레임워크 비용이 섞이지 않도록 worker의 Torch 미로딩 확인
- 원본 시간 표본·반복 수·입력·모델·스레드 설정과 요약 재계산 검증
- 성공·실패·중단 뒤 부모의 기존 계산 스레드 환경 복원

[실제 비용 결과와 사전 예산 판정]
- 전처리 포함 단일 요청 중앙값 4.3371ms
- 가장 높은 프로세스 단일 요청 p95 4.7538ms: 사전 상한 25ms 통과
- 모델 단독 중앙값 4.2116ms
- 프로세스 전체 네이티브 peak RSS 154.90MiB: 사전 상한 512MiB 통과
- 필수 후보 파일 5개 합계 759.05KiB: 사전 상한 50MiB 통과
- 주요 모델 배열 합계 2,926.58KiB, 전체 Validation 배치 중앙값 12.5505ms 기록
- 단일 장비·오프라인 비용이며 JSON·HTTP·큐·feature 검증 비용은 제외
- 기존 Validation FPR 1.98%·정상 묶음 FPR 5.16%·Recall 63.96% 일치 확인

[독립 Test 전 후보 잠금과 승인 경계]
- 비용 통과 시에만 candidate_lock.json 생성
- 모델·0.7 threshold·원본 계약·파일 해시·승인 기준·정책 서명 고정
- Test 예상 해시는 dataset manifest에서만 읽고 실제 Test CSV는 해시 검사에도 열지 않음
- 기존 사전 기준인 전체 FPR 10% 이하·정상 묶음 FPR 10% 이하·Recall 50% 이상 유지
- 비용 실패 시 다른 후보 교체·기준 완화·Test 접근 없이 보류
- 상태를 candidate_locked_pending_independent_test로 기록
- Test 확인·holdout 예약·실제 배포 승인·자동 차단은 수행하지 않음
- candidate_bundle은 미승인 평가 자료이며 deployment_bundle을 생성하지 않음
- 비용 완료와 실행 오류·timeout·중단 기록을 분리하고 기존 결과 덮어쓰기 거부

[회귀 테스트와 산출물 검증]
- 비용 경계값, 미측정·비정상 값, 고정 정책, Test 보호, 해시 변조 검사 추가
- 미승인 상태·기본 정책 보존·stateful schema 오용·예측 불일치 검사 추가
- 실패·중단 환경 복원·결과 보존·측정 기록 불일치·실제 worker 판단 일치 검사 추가
- 새 테스트 15개를 포함해 전체 319개 테스트 통과
- 실제 실행에 Test·수집 원본 접근, 모델·scaler 재학습, 네트워크 송신 금지 검사 적용
- 완료 후 산출물 12개·보고서 2개·보호 파일 96개의 해시 재검증
- Validation NPZ 2,737×7·동일 요청 300개·원본 비용 표본·잠금 서명 대조

[한글 문서와 다음 작업]
- 비용 개념, 파일 역할, 실행 흐름, 실제 결과, 상태, 제한과 재측정 주의를 한글로 문서화
- AI·evaluation·artifacts·reports README와 프로젝트 로드맵 갱신
- FP 39건·FN 275건의 오탐/누락 trade-off와 malformed 길이 관측 한계 유지
- 다음 단계는 잠근 RF·0.7의 새 Test 5,407건 독립 승인 확인
- 남은 큰 작업 묶음은 4개이며 보정 독립 승인이 완료되면 3개로 전환
```

## 포함할 파일

- `AI/configs/operational_recovery_cost.json`
- `AI/evaluation/run_operational_recovery_cost.py`
- `AI/tests/test_operational_recovery_cost.py`
- `AI/docs/operational_recovery_cost.md`
- `AI/docs/operational_recovery_cost_commit.md`
- `AI/reports/operational_recovery_cost.json`
- `AI/reports/operational_recovery_cost.md`
- 수정한 `AI/README.md`, `AI/evaluation/README.md`, `AI/artifacts/README.md`, `AI/reports/README.md`, `AI/docs/project_roadmap.md`

이번 단계에는 Server 폴더 변경이 없다. 산출물은 기본 Git 제외 경로에 보관하므로 `.deps`, 원본 CSV, NPZ, 모델 바이너리를 강제로 포함하지 않는다. 추적 가능한 코드·설정·문서·결과 보고서가 이번 커밋의 범위다.
