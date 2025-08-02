#pragma once
/* ----------------------------
		RingRecvBuffer
- 하나의 큰 버퍼를 원형으로 사용
- 읽기/쓰기 포인터가 독립적
- memcpy 최소화 (앞으로 당길 필요 없음)

장점: 고성능, 대용량 지속 수신에 최적
단점: 포인터 wrap-around 처리 필요, 코드 복잡
-------------------------------*/

class RingRecvBuffer
{
public:
	RingRecvBuffer(ullong bufferSize = 8192);

    // WSABUF 두 개를 만들어 Scatter Recv에 사용
    void GetRecvWsaBuf(WSABUF outBufs[2]);

    // WSARecv 완료 후 실제 수신된 바이트 수를 Commit
    void CommitWrite(ullong bytes);

    // --- Read 포인터 이동 ---
    void CommitRead(ullong bytes);

    // 현재 읽을 수 있는 데이터 크기 (사용된 공간)
    ullong GetUseSize() const;

    // 남은 공간 크기 (쓸 수 있는 공간)
    ullong GetFreeSize() const;

    // 데이터 미리 보기 (연속 구간 최대 outLen)
    char* Peek(ullong& outLen);

    // --- PeekCopy: 지정한 길이만큼 안전하게 복사 (readPos 이동 X) ---
    bool PeekCopy(char* dest, ullong bytes);

    // 실제 데이터를 복사하고 readPos 이동
    bool Read(char* dest, ullong bytes);

    // 단순히 읽은 만큼 readPos 이동 (패킷 스킵용)
    void Consume(ullong bytes);

    void DebugPrint() const;
private:
	vector<char> m_vRecvbuffer;
	ullong m_ullCapacity;
	ullong m_ullReadPos;	// 다음 데이터를 읽을 위치. OnRead(numOfBytes)가 호출되면 앞으로 이동
	ullong m_ullWritePos;	// 다음 데이터를 쓸 위치(0 ~ capacity-1). OnWrite(numOfBytes)가 호출되면 앞으로 이동

	/*----------------------------------
			RingBuffer의 상태 표현 
		[읽은 데이터][읽을 데이터][쓸 수 있는 빈공간]
						^readPos      ^writePos
	-----------------------------------*/
};

/*
    이 버전의 장점
       
    PeekCopy를 통해 wrap-around 상태에서도 안전하게 헤더 확인 가능
        uint16_t packetSize;
        m_recvRingBuffer.PeekCopy((char*)&packetSize, sizeof(packetSize));
            → 데이터 부족하면 false 반환

    Read는 내부적으로 PeekCopy + CommitRead를 합쳐서 구현
        → 불필요한 memcpy 중복 제거

    CommitRead / CommitWrite 분리
        → IOCP 환경에서 Scatter-Gather Recv와 패킷 파싱 동시 처리에 유리
*/