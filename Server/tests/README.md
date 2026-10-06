# FeatureCollector 테스트

이 프로젝트는 실제 FeatureCollector와 AsyncFeatureQueue를 독립 실행 파일로 컴파일한다. 서버의 TCP 처리나 HTTP 호출은 테스트에 포함하지 않는다.

Visual Studio에서 `FeatureCollectorTests.vcxproj`를 열고 Debug x64로 빌드하거나, Visual Studio 개발자 PowerShell에서 다음을 실행한다.

```powershell
msbuild .\tests\FeatureCollectorTests.vcxproj /p:Configuration=Debug /p:Platform=x64
.\tests\bin\Debug\FeatureCollectorTests.exe
```

프로젝트 toolset은 기존 서버 x64와 같은 v142다. Visual Studio 2019 C++ 도구 또는 해당 호환 도구가 필요하다.

검사 내용:

- 수신/송신 바이트, 파싱 수, 오류 수, 첫 메시지 및 평균 간격
- 메시지 이후 발생한 오류가 과거 message snapshot에 들어가지 않는지
- 종료 후 완료/오류/파싱/중복 close/동일 ID 재시작이 snapshot을 바꾸지 않는지
- 종료 이후 8개 스레드에서 들어온 이벤트를 운영 통계에 정확히 계수하는지
- 큐가 가득 차도 collector 종료 상태를 유지하는지
- 메시지 0개 또는 1개 세션의 평균 간격이 0인지

실제 서버와 FastAPI 연동 테스트는 AI 폴더의 `tests/run_runtime_e2e.py`로 수행한다.
