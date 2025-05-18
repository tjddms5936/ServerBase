#pragma once

class Session;
class IocpEvent : public OVERLAPPED
{
public:
	enum class Type {Recv, Send, Accept};

	IocpEvent(Type type, Session* owner) :
		m_Type(type), m_Owner(owner) {}

	virtual ~IocpEvent() {}
	IocpEvent(const IocpEvent&) = delete;
	IocpEvent& operator=(const IocpEvent&) = delete;

public:
	// 인터페이스
	Type GetType() const { return m_Type; }
	Session* GetOwner() const { return m_Owner; }

private:
	Type m_Type;
	Session* m_Owner;
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

