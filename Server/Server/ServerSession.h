#pragma once
#include "Session.h"
#include "Protocol.h"

class ServerSession : public Session
{
public:
	ServerSession(SOCKET socket, SessionType eSessionType);
	virtual ~ServerSession() = default;
	ServerSession(const ServerSession&) = delete;
	ServerSession& operator=(const ServerSession&) = delete;

public:
	// 인터페이스
	virtual void OnPacketReceived(PACKET_NUMBER pkgHeader, int32 pkgSize, InputMemoryStream& stream) override;
	
	// 더미 클라용이라 여기서 사용 X
	virtual void Run() override {};

private:
	// 패킷별 처리 함수
	void HANDLE_CP_CHAT(InputMemoryStream& stream);

};

