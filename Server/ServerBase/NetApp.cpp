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
	// ���� �ʱ�ȭ
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

	if (!m_listener->StartAccept(4000, m_core.get()))
	{
		std::cerr << "[ServerApp] Listener accept start failed\n";
		return false;
	}

	m_workerpool = make_unique<IOCPWorkerPool>(m_core.get(), thread::hardware_concurrency());
    return true;
}

void ServerApp::Run()
{
	m_workerpool->Start();

	ServerControlLoop();
}

void ServerApp::Finalize()
{
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
	// WinSock �ʱ�ȭ
	WSADATA wsaData;
	if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
	{
		std::cerr << "WSAStartup failed\n";
		return false;
	}

	// �ھ� ����
	m_core = make_unique<IocpCore>();
	if (!m_core->Initialize())
		return false;

	// ���� ����
	m_socket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (m_socket == INVALID_SOCKET)
	{
		std::cerr << "socket() failed\n";
		return false;
	}

	// ���� ���� �õ�
	std::cout << "[Client] Calling connect()...\n";
	if (!ConnectToServer())
		return false;
	std::cout << "[Client] Connected to server.\n";

	// ���� ���� �� IOCP ���
	std::cout << "[Client] Making Session and Register IOCP...\n";
	m_session = make_shared<Session>(m_socket, SessionType::Client);
	if (!m_core->Register(reinterpret_cast<HANDLE>(m_socket), 0))
	{
		std::cerr << "[ClientApp] IOCP Register failed\n";
		return false;
	}
	std::cout << "[Client] Complete making Session and Register IOCP\n";

	// ���� ���� (���� ���� ��û)
	std::cout << "[Client] Session Start\n";
	m_session->Start();

	// ��Ŀ ������ Ǯ ����
	std::cout << "[Client] Worker Thread Pool Start\n";
	m_workerpool = make_unique<IOCPWorkerPool>(m_core.get(), 2);
    return true;
}

void ClientApp::Run()
{
	m_workerpool->Start();
	std::cout << "[Client] Connected. Type message (quit to exit)\n";

	std::string input;
	while (true)
	{
		std::getline(std::cin, input);
		if (input == "quit")
			break;

		// 1) ��Ŷ ���� ��� (��� 2����Ʈ + payload)
		uint16 packetSize = static_cast<uint16>(input.size() + sizeof(uint16));

		// 2) ��Ŷ ���� ����
		// std::vector<char> packet(packetSize);
		// memcpy(packet.data(), &packetSize, sizeof(uint16));        // ���
		// memcpy(packet.data() + sizeof(uint16), input.data(), input.size()); // Payload

		// 3) ����
		// m_session->PostSend(packet.data(), static_cast<int>(packet.size()));

		// ���� ������ ���� �Լ� �ϳ��� �ذ�. ������ ��Ģ ���� �ϱ� ����.
		m_session->SendPacket(input.data(), (int)input.size());
	}
}

void ClientApp::Finalize()
{
	if (m_workerpool)
		m_workerpool->Stop();

	if (m_socket != INVALID_SOCKET)
		closesocket(m_socket);

	WSACleanup();
}

bool ClientApp::ConnectToServer()
{
	// ���� �ּ� ����
	SOCKADDR_IN serverAddr = {};
	serverAddr.sin_family = AF_INET;
	serverAddr.sin_port = htons(m_serverPort);                   // ���� ��Ʈ
	inet_pton(AF_INET, m_serverIp.c_str(), &serverAddr.sin_addr); // ���� ������

	if (connect(m_socket, (SOCKADDR*)&serverAddr, sizeof(serverAddr)) == SOCKET_ERROR)
	{
		std::cerr << "connect() failed: " << WSAGetLastError() << std::endl;
		return false;
	}
	return true;
}
