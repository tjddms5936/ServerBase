#include "pch.h"
#include "IocpCore.h"
#include "Listener.h"

#define MAX_BUFFER_SIZE 1024

int main()
{
	int iResult = 0;
    // 윈속 초기화
	WSADATA wsaData = {};
	iResult = WSAStartup(MAKEWORD(2, 2), &wsaData);
	if (iResult != 0)
	{
		cout << "WSAStartup() Error\tErrorCode : " << iResult << endl;
		return -1;
	}

	// 서버 소켓 생성
	// 소켓에 주소 할당 
	// 연결 요청 대기 상태
	// 연결 요청 수락
	// 데이터의 송수신
	// 클라이언트 연결 종료
	// 서버 연결 종료

	IocpCore core;
	if (!core.Initialize())
	{
		std::cout << "IOCP Initialize failed\n";
		return -1;
	}

	// 클라이언트 접속 대기 + accept + Session생성 후 IOCP 포트 등록 + 수신 대기
	shared_ptr<Listener> listener = make_shared<Listener>();
	listener->StartAccept(4000, &core);

	// 이벤트 대기 및 처리
	while (true)
	{
		core.Dispatch();
	}

	// 윈도우 소켓 해제
	WSACleanup();
}