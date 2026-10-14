# 최종 평가 단계 커밋 제안

## 범위

이 문서는 최종 성능·비용 비교 단계의 커밋 내용이다. 실제 Git commit·stage는 수행하지 않았다. 작업 시작 시 앞 단계의 시계열 준비·LSTM 구현 변경도 미커밋으로 남아 있었으므로, 단계별로 나누려면 앞 단계 변경과 아래 이번 단계 파일을 구분해야 한다. README·로드맵처럼 두 단계가 함께 수정한 파일은 변경 부분을 확인한다.

## 제목

```text
운영 LR/RF/LSTM 최종 Test·비용 평가와 사전 후보 잠금 및 승인 보류 구현
```

## 본문

```text
- Validation에서 선정한 LR C=0.1·0.5, RF leaf=5·0.3, LSTM h16·0.5를 고정 비교 대상으로 지정
- 기존 Train/Validation·시계열·모델·보고서의 해시 및 같은 행 재예측 지표 검증
- 기존 학습용 Test 금지 로더와 학습 코드 유지, 최종 평가 전용 Test 로더·창 구성 모듈 분리
- 모델별 새 프로세스 3회·같은 Validation 300개 요청·워밍업 20회·배치 10회·CPU 계산 스레드 1로 비용 측정
- 모델 단독과 전처리/threshold 포함 지연시간, 프로세스 RSS/USS/native peak·주요 배열·필수 파일 저장 비용 구분
- LR/RF 프로세스에는 Torch를 로드하지 않아 LSTM 라이브러리 메모리 비용과 분리
- float32 단일/배치 확률 오차는 사전 상한 1e-5로 검증하고 실제 threshold 판단은 정확히 일치하도록 검사
- 최초 비용 점검 실패 v1을 보존하고 Test 공개 전에 수치 검사 수정 후 새 v2 경로에서 재실행
- Validation·비용 기반 후보 선택과 candidate_lock 저장 뒤에만 Test 접근 허용
- holdout 사용 예약·재실행 노출 표시 및 같은 Test에서 모델/threshold/승인 조건 변경을 통한 재선정 방지
- Test 1,071개 메시지의 해시·정답·metadata·run 배치·분할 비중복 검증
- 동일 session의 과거·현재 최대 8개 Test 창 생성, 짧은 세션·길이 1 유지와 저장 후 원본 재검증
- 고정 Test에서 LR FP/FN 113/131, RF 122/9, LSTM 60/104 및 시나리오·coverage·prefix 비교 기록
- Test 전 잠근 RF의 FPR 16.01%가 사전 10% 상한을 넘었으므로 승인 보류
- Test 결과를 보고 LSTM으로 대체하거나 threshold를 사후 변경하지 않음
- 승인 통과 시 모델·schema·label map·선정 threshold 묶음을 만드는 경로 구현 및 테스트
- 현재는 배포 묶음을 생성하지 않고 FastAPI dummy·기존 C++ 서버 유지
- 접근 순서·비용 제약·승인 보류·재실행·worker 실패·창 정합성 테스트와 한글 문서 추가
- 신규 최종 평가 테스트 17개를 포함한 전체 262개 통과, 저장 모델의 고정 Test 재예측·지표·CSV 일치 확인
- 최종 산출물 19개·보고서·원본·모델·코드·lock 해시 보존 확인
- 다음 보정·새 독립 검증을 포함해 남은 실제 작업 묶음을 4개로 갱신
```

## 이번 단계의 핵심 파일

- `AI/configs/operational_final_evaluation.json`
- `AI/requirements-evaluation.txt`
- `AI/evaluation/operational_final_data.py`
- `AI/evaluation/operational_final_models.py`
- `AI/evaluation/operational_cost_worker.py`
- `AI/evaluation/run_operational_final_evaluation.py`
- `AI/tests/test_operational_final_evaluation.py`
- `AI/reports/operational_final_evaluation.json`
- `AI/reports/operational_final_evaluation.md`
- `AI/reports/operational_final_evaluation.predictions.csv`
- `AI/docs/operational_final_evaluation.md`
- `AI/docs/operational_final_evaluation_commit.md`
- 관련 README와 `AI/docs/project_roadmap.md`의 이번 단계 부분

`AI/artifacts/operational_final_evaluation/`과 실제 데이터·로컬 의존성은 기존 Git 제외 규칙을 유지한다. Server 폴더 변경은 이번 단계에 없다.
