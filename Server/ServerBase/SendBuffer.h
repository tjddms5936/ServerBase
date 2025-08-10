#pragma once

/*-------------------------------------------------------------------------------------------------
	���� ����
		- PostSend()���� std::vector<char>�� �Ź� ���� �Ҵ�/���� �� �� ����ȭ��ĳ�� ��ȿ��.
		
		- �۽� ����(����/�ӽ� vector)�� ���� ���� ���� ���� �� WSASend �Ϸ� ���� �����Ǹ� ũ����.
		
		- ���+���̷ε带 ��ġ�� ���� memcpy�� �պ� ���۸� �� ������� �� ���ʿ��� ����.
		
		- ���� �۽� �� WSASend ������ø ������ ť��/����(backpressure) ���� �� ����/���� ����.
	
	���� ��ǥ
		- Zero(�Ǵ� Near-zero) Copy: ���/���̷ε带 ��ġ�� �ʰ� WSABUF[2]�� ����.
		
		- ���� ������: �۽� ���۸� ���� ī����(���� ����) ���� �Ϸ���� ���� ����.
		
		- �Ҵ� �ּ�ȭ: SendBufferPool �� ����(����/���), ���� new/delete ����.
		
		- �۽� ť/����: 1���� ���ö���Ʈ, �������� ť�� ����. ���Ǻ� ť �뷮 �ѵ�.
-------------------------------------------------------------------------------------------------*/

struct stSlab
{
	stSlab(ullong c) :
		mem(new char[c]), cap(c)
	{}

	unique_ptr<char[]> mem;
	ullong cap;
};

/*----------------------
	- ���� ��� Ǯ(����)���� ������ ���� ���� SendBuffer
	- ���δ� shared_ptr<Slab> + ������/���̸� ����, �ݳ��� ���� ������ �ذ�
	- ���� ���(2~32B) �� �̴� ���, �Ϲ� ���̷ε� �� ǥ�� ��� �� �������ε� Ȯ�� ����
----------------------*/
class SendBuffer
{
public:
	SendBuffer(shared_ptr<stSlab> _slab, ullong _offset, ullong _len);
	~SendBuffer() = default;
	SendBuffer(const SendBuffer&) = delete;
	SendBuffer& operator =(const SendBuffer&) = delete;

	// �������̽�
	char* data() const { return  m_Slab->mem.get() + m_ullOffset; };
	ullong size() const { return  m_ullLen; };
	ullong capacity() const { return m_Slab->cap - m_ullOffset; };

	// �ۼ���
	char* writable() const { return m_Slab->mem.get() + m_ullOffset; }
	void commit(ullong n) { m_ullLen = n; }

private:
	shared_ptr<stSlab> m_Slab;
	ullong m_ullOffset;
	ullong m_ullLen;
};

/*-----------------------------------------
	- ������ Ǯ (multi-slab & free list �� ���� ����)
	- �۽� ���۸� Ǯ���� �����Ͽ� �Ҵ�/���� ������� ����, GC-like ���� ����
-----------------------------------------*/
class SendBufferPool
{
public:
	SendBufferPool(ullong _slabSize = 64 * 1024);
	~SendBufferPool() = default;
	SendBufferPool(const SendBufferPool&) = delete;
	SendBufferPool& operator =(const SendBufferPool&) = delete;

	// �������̽�
	
	// �ܼ�: slab ���θ� �ϳ��� ���۷� ������(�ʿ�� �� �ɰ��� �������� Ȯ�� ����)
	shared_ptr<SendBuffer> alloc(ullong need);

private:
	ullong m_ullSlabSize;
	shared_ptr<stSlab> m_CurSlab;
	ullong m_ullCurUsed = 0;
};