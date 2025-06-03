#include "pch.h"
#include <ws2tcpip.h> // inet_pton 필요
#include <string>

int main()
{
    // WinSock 초기화
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        std::cerr << "WSAStartup failed\n";
        return -1;
    }

    // 소켓 생성
    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET)
    {
        std::cerr << "socket() failed\n";
        WSACleanup();
        return -1;
    }

    // 서버 주소 설정
    SOCKADDR_IN serverAddr = {};
    serverAddr.sin_family = AF_INET;
    serverAddr.sin_port = htons(4000);                   // 서버 포트
    inet_pton(AF_INET,"127.0.0.1", &serverAddr.sin_addr); // 로컬 루프백

    // 서버 연결 시도
    std::cout << "[Client] Calling connect()...\n";
    if (connect(sock, (SOCKADDR*)&serverAddr, sizeof(serverAddr)) == SOCKET_ERROR)
    {
        std::cerr << "connect() failed: " << WSAGetLastError() << std::endl;
        closesocket(sock);
        WSACleanup();
        return -1;
    }

    std::cout << "[Client] Connected to server.\n";

    while (true)
    {
        std::string input;
        std::cout << "Message> ";
        std::getline(std::cin, input);

        if (input == "quit")
            break;

        send(sock, input.c_str(), static_cast<int>(input.size()), 0);

        char buffer[1024] = {};
        int recvLen = recv(sock, buffer, sizeof(buffer), 0);
        if (recvLen > 0)
        {
            std::cout << "[Client] Echoed: " << std::string(buffer, recvLen) << std::endl;
        }
    }

    closesocket(sock);
    WSACleanup();
    return 0;
}