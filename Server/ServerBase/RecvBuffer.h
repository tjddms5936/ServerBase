#pragma once
/* ----------------------------
		RingRecvBuffer
- �ϳ��� ū ���۸� �������� ���
- �б�/���� �����Ͱ� ������
- memcpy �ּ�ȭ (������ ��� �ʿ� ����)

����: ����, ��뷮 ���� ���ſ� ����
����: ������ wrap-around ó�� �ʿ�, �ڵ� ����
-------------------------------*/

class RingRecvBuffer
{
public:
	RingRecvBuffer(ullong bufferSize = 8192);

    // WSABUF �� ���� ����� Scatter Recv�� ���
    void GetRecvWsaBuf(WSABUF outBufs[2]);

    // WSARecv �Ϸ� �� ���� ���ŵ� ����Ʈ ���� Commit
    void CommitWrite(ullong bytes);

    // --- Read ������ �̵� ---
    void CommitRead(ullong bytes);

    // ���� ���� �� �ִ� ������ ũ�� (���� ����)
    ullong GetUseSize() const;

    // ���� ���� ũ�� (�� �� �ִ� ����)
    ullong GetFreeSize() const;

    // ������ �̸� ���� (���� ���� �ִ� outLen)
    char* Peek(ullong& outLen);

    // --- PeekCopy: ������ ���̸�ŭ �����ϰ� ���� (readPos �̵� X) ---
    bool PeekCopy(char* dest, ullong bytes);

    // ���� �����͸� �����ϰ� readPos �̵�
    bool Read(char* dest, ullong bytes);

    // �ܼ��� ���� ��ŭ readPos �̵� (��Ŷ ��ŵ��)
    void Consume(ullong bytes);

    void DebugPrint() const;
private:
	vector<char> m_vRecvbuffer;
	ullong m_ullCapacity;
	ullong m_ullReadPos;	// ���� �����͸� ���� ��ġ. OnRead(numOfBytes)�� ȣ��Ǹ� ������ �̵�
	ullong m_ullWritePos;	// ���� �����͸� �� ��ġ(0 ~ capacity-1). OnWrite(numOfBytes)�� ȣ��Ǹ� ������ �̵�

	/*----------------------------------
			RingBuffer�� ���� ǥ�� 
		[���� ������][���� ������][�� �� �ִ� �����]
						^readPos      ^writePos
	-----------------------------------*/
};

/*
    �� ������ ����
       
    PeekCopy�� ���� wrap-around ���¿����� �����ϰ� ��� Ȯ�� ����
        uint16_t packetSize;
        m_recvRingBuffer.PeekCopy((char*)&packetSize, sizeof(packetSize));
            �� ������ �����ϸ� false ��ȯ

    Read�� ���������� PeekCopy + CommitRead�� ���ļ� ����
        �� ���ʿ��� memcpy �ߺ� ����

    CommitRead / CommitWrite �и�
        �� IOCP ȯ�濡�� Scatter-Gather Recv�� ��Ŷ �Ľ� ���� ó���� ����
*/