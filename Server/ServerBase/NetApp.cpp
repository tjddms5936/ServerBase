#include "pch.h"
#include "NetApp.h"

ServerApp::ServerApp() :
    m_core(make_unique<IocpCore>()), m_listener(make_shared<Listener>())
{
}

ServerApp::~ServerApp()
{
    Finalize();
}

bool ServerApp::Initialize()
{
	int iResult = 0;
	WSADATA wsaData = {};
	iResult = WSAStartup(MAKEWORD(2, 2), &wsaData);
	if (iResult != 0)
	{
		std::cerr << "WSAStartup() Error\tErrorCode : " << iResult << endl;
		return false;
	}

	if (!m_core->Initialize())
	{
		std::cerr << "IOCP Initialize failed\n";
		return false;
	}

	uint32 hwThreadCnt = thread::hardware_concurrency();
	uint32 ioThreadCnt = std::max<uint32>(2, hwThreadCnt / 2);
	uint32 logicThreadCnt = std::max<uint32>(1, hwThreadCnt);

	if (!m_listener->StartAccept(4000, m_core.get(), static_cast<int32>(ioThreadCnt)))
	{
		std::cerr << "[ServerApp] Listener accept start failed\n";
		return false;
	}

	m_workerpool = make_unique<IOCPWorkerPool>(m_core.get(), static_cast<int32>(ioThreadCnt), static_cast<int32>(logicThreadCnt));
    return true;
}

void ServerApp::Run()
{
	m_workerpool->Start();

	ServerControlLoop();
}

void ServerApp::Finalize()
{
	if (m_listener)
	{
		m_listener->Close();
		m_listener->WaitForAllAcceptEventsReturned(1000);
	}

	if (m_workerpool)
		m_workerpool->Stop();

	WSACleanup();
}
void ServerApp::ServerControlLoop()
{
	while (m_brunning)
	{
		string command;
		cin >> command;

		if (command == "quit")
		{
			m_brunning = false;
		}
	}
}

ClientApp::ClientApp(const string& ip, uint16_t ui16port) :
	m_serverIp(ip), m_serverPort(ui16port)
{

}

ClientApp::~ClientApp()
{
	Finalize();
}

bool ClientApp::Initialize()
{
	WSADATA wsaData;
	if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
	{
		std::cerr << "WSAStartup failed\n";
		return false;
	}

	m_core = make_unique<IocpCore>();
	if (!m_core->Initialize())
		return false;

	m_socket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (m_socket == INVALID_SOCKET)
	{
		std::cerr << "socket() failed\n";
		return false;
	}

	std::cout << "[Client] Calling connect()...\n";
	if (!ConnectToServer())
		return false;
	std::cout << "[Client] Connected to server.\n";

	std::cout << "[Client] Making Session and Register IOCP...\n";

	if (m_ClientSessionFactory != nullptr)
		m_session = m_ClientSessionFactory(m_socket);

    if (m_session == nullptr)
    {
        std::cerr << "[Listener] Session nullptr. Closing socket" << endl;
        closesocket(m_socket);
        m_socket = INVALID_SOCKET;
        return false;
    }

    SOCKET sessionSocket = m_session->GetSocket();
    m_socket = INVALID_SOCKET;

    if (!m_core->Register(reinterpret_cast<HANDLE>(sessionSocket), 0))
    {
        std::cerr << "[ClientApp] IOCP Register failed\n";
        m_session->CloseSocket();
        return false;
    }
	m_session->SetIocpCore(m_core.get());
	std::cout << "[Client] Complete making Session and Register IOCP\n";

	std::cout << "[Client] Session Start\n";
	m_session->Start();

	std::cout << "[Client] Worker Thread Pool Start\n";
	m_workerpool = make_unique<IOCPWorkerPool>(m_core.get(), 2, 1);
    return true;
}

void ClientApp::Run()
{
	m_workerpool->Start();
	std::cout << "[Client] Connected. Type message (quit to exit)\n";

	m_session->Run();
}

void ClientApp::Finalize()
{
	if (m_workerpool)
		m_workerpool->Stop();

    if (m_socket != INVALID_SOCKET)
    {
        closesocket(m_socket);
        m_socket = INVALID_SOCKET;
    }

	WSACleanup();
}

bool ClientApp::ConnectToServer()
{
	SOCKADDR_IN serverAddr = {};
	serverAddr.sin_family = AF_INET;
	serverAddr.sin_port = htons(m_serverPort);
	inet_pton(AF_INET, m_serverIp.c_str(), &serverAddr.sin_addr);

	if (connect(m_socket, (SOCKADDR*)&serverAddr, sizeof(serverAddr)) == SOCKET_ERROR)
	{
		std::cerr << "connect() failed: " << WSAGetLastError() << std::endl;
		return false;
	}
	return true;
}
