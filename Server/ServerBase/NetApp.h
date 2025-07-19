#pragma once
#include "IocpCore.h"
#include "Listener.h"
#include "IOCPWorkerPool.h"

class ClientSession;

class NetAppBase
{
public:
	virtual ~NetAppBase() = default;

	virtual bool Initialize() = 0;
	virtual void Run() = 0;
	virtual void Finalize() = 0;
};

class ServerApp : public NetAppBase
{
public:
	ServerApp();
	virtual ~ServerApp();
	virtual bool Initialize() override;
	virtual void Run() override;
	virtual void Finalize() override;

private:
	void ServerControlLoop();
private:
	unique_ptr<IocpCore> m_core;
	shared_ptr<Listener> m_listener;
	unique_ptr<IOCPWorkerPool> m_workerpool;
	atomic<bool> m_brunning = true;
};

class ClientApp : public NetAppBase
{
public:
	ClientApp() {}

	virtual bool Initialize() override;
	virtual void Run() override;
	virtual void Finalize() override;

private:
	std::shared_ptr<ClientSession> m_session;
};