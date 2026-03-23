#include "pch.h"
#include "ServerSession.h"

ServerSession::ServerSession(SOCKET socket, SessionType eSessionType)
	: Session(socket, eSessionType)
{
	
}

void ServerSession::OnPacketReceived(PACKET_NUMBER pkgID, int32 pkgSize, InputMemoryStream& stream)
{
	std::cout << "[Server] Received packet [ID,Size]: [" << static_cast<int32>(pkgID) << "," << pkgSize;

	switch (pkgID)
	{
	case PACKET_NUMBER::CS_CHAT: {HANDLE_CP_CHAT(stream); } break;
	default:
		cout << "] ... error" << endl;
		std::cerr << "[ServerSession] Unknown packet ID: " << static_cast<int32>(pkgID) << endl;
		break;

	}
}

void ServerSession::HANDLE_CP_CHAT(InputMemoryStream& stream)
{
	CP_CHAT pkg;
	pkg.DeSerializePayload(stream);

	std::cout << "]\t payload: " << pkg.data << endl << endl;
	// PostSend(packet.data(), packetSize); // 에코 기본 방법

	// 정책 통일. 헤더는 SendPacket이 생성
	SP_CHAT sendmsg;
	sendmsg.data = "Hello my world";
	SendPacket(&sendmsg);
}
