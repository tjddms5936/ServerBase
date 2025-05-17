#include "pch.h"
#include "iostream"
#include <WinSock2.h>

#define MAX_BUFFER_SIZE 1024

int main()
{
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
	//SOCKET serverSocket = socket(PF_INET, SOCK_STREAM, IPPROTO_TCP);
	//if (serverSocket == INVALID_SOCKET)
	//{
	//	cout << "socket() Error" << endl;
	//	return -1;
	//}

	//// 소켓에 주소 할당 
	//SOCKADDR_IN sockAddr = {};
	//sockAddr.sin_family = AF_INET;
	//sockAddr.sin_port = htons(4000); 
	//sockAddr.sin_addr.S_un.S_addr = htonl(INADDR_ANY);
	//if (bind(serverSocket, (sockaddr*)&sockAddr, sizeof(SOCKADDR_IN)) == SOCKET_ERROR)
	//{
	//	cout << "bind() Error" << endl;
	//	return -1;
	//}

	//// 연결 요청 대기 상태
	//listen(serverSocket, SOMAXCONN);

	//char message[MAX_BUFFER_SIZE] = {};
	//int length = 0;

	//// 연결 요청 수락
	//SOCKADDR_IN ClientAddr = {};
	//int size = sizeof(SOCKADDR_IN);
	//SOCKET clientSocket = accept(serverSocket, (SOCKADDR*)&ClientAddr, &size); // addrlen에서 참조하는 정수(size)에는 처음에 addr이 가리키는 공간의 양이 포함됩니다. 반환할 때 반환된 주소의 실제 길이(바이트)를 포함합니다.
	//if (clientSocket == SOCKET_ERROR)
	//{
	//	closesocket(serverSocket);
	//	WSACleanup();
	//}

	//// 데이터의 송수신
	//while ((length = recv(clientSocket, message, MAX_BUFFER_SIZE, 0)) != 0)
	//{
	//	send(clientSocket, message, length, 0);
	//}

	//// 클라이언트 연결 종료
	//closesocket(clientSocket);

	//// 서버 연결 종료
	//closesocket(serverSocket);

	//// 윈도우 소켓 해제
	//WSACleanup();
}