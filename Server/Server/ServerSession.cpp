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
    case PACKET_NUMBER::CS_CHAT: { if (!HANDLE_CP_CHAT(stream, pkgSize)) return; } break;
    default:
        cout << "] ... error" << endl;
        std::cerr << "[ServerSession] Unknown packet ID: " << static_cast<int32>(pkgID) << endl;
        // 알 수 없는 ID는 한 번 기록하고 닫아 남은 payload 검사와 중복 계수하지 않는다.
        ReportProtocolError("unknown packet id", 0, static_cast<int32>(pkgID), pkgSize);
        CloseSocket();
        return;

    }
}

bool ServerSession::HANDLE_CP_CHAT(InputMemoryStream& stream, int32 packetSize)
{
    CP_CHAT pkg;
    if (!pkg.DeSerializePayload(stream))
    {
        cout << "] ... malformed payload" << endl;
        std::cerr << "[ServerSession] Failed to deserialize CS_CHAT payload" << endl;
        // 업무 payload 실패도 종료 summary의 오류 수에 포함한다.
        ReportProtocolError("failed to deserialize CS_CHAT payload", 0, static_cast<int32>(PACKET_NUMBER::CS_CHAT), packetSize);
        CloseSocket();
        return false;
    }

    if (stream.GetRemainingSize() != 0)
    {
        cout << "] ... malformed payload" << endl;
        std::cerr << "[ServerSession] CS_CHAT payload has trailing bytes: " << stream.GetRemainingSize() << endl;
        ReportProtocolError("CS_CHAT payload has trailing bytes", 0, static_cast<int32>(PACKET_NUMBER::CS_CHAT), packetSize);
        CloseSocket();
        return false;
    }

    std::cout << "]\t payload: " << pkg.data << endl << endl;

    SP_CHAT sendmsg;
    sendmsg.data = "Hello my world";
    SendPacket(&sendmsg);
    return true;
}
