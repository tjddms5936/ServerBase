#include "pch.h"
#include "ClientSession.h"

ClientSession::ClientSession(SOCKET socket, SessionType eType)
    :Session(socket, eType)
{
}

void ClientSession::OnPacketReceived(PACKET_NUMBER pkgID, int32 pkgSize, InputMemoryStream& stream)
{
    std::cout << "[Client] Received packet [ID,Size]: [" << static_cast<int32>(pkgID) << "," << pkgSize;
    switch (pkgID)
    {
    case PACKET_NUMBER::SC_CHAT: { if (!HANDLE_SP_CHAT(stream)) return; } break;

    default:
        cout << "] ... error" << endl;
        std::cerr << "[ClientSession] Unknown packet ID: " << static_cast<int32>(pkgID) << endl;
        break;
    }
}

void ClientSession::Run()
{
    std::string input;
    while (true)
    {
        std::getline(std::cin, input);
        if (input == "quit")
            break;

        CP_CHAT sendmsg;
        sendmsg.data = "Hellooo";
        if (!QueuePacketToWorker(sendmsg))
        {
            std::cerr << "[ClientSession] Failed to queue send request" << std::endl;
            break;
        }
    }
}

bool ClientSession::HANDLE_SP_CHAT(InputMemoryStream& stream)
{
    SP_CHAT pkg;
    if (!pkg.DeSerializePayload(stream))
    {
        cout << "] ... malformed payload" << endl;
        std::cerr << "[ClientSession] Failed to deserialize SC_CHAT payload" << endl;
        CloseSocket();
        return false;
    }

    if (stream.GetRemainingSize() != 0)
    {
        cout << "] ... malformed payload" << endl;
        std::cerr << "[ClientSession] SC_CHAT payload has trailing bytes: " << stream.GetRemainingSize() << endl;
        CloseSocket();
        return false;
    }

    std::cout << "]\t payload: " << pkg.data << endl << endl;
    return true;
}
