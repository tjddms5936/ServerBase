#include "pch.h"
#include "NetApp.h"
#include "ServerSession.h"
#include "../FeatureExtraction/FeatureCollector.h"
#include "../FeatureExtraction/AsyncFeatureQueue.h"
#include "../FeatureExtraction/AiInferenceWorker.h"
#include "../FeatureExtraction/AiRuntimeSupport.h"
// #include "IocpCore.h"
// #include "Listener.h"
// #include "IOCPWorkerPool.h"

// 세션 팩토리에서 관찰자를 주입해 네트워크 코어와 특징 수집기를 연결
void SetupListener(shared_ptr<Listener> listener, shared_ptr<feature_extraction::INetworkEventObserver> observer)
{
	listener->SetSessionFactory(
		[observer](SOCKET socket) -> shared_ptr<Session>
		{
			shared_ptr<ServerSession> session = make_shared<ServerSession>(socket, SessionType::Server);
			session->SetNetworkEventObserver(observer);
			return session;
		});
}

int wmain(int argc, wchar_t** argv)
{
    try
    {
    const auto options = feature_extraction::ParseAiRuntimeOptions(argc, argv);

	shared_ptr<feature_extraction::FeatureCollector> featureCollector = make_shared<feature_extraction::FeatureCollector>();
	shared_ptr<feature_extraction::AsyncFeatureQueue> featureQueue = make_shared<feature_extraction::AsyncFeatureQueue>(options.queue_capacity);
	featureCollector->SetFeatureQueue(featureQueue);
	shared_ptr<feature_extraction::AiInferenceWorker> inferenceWorker = make_shared<feature_extraction::AiInferenceWorker>(featureQueue, options.worker);
	if (!inferenceWorker->Start())
		return -1;

    {
    // 생산자인 TCP 스레드를 먼저 정리한 다음 AI 큐를 닫아 종료 이후 유입을 막는다.
    ServerApp app;
	// Listener 설정
	SetupListener(app.GetListener(), featureCollector);

	if (!app.Initialize())
	{
		inferenceWorker->Stop();
		return -1;
	}

	app.Run();
    // accept 대기용 미시작 세션의 해제 이벤트를 새 수집 레코드로 보내지 않고 생산을 끊는다.
    featureCollector->SetFeatureQueue(nullptr);
    }
    const auto stopStarted = std::chrono::steady_clock::now();
	inferenceWorker->Stop();
    const double stopMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - stopStarted).count();
    feature_extraction::WriteAiRuntimeReport(options, *featureCollector, *featureQueue, *inferenceWorker, stopMs);
	return 0;
    }
    catch (...) { std::cerr << "[Server] AI configuration or report failure.\n"; return 2; }
	//int iResult = 0;
 //   // 윈속 초기화
	//WSADATA wsaData = {};
	//iResult = WSAStartup(MAKEWORD(2, 2), &wsaData);
	//if (iResult != 0)
	//{
	//	cout << "WSAStartup() Error\tErrorCode : " << iResult << endl;
	//	return -1;
	//}

	//// 서버 소켓 생성
	//// 소켓에 주소 할당
	//// 연결 요청 대기 상태
	//// 연결 요청 수락
	//// 데이터의 송수신
	//// 클라이언트 연결 종료
	//// 서버 연결 종료

	//IocpCore core;
	//if (!core.Initialize())
	//{
	//	std::cout << "IOCP Initialize failed\n";
	//	return -1;
	//}

	//// 클라이언트 접속 대기 + accept + Session생성 후 IOCP 포트 등록 + 수신 대기
	//shared_ptr<Listener> listener = make_shared<Listener>();
	//listener->StartAccept(4000, &core);

	//// 이벤트 대기 및 처리. // 성능비교 대상
	///*while (true)
	//{
	//	core.Dispatch();
	//}*/

	//// 여기서 workerPool.Stop() 해줘야 하나?
	//IOCPWorkerPool workerPool(&core, thread::hardware_concurrency());
	//workerPool.Start();

	//ServerControlLoop();

	//workerPool.Stop(); // 해주면 장점이 있음.

	//// 윈도우 소켓 해제
	//WSACleanup();
}
