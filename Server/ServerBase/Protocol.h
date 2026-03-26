#pragma once
#include "ServerPch.h"
#include "MemoryStream.h"
#include "ProtocolHeaderID.h"

// 패킷 헤더 정의
struct PacketHeader
{
	PacketHeader()
	{
		pkgID = 0;
		pkgSize = 0;
		sizeof(PacketHeader);
	}
	int32 pkgID;
	int32 pkgSize; // 헤더 포함 도탈 사이즈
};

struct IIocpPacket
{
	virtual ~IIocpPacket() = default;
	virtual void SerializePayload(OutputMemoryStream& stream) = 0; // 페이로드만 직렬화
	virtual bool DeSerializePayload(InputMemoryStream& stream) = 0; // 페이로드만 역직렬화
	virtual shared_ptr<IIocpPacket> Clone() const = 0;

	PacketHeader PkgHeader;
};

struct CP_CHAT : IIocpPacket
{
	CP_CHAT()
	{
		PkgHeader.pkgID = static_cast<int32>(PACKET_NUMBER::CS_CHAT);
		PkgHeader.pkgSize = 0;

		data.clear();
		data2 = 0;
	}

	void SerializePayload(OutputMemoryStream& stream) override
	{
		stream.Serialize(data, data2);
	}

	bool DeSerializePayload(InputMemoryStream& stream) override
	{
		return stream.DeSerialize(data, data2);
	}

	shared_ptr<IIocpPacket> Clone() const override
	{
		return make_shared<CP_CHAT>(*this);
	}

	std::string data;
	int data2;
};

struct SP_CHAT : IIocpPacket
{
	SP_CHAT()
	{
		PkgHeader.pkgID = static_cast<int32>(PACKET_NUMBER::SC_CHAT);
		PkgHeader.pkgSize = 0;

		data.clear();
	}

	void SerializePayload(OutputMemoryStream& stream) override
	{
		stream.Serialize(data);
	}

	bool DeSerializePayload(InputMemoryStream& stream) override
	{
		return stream.DeSerialize(data);
	}

	shared_ptr<IIocpPacket> Clone() const override
	{
		return make_shared<SP_CHAT>(*this);
	}

	std::string data;
};
