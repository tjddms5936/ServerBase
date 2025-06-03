#pragma once

class Session;
class IocpEvent;

class IocpObject : public enable_shared_from_this<IocpObject>
{
public:
	virtual HANDLE GetHandle() abstract;
	virtual void Dispatch(IocpEvent* pIocpEvent, int32 numOfBytes = 0) abstract;
};

class IocpEvent : public OVERLAPPED
{
public:
	enum class Type {Recv, Send, Accept};

	IocpEvent(Type type, shared_ptr<IocpObject> owner, shared_ptr<Session> pPartsSession = nullptr) :
		m_Type(type), m_Owner(owner), m_PartsSession(pPartsSession) {}

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
	Type m_Type;
	shared_ptr<IocpObject> m_Owner;
	shared_ptr<Session> m_PartsSession;
};

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

	HANDLE GetHandle() const;
	
private:
	HANDLE m_IocpHandle;
};

