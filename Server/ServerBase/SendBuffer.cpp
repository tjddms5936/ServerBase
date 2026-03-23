#include "pch.h"
#include "SendBuffer.h"

SendBuffer::SendBuffer(shared_ptr<stSlab> _slab, ullong _offset, ullong _len) :
	m_Slab(move(_slab)), m_ullOffset(_offset), m_ullLen(_len)
{
}

SendBufferPool::SendBufferPool(ullong _slabSize) :
	m_ullSlabSize(_slabSize)
{
}

shared_ptr<SendBuffer> SendBufferPool::alloc(ullong need)
{
	// 필요 사이즈가 m_ullSlabSize보다 큰 경우 처리
	// 아얘 막을 것인가, 더 큰걸 줄 것인가는 선택.
	if (need > m_ullSlabSize)
	{
		return make_shared<SendBuffer>(make_shared<stSlab>(need), 0, need);
	}

	if (!m_CurSlab || need > (m_ullSlabSize - m_ullCurUsed))
	{
		m_CurSlab = make_shared<stSlab>(m_ullSlabSize);
		m_ullCurUsed = 0;
	}

	shared_ptr<SendBuffer> buf = make_shared<SendBuffer>(m_CurSlab, m_ullCurUsed, need);
	m_ullCurUsed += need; // 다음 오프셋
	return buf;
}
