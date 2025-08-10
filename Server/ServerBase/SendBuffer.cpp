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
	if (!m_CurSlab || m_ullCurUsed + need > m_ullSlabSize)
	{
		m_CurSlab = make_shared<stSlab>(m_ullSlabSize);
		m_ullCurUsed = 0;
	}

	shared_ptr<SendBuffer> buf = make_shared<SendBuffer>(m_CurSlab, m_ullCurUsed, need);
	m_ullCurUsed += need; // 다음 오프셋
	return buf;
}
