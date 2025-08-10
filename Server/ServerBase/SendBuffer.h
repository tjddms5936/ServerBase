#pragma once

/*-------------------------------------------------------------------------------------------------
	기존 문제
		- PostSend()에서 std::vector<char>를 매번 새로 할당/복사 → 힙 단편화·캐시 비효율.
		
		- 송신 버퍼(스택/임시 vector)의 수명 보장 실패 위험 → WSASend 완료 전에 해제되면 크래시.
		
		- 헤더+페이로드를 합치기 위해 memcpy로 합본 버퍼를 또 만들었음 → 불필요한 복사.
		
		- 다중 송신 시 WSASend 동시중첩 관리와 큐잉/역압(backpressure) 부재 → 꼬임/폭주 위험.
	
	개선 목표
		- Zero(또는 Near-zero) Copy: 헤더/페이로드를 합치지 않고 WSABUF[2]로 보냄.
		
		- 수명 안전성: 송신 버퍼를 참조 카운팅(공유 소유) 으로 완료까지 생존 보장.
		
		- 할당 최소화: SendBufferPool 로 재사용(슬랩/블록), 잦은 new/delete 제거.
		
		- 송신 큐/역압: 1개만 인플라이트, 나머지는 큐에 적재. 세션별 큐 용량 한도.
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
	- 고정 블록 풀(슬랩)에서 조각을 빌려 쓰는 SendBuffer
	- 내부는 shared_ptr<Slab> + 오프셋/길이만 갖고, 반납은 참조 해제로 해결
	- 작은 헤더(2~32B) 용 미니 블록, 일반 페이로드 용 표준 블록 두 라인으로도 확장 가능
----------------------*/
class SendBuffer
{
public:
	SendBuffer(shared_ptr<stSlab> _slab, ullong _offset, ullong _len);
	~SendBuffer() = default;
	SendBuffer(const SendBuffer&) = delete;
	SendBuffer& operator =(const SendBuffer&) = delete;

	// 인터페이스
	char* data() const { return  m_Slab->mem.get() + m_ullOffset; };
	ullong size() const { return  m_ullLen; };
	ullong capacity() const { return m_Slab->cap - m_ullOffset; };

	// 작성용
	char* writable() const { return m_Slab->mem.get() + m_ullOffset; }
	void commit(ullong n) { m_ullLen = n; }

private:
	shared_ptr<stSlab> m_Slab;
	ullong m_ullOffset;
	ullong m_ullLen;
};

/*-----------------------------------------
	- 간단한 풀 (multi-slab & free list 로 개선 가능)
	- 송신 버퍼를 풀에서 재사용하여 할당/해제 오버헤드 절감, GC-like 스톨 제거
-----------------------------------------*/
class SendBufferPool
{
public:
	SendBufferPool(ullong _slabSize = 64 * 1024);
	~SendBufferPool() = default;
	SendBufferPool(const SendBufferPool&) = delete;
	SendBufferPool& operator =(const SendBufferPool&) = delete;

	// 인터페이스
	
	// 단순: slab 전부를 하나의 버퍼로 빌려줌(필요시 더 쪼개는 버전으로 확장 가능)
	shared_ptr<SendBuffer> alloc(ullong need);

private:
	ullong m_ullSlabSize;
	shared_ptr<stSlab> m_CurSlab;
	ullong m_ullCurUsed = 0;
};