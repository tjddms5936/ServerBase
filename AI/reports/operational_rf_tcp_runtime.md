# 실제 RF와 C++ TCP 종단 검증

기존 모델·threshold·Test를 바꾸지 않고 로컬 실제 서버 경로를 검사했다.

| 시나리오 | 저장 | 실패 | 파싱 거부 | 요약 제외 | 큐 유실 | 종료 폐기 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| actual_rf_messages | 28 | 0 | 0 | 3 | 0 | 0 |
| fault_busy | 1 | 1 | 0 | 1 | 0 | 0 |
| fault_bad_json | 1 | 0 | 1 | 1 | 0 | 0 |
| fault_wrong_id | 1 | 0 | 1 | 1 | 0 | 0 |
| fault_wrong_model | 1 | 0 | 1 | 1 | 0 | 0 |
| fault_wrong_policy | 1 | 0 | 1 | 1 | 0 | 0 |
| fault_oversized | 1 | 1 | 0 | 1 | 0 | 0 |
| fault_delay | 1 | 1 | 0 | 1 | 0 | 0 |
| api_unavailable | 0 | 1 | 0 | 0 | 0 | 3 |
| bounded_queue_shutdown | 1 | 0 | 0 | 0 | 96 | 4 |
| concurrent_load | 120 | 0 | 0 | 8 | 0 | 0 |
| compatibility_summary | 1 | 0 | 0 | 1 | 0 | 0 |

## 범위와 한계

로컬 기능·유한 부하 검사이며 새로운 정확도 평가·운영 SLA·장시간 안정성 증명이 아니다.
성공 경로는 실제 승인 RF이고 장애 응답·지연은 검사 전용 앱에서 주입했다.
TCP 왕복에는 RF 완료를 기다리는 시간이 포함되지 않는다. API·worker HTTP 지연과 분리한다.
종료 예산은 대기 큐 배출 기준이다. 진행 중 WinHTTP의 단계별 timeout까지 추가로 기다릴 수 있다.
세션·결과 저장소의 보존 상한·TTL, 실행 중 관리자 조회 API, 자동 재시도는 구현하지 않았다.
종료 시 열린 클라이언트 연결의 모두 닫힘은 별도 보장이 아니다. 검사는 연결을 닫고 worker 진행·큐 적체 상태에서 종료했다.

## 다음 작업

남은 큰 단계는 1개다. 재현 절차·실험 보고서·GitHub README·면접 설명을 최종 정리한다.
