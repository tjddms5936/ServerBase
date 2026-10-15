# 실제 RF와 C++ TCP 종단 검증

## 이번에 완료한 일

실제 클라이언트 메시지에서 만들어진 원본 feature 7개가 비동기 큐·C++ worker·FastAPI·승인 RF를 거쳐 해당 세션의 마지막 성공 판단으로 저장되는지 확인했다. 새 모델 학습·threshold 변경·공식 Test 재평가는 하지 않았다. 모델과 승인 묶음은 이전 단계 그대로다.

```text
테스트 클라이언트 → 기존 TCP·Session → 기존 FeatureCollector
→ bounded AsyncFeatureQueue → 별도 AiInferenceWorker
→ 실제 FastAPI 검증·RF 점수·0.7 판단
→ C++ JSON 검증 → InferenceResultStore → 종료 진단 보고서
```

TCP 응답은 AI 판단을 기다리지 않는다. AI 오류가 나도 해당 메시지를 정상이라고 대신 판단하거나 클라이언트 연결을 자동 차단하지 않는다.

## 파일별 변경

| 파일 | 역할 |
| --- | --- |
| `Server/FeatureExtraction/AiInferenceWorker.h/.cpp` | 명시적 RF 모드의 요약 제외, 구조화된 응답 검사, 지연 응답 거부, 응답 크기 상한, 종료 배출 예산·폐기 통계를 추가한다. |
| `Server/FeatureExtraction/InferenceResult.h` | RF 점수·정책·세션 내 메시지 번호·HTTP 왕복 시간을 보존한다. |
| `Server/FeatureExtraction/AiRuntimeSupport.h` | 실행 옵션 해석과 종료 시 세션별 마지막 성공·누락 상태·통계의 JSON 저장을 담당한다. |
| `Server/Server/main.cpp` | 실행 옵션을 주입하고 생산 연결을 끊은 뒤 TCP 스레드·AI worker 순으로 정리하고 선택적 진단 파일을 쓴다. |
| `Server/run_server.bat` | 마우스 실행을 `--ai-rf` 모드로 연결한다. |
| `Server/Server/Server.vcxproj`, `Server/ServerBase/ServerBase.vcxproj` | 해당 파일의 Windows SDK JSON 사용을 위한 빌드 설정을 추가한다. |
| `Server/tests/AiRuntimeTests.cpp/.vcxproj` | 큐 닫힘·포화·동시 생산자·worker 시작/종료·미판단을 독립 검사한다. |
| `AI/tests/rf_runtime_api.py` | 실제 RF 앱을 감싼 검사 전용 기록·장애 주입기다. 운영 앱에는 제어 경로가 없다. |
| `AI/evaluation/verify_rf_tcp_runtime.py` | 소유한 API·실제 C++ 서버·클라이언트를 실행하고 12개 종단 시나리오·정상 종료·해시를 검사한다. |
| `AI/tests/test_rf_tcp_runtime.py` | 프레임·응답 읽기·통계·덮어쓰기 거부·검사 전용 장애 모드를 검사한다. |

`Session`, 프로토콜, `FeatureCollector`, 큐의 기존 계산·입력 의미는 수정하지 않았다. 응답 파서는 부분 문자열 검색 대신 Windows SDK의 `Windows.Data.Json`을 사용한다. v142·Windows SDK 10.0.19041의 이름 탐색 호환 설정은 worker와 서버 진입점에만 한정하고 진입점은 공통 PCH를 사용하지 않는다. 외부 JSON 라이브러리를 다운로드하지 않는다.

수정 대상의 기존 CP949 파일 4개만 UTF-8 BOM으로 변환해 한글 주석을 보존했다. 전체 저장소의 문자 인코딩을 바꾸지는 않았다.

## 두 실행 모드

- `--ai-rf`: 승인 버전 `rf-raw7-recovery-v1-t0.7`·원본 메시지·0.7·로컬 참고용 정책을 검사한다. 요약은 HTTP 호출 없이 별도 제외 수로 센다. 실행 배치의 기본 모드다.
- 인자 없는 실행: 과거 수집·dummy 호환 경로다. message와 summary를 전송한다. 실제 RF가 summary에 422 `unsupported_event_type`을 주면 장애가 아닌 요약 제외로 센다. 다른 422를 임의로 정상 처리하지 않는다. 실제 RF 시연에는 반드시 명시적 RF 모드를 사용한다.

RF 모드에서는 JSON 타입, 유한한 점수 범위, `request_id == queue_sequence_id`, 응답 이벤트·버전·threshold·scope, score에 따른 label·is_attack·confidence의 일관성을 모두 확인한다. 검사 실패 응답은 결과 저장소에 넣지 않는다. 입력 JSON과 원본 feature의 생성 방식은 그대로 유지한다.

## 마지막 성공과 최신 판단의 차이

예를 들어 같은 세션에서 1번 메시지 판단은 성공했지만 2번 요청에 503이 발생하면 다음과 같이 기록한다.

```text
request_count = 2
has_successful_result = true
last_success_request_index = 1
covers_latest_message = false
```

이전 결과를 지우지는 않지만 현재 메시지까지 판단됐다고 표시하지 않는다. 한 번도 성공하지 않았으면 `has_successful_result=false`이며 정상 판단을 만들어 넣지 않는다. 응답 수신 시각·원본 응답·세션 ID·큐 순번도 보존한다. 이는 종료 시점의 최소 조회 데모이며 실행 중 관리자 HTTP API나 전체 콘솔 화면을 추가한 것은 아니다.

## 실패와 종료 정책

- HTTP·접속·크기 제한·예산 초과 오류는 실패 수에 기록하고 다음 item을 처리한다. 자동 재시도는 하지 않는다.
- 2xx라도 잘못된 JSON·식별자·승인 정책이면 파싱 거부 수에 기록한다.
- 응답 본문은 최대 64 KiB다. 로컬 HTTP는 프록시를 통하지 않고 자동 리다이렉트도 따르지 않는다.
- 연결·송신·수신 timeout을 양수로 제한한다. 응답 헤더 대기 옵션도 별도로 지정한다.
- HTTP 반환이 세 단계 timeout 합을 넘겼으면 늦은 응답으로 세고 마지막 성공을 갱신하지 않는다. 이 검사는 이미 걸린 HTTP 호출을 즉시 취소하는 기능은 아니다.
- 종료 때 collector의 큐 연결을 먼저 해제하고 TCP 객체를 정리한 뒤 worker 큐를 닫는다. 종료 배출 예산이 지나면 나머지 item을 폐기하고 별도 수로 남긴다.
- `Stop()` 동시 호출은 mutex로 직렬화하며 닫힌 큐의 worker 재시작은 거부한다.

종료 예산 100ms를 지정했다고 프로세스가 반드시 100ms 안에 끝나는 것은 아니다. 진행 중 동기 WinHTTP·TCP 정리를 더 기다릴 수 있다. 응답 헤더 timeout 통지는 실제 수신 시점까지 지연될 수 있다는 점도 고려해야 한다. [Microsoft WinHTTP 옵션 설명](https://learn.microsoft.com/en-us/windows/win32/winhttp/option-flags#winhttp_option_receive_response_timeout)

미리 생성한 accept 대기용 세션은 실제 연결이 시작된 세션과 구분한다. 종료 시 이 객체들의 해제 이벤트를 새 수집 레코드로 내보내지 않으며, 진단 파일에서는 `unstarted_session_count`로만 별도 집계한다. 실제 연결을 모두 종료한 뒤 검사했으므로, 열린 클라이언트의 강제 종료까지 포괄하는 완전한 운영 수명주기를 증명하지는 않는다.

## 실제 검사 결과

Python 전체 회귀 테스트 365개가 통과했다. 새 Python 검사 8개·C++ runtime 4개 검사 그룹·기존 수집기 테스트·Debug x64 솔루션 빌드를 확인했다. 기존 dummy 프로토콜 종단 검사 8개도 통과했고 이전 보고서를 덮어쓰지 않도록 `artifacts/inference_api/rf_v1/legacy_runtime_check/`에 따로 저장했다. 전체 회귀 테스트의 임시 합성 모델 학습은 기존 테스트 동작이며 승인 RF를 재학습하거나 공식 Test를 다시 평가한 것은 아니다.

최종 결과는 `reports/operational_rf_tcp_runtime_v2.json/.md`, 실제 로그·세션 보고서·HTTP 기록·해시는 `artifacts/inference_api/rf_v1/tcp_runtime_v2/`에 있다. 보호 해시 33개와 보고서·로그·진단 파일 28개를 재검증했다. 접미사 없는 보고서와 `tcp_runtime_attempt5/`는 초기 성공 기록으로 보존하며, 이후 빌드·검사 파일까지 보호 범위를 넓히고 최종 빌드로 전체 12개를 다시 실행했다. 보고서 이름의 v2는 검사 실행 버전이지 모델·feature 계약 변경이 아니다. 이전 실패 시도도 별도 디렉터리의 실패 기록으로 보존한다. 첫 시도에서 미시작 accept 세션 집계 문제를 확인했고, 지연 검사에서는 OS timeout과 실제 반환 시간이 같지 않음을 확인해 명시적 늦은 응답 거부를 추가했다.

| 검사 | 결과 |
| --- | --- |
| 간격·묶음·분할 전송 3세션 | 28개 메시지 전송·실제 RF 직접 계산·최종 세션 응답 일치, 요약 3개 제외 |
| 503 | 두 번째 판단은 저장하지 않고 첫 성공만 보존 |
| 잘못된 JSON·요청 ID·모델 버전·threshold | 각각 파싱 거부 1건, 과거 결과는 최신 메시지를 포함하지 않는다고 표시 |
| 70,000바이트 응답 | 크기 제한 실패, 잘못된 결과 저장 없음 |
| 2,500ms 지연 | 늦은 응답 1건 거부, 이전 성공만 보존, TCP 응답은 계속 처리 |
| API 미실행 | 저장 0건, 실패 1건·종료 폐기 3건, 미판단을 정상으로 표시하지 않음 |
| 큐 용량 4·지연 700ms·100메시지 | TCP 응답 100개 수신, 성공 1개·큐 유실 96개·종료 폐기 4개 |
| 8세션·120메시지 | 성공 120개·요약 제외 8개, 전송/파싱 실패·큐 유실 0개 |
| 호환 모드 요약 | 메시지 1개 저장, summary 422를 제외 1건으로 분리, 실패 0개 |

큐 포화의 101개 item은 메시지 100개와 종료 요약 1개다. 유실·폐기는 메시지뿐 아니라 요약도 포함하므로 각 수를 모델의 FN으로 해석하지 않는다. 최종 실행에서 적체 종료의 프로세스 정리는 약 733ms, API 미실행은 약 1,660ms였다. 새 RF 종단 검사에서 소유한 C++·API 프로세스는 모두 정상 종료했고 다른 서버를 종료하지 않았다.

동시 8세션 TCP 왕복 120개의 중앙값은 약 0.356ms, p95는 약 0.756ms다. **AI 완료 시간은 아니다.** TCP 응답은 비동기 추론과 독립적으로 처리되므로 API 경로 시간·worker HTTP 시간·TCP 왕복을 구분한다. 종료 보고서의 HTTP 시간은 각 세션의 마지막 성공 표본만 있으며 전체 메시지의 큐 대기 p95를 뜻하지 않는다. 테스트용 기록 middleware의 오버헤드도 포함한다. 장시간 부하·최대 처리량·실운영 SLA를 주장하지 않는다.

## 실행 방법

서버 코드를 변경했으므로 기존 실행 중인 본인 서버를 종료하고 Debug x64로 다시 빌드한다. Visual Studio에서 솔루션을 빌드하거나 개발자 PowerShell의 Server 폴더에서 실행한다.

```powershell
msbuild .\Server.sln /p:Configuration=Debug /p:Platform=x64
```

마우스 시연은 AI의 `run_fastapi.bat` → Server의 `run_server.bat` → Server의 `run_dummy_client.bat` 순서다. 서버 종료는 서버 콘솔에서 `quit`, API 종료는 API 콘솔에서 Ctrl+C다. 기존 클라이언트를 먼저 종료한다. 자동 검사 서버는 이미 종료했으므로 직접 사용할 때는 배치로 다시 시작한다.

세션 결과 파일을 남기는 수동 서버 실행 예시는 Server 폴더에서 다음과 같다. 같은 이름의 보고서는 덮어쓰지 않으므로 매번 새 이름을 사용한다.

```powershell
.\BIN\Debug\Server.exe --ai-rf --ai-report "$PWD\manual_rf_result_01.json"
```

`--ai-port`로 API 포트, `--ai-queue-capacity`로 큐 상한, `--ai-timeout-ms`로 단계별 timeout, `--ai-drain-ms`로 종료 배출 예산을 지정할 수 있다. TCP 포트는 기존 4000을 유지한다. 보고서는 종료 뒤 확인한다.

자동 검사는 API·서버를 따로 켜지 않은 상태에서 AI 폴더에서 실행한다. 기본 결과가 이미 있으므로 새 출력 경로를 명시한다.

```powershell
python -X utf8 .\evaluation\verify_rf_tcp_runtime.py --artifact-dir artifacts\inference_api\rf_v1\tcp_manual_01 --report-prefix reports\rf_tcp_manual_01
```

4000 포트가 사용 중이면 검사 전에 실패하며 기존 서버를 강제 종료하지 않는다. API는 빈 로컬 포트를 선택한다. 승인 모델과 고정 의존성이 로컬에 필요하며 데이터셋 CSV·공식 Test를 열지 않는다. 검사 전용 API를 수동 운영이나 외부 공개에 사용하지 않는다.

## 남은 한계와 다음 작업

최종 Test에서 드러난 FN 531건·잘못된 길이 누락·첫 메시지 한계는 그대로다. 이번 검사는 통신·저장·유실·오류 분리를 확인한 것이지 탐지 성능의 개선이나 외부 공격 대응 보증이 아니다.

세션 저장소의 TTL·보존 상한, 영속 결과 저장, 실행 중 관리자 조회 API, 실제 서버와 동일한 지속 부하·네트워크 장애·악의적 무한 전송의 강제 취소, 열린 연결 종료 보장은 별도 운영 확장이다. 현재 로컬 참고용 MVP의 범위를 넘는 항목을 완료했다고 표시하지 않는다.

남은 큰 단계는 **1개**다. 재현 절차·아키텍처·실험 결과와 한계를 정리하고 최종 GitHub README·면접 설명 자료를 마무리한다.
