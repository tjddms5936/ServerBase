#pragma once

// ---------------------
//	네이밍 규칙 : 
//		CS - Client To Server  (클라쪽에서 사용)
//		SC - Server To Client  (서버쪽에서 사용)
// ---------------------
enum class PACKET_NUMBER : uint16
{
	CS_CHAT = 0,
	SC_CHAT = 1,
	CS_TEST = 2,
	SC_TEST = 3,
};