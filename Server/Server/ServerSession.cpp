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
    case PACKET_NUMBER::CS_CHAT: { if (!HANDLE_CP_CHAT(stream)) return; } break;
    default:
        cout << "] ... error" << endl;
        std::cerr << "[ServerSession] Unknown packet ID: " << static_cast<int32>(pkgID) << endl;
        break;

    }
}

bool ServerSession::HANDLE_CP_CHAT(InputMemoryStream& stream)
{
    CP_CHAT pkg;
    if (!pkg.DeSerializePayload(stream))
    {
        cout << "] ... malformed payload" << endl;
        std::cerr << "[ServerSession] Failed to deserialize CS_CHAT payload" << endl;
        CloseSocket();
        return false;
    }

    if (stream.GetRemainingSize() != 0)
    {
        cout << "] ... malformed payload" << endl;
        std::cerr << "[ServerSession] CS_CHAT payload has trailing bytes: " << stream.GetRemainingSize() << endl;
        CloseSocket();
        return false;
    }

    std::cout << "]\t payload: " << pkg.data << endl << endl;

    SP_CHAT sendmsg;
    sendmsg.data = "Hello my world";
    SendPacket(&sendmsg);
    return true;
}