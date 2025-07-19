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
	// 윈속 초기화
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

bool ClientApp::Initialize()
{
    return false;
}

void ClientApp::Run()
{
}

void ClientApp::Finalize()
{
}
