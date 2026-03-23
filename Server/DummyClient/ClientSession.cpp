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
	case PACKET_NUMBER::SC_CHAT: { HANDLE_SP_CHAT(stream); } break;

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

		// 1) 패킷 길이 계산 (헤더 2바이트 + payload)
		// uint16 packetSize = static_cast<uint16>(input.size() + sizeof(uint16));

		// 2) 패킷 버퍼 생성
		// std::vector<char> packet(packetSize);
		// memcpy(packet.data(), &packetSize, sizeof(uint16));        // 헤더
		// memcpy(packet.data() + sizeof(uint16), input.data(), input.size()); // Payload

		// 3) 전송
		// m_session->PostSend(packet.data(), static_cast<int>(packet.size()));

		// 위의 과정을 다음 함수 하나로 해결. 서버와 규칙 통일 하기 위함.
		// m_session->SendPacket(input.data(), (int)input.size());

		CP_CHAT sendmsg;
		sendmsg.data = "Hellooo";
		SendPacket(&sendmsg);
	}
}

void ClientSession::HANDLE_SP_CHAT(InputMemoryStream& stream)
{
	SP_CHAT pkg;
	pkg.DeSerializePayload(stream);

	std::cout << "]\t payload: " << pkg.data << endl << endl;
	// PostSend(packet.data(), packetSize); // 에코 기본 방법

}
