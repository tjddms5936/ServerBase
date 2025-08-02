#include "pch.h"
#include "RecvBuffer.h"

RingRecvBuffer::RingRecvBuffer(ullong bufferSize) :
	m_vRecvbuffer(bufferSize), m_ullCapacity(bufferSize), m_ullReadPos(0), m_ullWritePos(0)
{

}

void RingRecvBuffer::GetRecvWsaBuf(WSABUF outBufs[2])
{
	ullong ullFreeSpace = GetFreeSize();

	// RingBuffer가 꽉 차서 더 이상 수신 데이터를 쓸 공간이 없는 경우
	if (ullFreeSpace == 0)
	{
		// 오버 플로우 방지
		/*
			- 이 상태에서 또 WSARecv를 걸어버리면, OS는 데이터를 버퍼에 쓰려고 시도함
			- 실제로는 쓸 공간이 없으므로 쓰기 포인터가 읽기 포인터를 덮어버리는(Overrun) 문제가 발생
			
			WSARecv()에 유효하지 않은 버퍼 길이 0을 넘겨주면
				- OS가 이 소켓에 대한 Recv를 수행하지 않고 즉시 WSA_IO_PENDING → 0바이트 이벤트 발생
				- 또는 조건에 따라 아예 IO가 걸리지 않음 

			즉, 빈 버퍼로 Recv를 걸어주면서 버퍼를 덮어쓰지 않도록 방지

			정리:
				free space가 0이면, 더 이상 받을 공간이 없으므로
				nullptr 버퍼를 줘서 OS가 데이터 쓰기를 시도하지 않게 막는 것이 곧 오버플로우 방지.
		*/

		outBufs[0].buf = nullptr;
		outBufs[0].len = 0;

		outBufs[1].buf = nullptr;
		outBufs[1].len = 0;
		return;
	}

	if (m_ullWritePos >= m_ullReadPos)
	{
		// case 1 : 단일 연속 공간 + wrap 공간.   writePos~end, 0~readPos
		/*----------------------------------
				RingBuffer의 상태 표현

			0                capacity-1
			[읽은][데이터][데이터][빈공간→][빈공간→]
					^readPos        ^writePos


			- 빈공간은 writePos~끝까지 연속, 그 뒤 0~readPos까지 wrap 공간
				- outBufs[0] → writePos부터 끝까지 (연속 공간)
				- outBufs[1] → 0부터 readPos 직전까지 (wrap-around 공간)
		-----------------------------------*/
		ullong ullTailSpace = m_ullCapacity - m_ullWritePos;
		outBufs[0].buf = &m_vRecvbuffer[m_ullWritePos];
		outBufs[0].len = static_cast<ULONG>(ullTailSpace);

		// wrap 공간 
		outBufs[1].buf = &m_vRecvbuffer[0];
		outBufs[1].len = static_cast<ULONG>(m_ullReadPos);
	}
	else
	{
		// case 2 : 연속 공간 한 덩어리만.	writePos~readPos-1
		/*----------------------------------
				RingBuffer의 상태 표현

			0                capacity-1
			[빈공간→][데이터][데이터][데이터]
			^writePos       ^readPos

			- 빈공간은 writePos~readPos-1까지 연속 한 덩어리
				- outBufs[0] → writePos~readPos-1
				- outBufs[1] → 필요 없음 (nullptr)
		-----------------------------------*/
		ullong ullFreeChunk = m_ullReadPos - m_ullWritePos;
		outBufs[0].buf = &m_vRecvbuffer[m_ullWritePos];
		outBufs[0].len = static_cast<ULONG>(ullFreeChunk);
		outBufs[1].buf = nullptr;
		outBufs[1].len = 0;
	}
}

void RingRecvBuffer::CommitWrite(ullong bytes)
{
	// 수신 완료 후 numOfBytes만큼 쓰기 포인터 이동
	m_ullWritePos = (m_ullWritePos + bytes) % m_ullCapacity;
}

void RingRecvBuffer::CommitRead(ullong bytes)
{
	m_ullReadPos = (m_ullReadPos + bytes) % m_ullCapacity;
}

ullong RingRecvBuffer::GetUseSize() const
{
	// 읽을 수 있는 데이터 크기
	if (m_ullWritePos >= m_ullReadPos)
		return m_ullWritePos - m_ullReadPos;
	else
		return m_ullCapacity - (m_ullReadPos - m_ullWritePos);
}

char* RingRecvBuffer::Peek(ullong& outLen)
{
	if (m_ullReadPos == m_ullWritePos)
	{
		outLen = 0;
		return nullptr;
	}

	if (m_ullWritePos > m_ullReadPos) 
		outLen = m_ullWritePos - m_ullReadPos; // 연속 데이터
	else
		outLen = m_ullCapacity - m_ullReadPos; // wrap된 데이터: 끝까지 연속

	return &m_vRecvbuffer[m_ullReadPos];
}

bool RingRecvBuffer::PeekCopy(char* dest, ullong bytes)
{
	// 지정한 길이만큼 안전하게 복사 (readPos 이동 X)
	if (GetUseSize() < bytes)
		return false;

	ullong ullFirstChunk = min(bytes, m_ullCapacity - m_ullReadPos);
	memcpy(dest, &m_vRecvbuffer[m_ullReadPos], ullFirstChunk);

	ullong ullRemain = bytes - ullFirstChunk;
	if (ullRemain > 0)
		memcpy(dest + ullFirstChunk, &m_vRecvbuffer[0], ullRemain);

	return true;
}

bool RingRecvBuffer::Read(char* dest, ullong bytes)
{
	// 패킷 복사 (readPos 이동 O)
	if (!PeekCopy(dest, bytes))
		return false;

	CommitRead(bytes);
	return true;
}

void RingRecvBuffer::Consume(ullong bytes)
{
	bytes = min(bytes, GetUseSize());
	m_ullReadPos = (m_ullReadPos + bytes) % m_ullCapacity;
}

void RingRecvBuffer::DebugPrint() const
{
	ullong usedSize = (m_ullWritePos >= m_ullReadPos)
		? (m_ullWritePos - m_ullReadPos)
		: (m_ullCapacity - (m_ullReadPos - m_ullWritePos));

	ullong freeSize = GetFreeSize();

	std::cout << "[RingBuffer] "
		<< "Used=" << usedSize
		<< ", Free=" << freeSize
		<< ", ReadPos=" << m_ullReadPos
		<< ", WritePos=" << m_ullWritePos
		<< std::endl;
}

ullong RingRecvBuffer::GetFreeSize() const
{
	return m_ullCapacity - GetUseSize() - 1;
}
