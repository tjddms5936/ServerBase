#include "pch.h"
#include "Listener.h"

LPFN_ACCEPTEX		Listener::AcceptEx = nullptr;

Listener::Listener() :
	m_listenSocket(INVALID_SOCKET), m_core(nullptr), m_listenerEvent(nullptr)
{

}

Listener::~Listener()
{
	Close();
}

void Listener::Init(IocpCore* core)
{
	m_core = core;
	m_listenSocket = ::WSASocket(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED);

	m_listenerEvent = new IocpEvent(IocpEvent::Type::Accept, shared_from_this(), nullptr);

	m_core->Register(GetHandle(), 0);

	/* ��Ÿ�ӿ� �ּ� ������ API */
	ASSERT_CRASH(BindWindowsFunction(m_listenSocket, WSAID_ACCEPTEX, reinterpret_cast<LPVOID*>(&AcceptEx)));
}

bool Listener::BindWindowsFunction(SOCKET socket, GUID guid, LPVOID* fn)
{
	DWORD bytes = 0;
	return SOCKET_ERROR != ::WSAIoctl(m_listenSocket, SIO_GET_EXTENSION_FUNCTION_POINTER, &guid, sizeof(guid), fn, sizeof(*fn), &bytes, NULL, NULL);
}

void Listener::Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes)
{
	OnAccept(pIocpEvent, numOfBytes);
}

bool Listener::StartAccept(uint16 port, IocpCore* core)
{
	Init(core);

	if (m_listenSocket == INVALID_SOCKET)
	{
		cout << "socket() failed" << endl;
		return false;
	}

	SOCKADDR_IN serverAddr = {};
	serverAddr.sin_family = AF_INET;
	serverAddr.sin_addr.S_un.S_addr = htonl(INADDR_ANY);
	serverAddr.sin_port = htons(port);

	if (bind(m_listenSocket, (SOCKADDR*)&serverAddr, sizeof(serverAddr)) == SOCKET_ERROR)
	{
		cout << "bind() failed" << endl;
		return false;
	}

	if (listen(m_listenSocket, SOMAXCONN) == SOCKET_ERROR)
	{
		cout << "listen() failed" << endl;
		return false;
	}

	cout << "Listening on port : " << port << "..." << endl;

	PostAccept();
	//thread acceptThread([this]()
	//	{

	//		while (true)
	//		{
	//			PostAccept();

	//			//// Accept�Ǿ����� ���ο� ���� ���� �� ���
	//			//Session* session = new Session(clientSocket);
	//			//if (!m_core->Register(reinterpret_cast<HANDLE>(session->GetSocket()), 0))
	//			//{
	//			//	cout << "Register() failed" << std::endl;
	//			//	delete session;
	//			//	continue;
	//			//}

	//			//std::cout << "[Listener] Starting session...\n";
	//			//session->Start();
	//		}
	//	});

	//acceptThread.detach(); // ������ ���� ���� detach

	return true;
}

void Listener::PostAccept()
{
	std::cout << "[Listener] Waiting for connection...\n";

	// �񵿱� AcceptEX ���

	// 1. Ŭ���̾�Ʈ ���� ���� (OVERLAPPED ����)
	SOCKET clientSocket = ::WSASocket(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_OVERLAPPED);
	if (clientSocket == INVALID_SOCKET)
	{
		int err = WSAGetLastError();
		std::cerr << "WSASocket failed: " << WSAGetLastError() << std::endl;
	}

	// 2. AcceptEx�� �Ѱ��� ���� ����
	const int addrLen = sizeof(SOCKADDR_IN) + 16;
	const int bufferLen = addrLen * 2; // ���� + ����Ʈ �ּ�
	char* acceptBuffer = new char[bufferLen];

	// ��.. ������ �̸� ����� IOCP�� ��ϸ� Dispatch���� ���ָ� ���?
	// ������ ���� ������ ��Ȱ���ϸ�..?
	shared_ptr<Session> session = make_shared<Session>(clientSocket);

	// 3. Overlapped �̺�Ʈ ����
	// IocpEvent* pEvent = new IocpEvent(IocpEvent::Type::Accept, session);
	m_listenerEvent->SetPartsSession(session);

	DWORD bytesReceived = 0;
	BOOL result = AcceptEx(
		m_listenSocket,        // ���� ����
		clientSocket,          // ���� ����� Ŭ���̾�Ʈ ����
		acceptBuffer,          // �ּ� ������ ���� ����
		0,                     // ������ �ʱ� ������ ����
		addrLen,               // ���� �ּ� ũ��
		addrLen,               // ����Ʈ �ּ� ũ��
		&bytesReceived,        // �Ϸ� �� ���� ���� ����Ʈ ��
		m_listenerEvent        // OVERLAPPED ����ü
	);

	if (result == FALSE && WSAGetLastError() != WSA_IO_PENDING)
	{
		std::cerr << "AcceptEx failed: " << WSAGetLastError() << std::endl;
		delete[] acceptBuffer;
		closesocket(clientSocket);

		// �ϴ� �ٽ� Accept �ɾ��ش�.
		PostAccept();
	}

}

void Listener::Close()
{
	if(m_listenSocket != INVALID_SOCKET)
	{
		closesocket(m_listenSocket);
		m_listenSocket = INVALID_SOCKET;
	}
}

void Listener::OnAccept(IocpEvent* pIocpEvent, int32 numOfBytes)
{
	shared_ptr<Session> pSession = pIocpEvent->GetPartsSession();
	if (!m_core->Register(reinterpret_cast<HANDLE>(pSession->GetSocket()), 0))
	{
		cout << "register() failed" << std::endl;
		PostAccept();
		return;
	}

	pSession->Start();

	PostAccept();
}
