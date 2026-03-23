#pragma once
#include "Session.h"
#include "Protocol.h"

class ClientSession : public Session
{
public:
	ClientSession(SOCKET socket, SessionType eType);
	~ClientSession() = default;
	ClientSession(const ClientSession&) = delete;
	ClientSession& operator=(const ClientSession&) = delete;


public:
	// 인터페이스
	virtual void OnPacketReceived(PACKET_NUMBER pkgID, int32 pkgSize, InputMemoryStream& stream) override;
	virtual void Run() override;

private:
	// 패킷별 처리 함수
	void HANDLE_SP_CHAT(InputMemoryStream& stream);
};