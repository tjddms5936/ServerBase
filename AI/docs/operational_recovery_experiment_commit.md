# 보정 RF 학습·Validation 비교 커밋 정리

## 제목

```text
feat: 보정 RF 후보 직접 학습과 새 Validation 20개 정책 비교·선정 구현
```

## 본문

```text
- 고정 보정 계획을 실행하는 별도 학습 실행기·경로 설정·비교 모듈 추가
- 수집 완료 근거와 사전 역할·계획·기준 RF·처리된 Train/Validation 해시 검증
- 새 Validation 예측과 학습 전에 입력·변환·규칙·환경·모델·코드 protocol 잠금
- 새 Train 7,976건으로 RF 후보 4개를 각각 한 번씩 직접 학습
- 원본 7개, 송수신량 제외 5개, 평균 간격 5개, 평균+파싱 바이트 합 6개 입력 비교
- 트리 100·최소 leaf 5·seed 42·n_jobs=1을 유지하고 scaler·Validation 합산 학습 금지
- 원본 v2 JSON schema와 모델 입력 변환 schema를 별도 저장
- request_index는 순서·누락·평균 분모에만 사용하고 원본 식별자를 예측 입력에 직접 넣지 않음
- 과거·현재만 사용하고 첫 메시지·malformed·짧은 세션과 독립 정답 유지

- 같은 새 Validation 2,737건에서 후보 4개 × threshold 5개의 20개 정책 비교
- 전체 FPR 10%·정상 묶음 FPR 10%·Recall 50% 조건을 모두 요구
- Recall·F1·FPR·후보 id·threshold의 사전 순위로 선정하고 임의 기준 완화 금지
- 기준 통과 2개 중 rf_raw7·threshold 0.7을 개발 정책으로 선정
- 선정 Validation FP 39·FN 275·FPR 1.98%·묶음 FPR 5.16%·Recall 63.96%·F1 75.66% 기록
- 평균+파싱 합 후보 0.7은 동일 Recall이지만 FP 65·F1 74.16%로 미선정
- 원본 RF 0.6의 묶음 FPR 10.17%를 반올림해 통과시키거나 threshold를 추가하지 않음

- 기존 RF·0.3은 재학습 없이 같은 새 Validation의 고정 기준선으로 예측
- 기존 기준선 FP 266·FN 71·FPR 13.48%·Recall 90.69%와 비교
- 새 원본 RF·0.3의 FP 209·FN 80으로 재학습과 threshold 변경 효과를 분리
- 기본 0.5·후보별 제약 통과 최적 정책·전체 선정 정책을 구분
- 같은 행의 수정/신규 FP/FN·변경 row_id·확률·시나리오·첫 메시지·burst prefix 기록
- 최종 선정에서 오탐은 줄지만 누락은 증가하며 파생 입력의 우월성을 주장하지 않음
- malformed_length 36건 중 33건 누락과 본문 관측·생성기·캠페인 한계 공개

- 후보별 모델·입력 schema·기본 정책·학습 요약·Validation 예측 저장
- 선정 모델·입력 변환·threshold와 비용/Test 미확인 상태를 selection.json에 기록
- 모든 후보를 재로딩해 Train/Validation 확률·저장 CSV·지표의 정확한 일치 확인
- 산출물 25개·보고서 4개·보호 파일 49개 해시와 20개 지표·선정 재검증
- 신규 테스트 15개를 포함한 전체 회귀 테스트 304개 통과
- 실제 실행에서 Train 전용 fit 4회 및 Test/raw/summary/송신/scaler fit 금지 검증
- 기존 모델·C++·FastAPI dummy·수집 데이터/결과·기존 학습/평가 코드 보존
- 비용 측정·새 Test 승인·실제 모델 배포는 미실행으로 명시
- 선정 raw7은 추가 Python prefix 상태가 필요 없으며 다음 비용 검증과 남은 큰 묶음 4개 문서화
```

## 범위

이번 커밋은 학습·Validation 비교 단계다. 앞선 수집 명령·원본 수집·데이터셋 분리 변경이 아직 남아 있다면 별도 단계의 내용으로 구분한다. `git add`나 `git commit`은 실행하지 않았다.

학습된 모델·비교 CSV는 기존 Git 제외 규칙을 따른다. 새 코드·설정·문서·요약 보고서를 커밋하며 배포 승인이나 독립 Test 개선 완료라고 적지 않는다.

## 다음 작업

선정 raw7·threshold 0.7 정책의 Validation 기반 지연시간·메모리·파일 비용을 확인하고 비용 통과 후보를 새 Test 공개 전에 잠근다. 이후 새 Test 승인 확인·FastAPI·TCP 통합·최종 문서화로 진행한다. 큰 작업 묶음은 아직 4개다.
