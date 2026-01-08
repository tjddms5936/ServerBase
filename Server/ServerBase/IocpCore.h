#pragma once

class Session;
class IocpEvent;

struct stSendItem
{
	/*
		Gather I/O (WSABUF[2]) 적용
			- 헤더(2바이트) 는 작은 SendBuffer에 작성.
			
			- 페이로드는 이미 있는 메모리(예: 다른 풀 버퍼, 패킷 빌더 결과 등)를 가리킴.
			
			- 두 버퍼의 수명을 I/O 완료까지 보장하기 위해 이벤트 컨텍스트에 shared_ptr 보관
	*/
	WSABUF bufs[2];
	DWORD  bufCount = 0;

	// 메모리 생존 보장용 keeper
	shared_ptr<void> keep1;
	shared_ptr<void> keep2;

	// 부분 전송 완료 대응
	int32 i32TotalLen = 0;	// 총 보낼 길이
	int32 i32SentLen = 0;	// 커널에 복사 된 길이
};

struct stIoData
{
	// Listener에서 하는 Accept에서 Race Condition 방지를 위한 구조체

	void	SetIoThreadID(int32 _i32IoThreadID) { i32IoThreadID = _i32IoThreadID; };
	int32	GetIoThreadID() { return i32IoThreadID; };
	int32 i32IoThreadID = -1;	// -1:미할당 0:이상 그 외 : IO 스레드 ID 
	int32 i32RetryCount = 0;	// 재시도 횟수
};

/*--------------------
		IocpObject
--------------------*/
class IocpObject : public enable_shared_from_this<IocpObject>
{
public:
	virtual HANDLE GetHandle() abstract;
	virtual void Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes = 0) abstract;
};


/*--------------------
		IocpEvent
--------------------*/
class IocpEvent : public OVERLAPPED
{
public:
	enum class Type {Recv, Send, Accept};

	IocpEvent(Type type, shared_ptr<IocpObject> owner, shared_ptr<Session> pPartsSession = nullptr) :
		m_Type(type), m_Owner(owner), m_PartsSession(pPartsSession) 
	{
		Init();
	};

	~IocpEvent() = default; // 가상 상속을 써버리면... 메모리 구조에서 OVERLAPPED 앞에 가상함수 테이블이 붙어버려서 오동작 일어남
	IocpEvent(const IocpEvent&) = delete;
	IocpEvent& operator=(const IocpEvent&) = delete;

public:
	// 인터페이스
	Type GetType() const { return m_Type; }
	shared_ptr<IocpObject> GetOwner() const { return m_Owner; }
	shared_ptr<Session> GetPartsSession() const { return m_PartsSession; }
	void SetPartsSession(shared_ptr<Session> pSession) { m_PartsSession = pSession; }
private:
	void Init();

private:
	Type					m_Type;
	shared_ptr<IocpObject>	m_Owner;
	shared_ptr<Session>		m_PartsSession;

public:
	// IocpEvent 확장: SendItem 포함
	stSendItem	m_stSendItem;	// ← 여기에 WSABUF들과 keeper가 붙는다
	stIoData	m_stIoData;		// Accept에서 Race Condition 방지용
};


/*--------------------
		IocpCore
--------------------*/
class IocpCore
{
public:
	IocpCore();
	~IocpCore() = default;
	IocpCore(const IocpCore&) = delete;
	IocpCore& operator=(const IocpCore&) = delete;


public:
	// 인터페이스
	bool Initialize(); // I/O CP 핸들 생성
	bool Register(HANDLE handle, ULONG_PTR key); // 소켓 핸들 등록
	bool Dispatch(uint32_t timeoutMs = INFINITE); // 이벤트 대기 및 처리
	bool Dequeue(DWORD& _dwTransferred, ULONG_PTR& completionkey, IocpEvent*& pEvent, uint32 timeoutMs = INFINITE);

	HANDLE GetHandle() const;
	
private:
	HANDLE m_IocpHandle;
};

