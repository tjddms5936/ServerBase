#include "pch.h"
#include "RecvBuffer.h"

RingRecvBuffer::RingRecvBuffer(ullong bufferSize) :
	m_vRecvbuffer(bufferSize), m_ullCapacity(bufferSize), m_ullReadPos(0), m_ullWritePos(0)
{

}

void RingRecvBuffer::GetRecvWsaBuf(WSABUF outBufs[2])
{
	ullong ullFreeSpace = GetFreeSize();

	// RingBuffer�� �� ���� �� �̻� ���� �����͸� �� ������ ���� ���
	if (ullFreeSpace == 0)
	{
		// ���� �÷ο� ����
		/*
			- �� ���¿��� �� WSARecv�� �ɾ������, OS�� �����͸� ���ۿ� ������ �õ���
			- �����δ� �� ������ �����Ƿ� ���� �����Ͱ� �б� �����͸� ���������(Overrun) ������ �߻�
			
			WSARecv()�� ��ȿ���� ���� ���� ���� 0�� �Ѱ��ָ�
				- OS�� �� ���Ͽ� ���� Recv�� �������� �ʰ� ��� WSA_IO_PENDING �� 0����Ʈ �̺�Ʈ �߻�
				- �Ǵ� ���ǿ� ���� �ƿ� IO�� �ɸ��� ���� 

			��, �� ���۷� Recv�� �ɾ��ָ鼭 ���۸� ����� �ʵ��� ����

			����:
				free space�� 0�̸�, �� �̻� ���� ������ �����Ƿ�
				nullptr ���۸� �༭ OS�� ������ ���⸦ �õ����� �ʰ� ���� ���� �� �����÷ο� ����.
		*/

		outBufs[0].buf = nullptr;
		outBufs[0].len = 0;

		outBufs[1].buf = nullptr;
		outBufs[1].len = 0;
		return;
	}

	if (m_ullWritePos >= m_ullReadPos)
	{
		// case 1 : ���� ���� ���� + wrap ����.   writePos~end, 0~readPos
		/*----------------------------------
				RingBuffer�� ���� ǥ��

			0                capacity-1
			[����][������][������][�������][�������]
					^readPos        ^writePos


			- ������� writePos~������ ����, �� �� 0~readPos���� wrap ����
				- outBufs[0] �� writePos���� ������ (���� ����)
				- outBufs[1] �� 0���� readPos �������� (wrap-around ����)
		-----------------------------------*/
		ullong ullTailSpace = m_ullCapacity - m_ullWritePos;
		outBufs[0].buf = &m_vRecvbuffer[m_ullWritePos];
		outBufs[0].len = static_cast<ULONG>(ullTailSpace);

		// wrap ���� 
		outBufs[1].buf = &m_vRecvbuffer[0];
		outBufs[1].len = static_cast<ULONG>(m_ullReadPos);
	}
	else
	{
		// case 2 : ���� ���� �� �����.	writePos~readPos-1
		/*----------------------------------
				RingBuffer�� ���� ǥ��

			0                capacity-1
			[�������][������][������][������]
			^writePos       ^readPos

			- ������� writePos~readPos-1���� ���� �� ���
				- outBufs[0] �� writePos~readPos-1
				- outBufs[1] �� �ʿ� ���� (nullptr)
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
	// ���� �Ϸ� �� numOfBytes��ŭ ���� ������ �̵�
	m_ullWritePos = (m_ullWritePos + bytes) % m_ullCapacity;
}

void RingRecvBuffer::CommitRead(ullong bytes)
{
	m_ullReadPos = (m_ullReadPos + bytes) % m_ullCapacity;
}

ullong RingRecvBuffer::GetUseSize() const
{
	// ���� �� �ִ� ������ ũ��
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
		outLen = m_ullWritePos - m_ullReadPos; // ���� ������
	else
		outLen = m_ullCapacity - m_ullReadPos; // wrap�� ������: ������ ����

	return &m_vRecvbuffer[m_ullReadPos];
}

bool RingRecvBuffer::PeekCopy(char* dest, ullong bytes)
{
	// ������ ���̸�ŭ �����ϰ� ���� (readPos �̵� X)
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
	// ��Ŷ ���� (readPos �̵� O)
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
