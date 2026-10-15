# 승인 묶음 검증과 실제 RF FastAPI 커밋 제안

## 제목

```text
feat(ai): 승인 묶음 검증 및 FastAPI 실제 RF 추론 연결
```

## 본문

```text
[승인 묶음 검증과 기동 정책]
- operational_rf_serving.json에 완료 평가·승인 묶음 manifest의 예상 해시와 경로 고정
- 완료 평가·Test 판정·후보 잠금·정책 서명·승인 파일의 연결 검증
- 필수 평가 산출물 8개와 완료 기록·후보 잠금 등 총 10개 파일의 해시·크기 확인
- 허용 경로·파일 구성·로컬 승인·운영/자동 차단 금지 상태 검사
- 원본 feature 의미 버전·입력 순서·타입·단위·관측 범위·결측 거부 정책 대조
- label map·raw7 변환·무scaler·무추가 prefix·양성 클래스·threshold 0.7 검사
- 승인 당시 Python 주·부 버전과 numpy/scikit-learn 버전 호환 확인
- 검증한 동일 바이트에서 RF Pipeline을 한 번 로딩하고 종류·클래스·입력·설정 검사
- 기동 실패 시 dummy나 정상 판단으로 대체하지 않음
- 추론 기동에서 Train/Test CSV·예측 CSV·수집 JSONL·과거 source를 다시 열지 않음

[FastAPI 실제 메시지 추론]
- ApprovedRfService와 실제 RF 전용 rf_main 진입점 추가
- 공통 create_app으로 기존 dummy 수집·회귀 테스트 경로 보존
- 기존 v2 JSON 검증 뒤 원본 feature 7개를 float64 고정 순서로 전달
- RF 양성 클래스 점수를 threshold 0.7과 비교해 normal/controlled_anomaly 반환
- 기존 C++ 응답 필드 request_id·label·is_attack·confidence·model_version·received_event_type 유지
- anomaly_score·decision_threshold·decision_scope를 추가해 점수와 판정 기준 분리
- confidence는 반환 클래스의 점수이며 보정된 실제 위험 확률이 아님을 문서화
- health에 실제 모델 종류·버전·준비 상태·지원 이벤트·threshold·모델 해시 표시
- 요청마다 모델을 재로딩하거나 승인 파일을 다시 읽지 않음

[실패·요약·동시성 처리]
- session_summary는 메시지 RF에 입력하지 않고 422 unsupported_event_type 반환
- 가짜 정상 요약 응답으로 C++ 마지막 RF 메시지 결과를 덮어쓰지 않음
- 선택적 수집은 판단과 독립적으로 유지하며 summary 수집도 추론과 구분
- 미준비·계산 실패는 503을 반환하고 dummy/정상 fallback 금지
- 실제 계산을 작업 스레드로 보내 이벤트 루프의 health 응답 유지
- 앱 프로세스당 RF 동시 계산 1개로 제한하고 초과 요청은 503 inference_busy 반환
- 요청 취소 시 계산이 끝날 때까지 실행 슬롯 유지
- Uvicorn 단일 worker·HTTP 동시성 상한·graceful shutdown 설정 추가
- 기존 C++의 summary 실패 카운터·503 미재시도는 후속 통합 과제로 명시

[실행과 의존성]
- 로컬 RF 실행기와 자동 검사 전용 stdin quit 정상 종료 기능 추가
- run_fastapi.bat의 기본 실행을 실제 승인 RF 진입점으로 연결
- 기존 Server 실행 배치의 AI 전달 구조는 변경하지 않음
- 배치 파일 ASCII·CRLF 유지 및 필요한 런타임 존재 확인
- requirements-inference.txt에 검증된 과학 계산·API 런타임 버전 고정
- 기존 inference_api.main:app은 수집·회귀 전용 명시적 dummy로 보존

[회귀 및 실제 HTTP 검증]
- 임시 합성 모델로 기동 검증·파일 변조·미승인·버전·입력 의미·경로 검사 추가
- RF 응답·0.7 경계·confidence·metadata 비입력·C++ 필드 호환 검사 추가
- summary 미추론·입력 거부·단일 로딩·요청 파일 I/O 금지·503·동시성 검사 추가
- 새 테스트 20개를 포함해 전체 357개 테스트 통과
- 실제 Uvicorn을 별도 로컬 포트에서 실행해 HTTP 검사 5개 수행
- 수작업 snapshot 3개의 점수·판단이 직접 RF 계산과 정확히 일치함을 확인
- 실제 이상 점수 0.863468은 이상, 첫 snapshot 0.633246은 0.7 미만 정상으로 반환
- summary와 잘못된 입력은 422로 거부하고 수집 기능 비활성 확인
- 소유한 검사 서버를 정상 종료하고 승인·구현 파일 21개의 보호 해시 확인
- HTTP 보고서와 로그를 저장하되 새 탐지 성능·독립 Test 결과로 주장하지 않음

[한글 문서와 후속 범위]
- 승인 검증·파일 역할·실행 경로·응답 의미·실패·요약·동시성·재현 절차 문서화
- AI/evaluation/artifacts/reports/scripts README와 로드맵 갱신
- 이전 모델·학습·비용·Test 승인 산출물과 C++ 서버 소스 보존
- 기존 Test FN 531건·잘못된 길이 FN 71/72건 등의 모델 한계는 그대로 공개
- FastAPI 실제 추론 연결 완료에 따라 남은 큰 작업 묶음 3개에서 2개로 갱신
- 다음은 실제 C++ TCP 요청·판단 결과 저장·장애·부하·종료 검증
```

## 커밋 범위

새 승인 설정·검증기·RF 서비스·진입점·실행기·고정 의존성·테스트·HTTP 검사기·한글 문서·API 보고서와 수정된 공통 API/응답 schema/실행 배치/README/로드맵을 포함한다. `.deps`, 모델 바이너리, 원본 데이터, 검사용 서버 로그 등 기존 Git 제외 대상은 유지한다. Server 폴더 변경은 없다.
