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
	// �������̽�
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
	// �������̽�
	bool Initialize(); // I/O CP �ڵ� ����
	bool Register(HANDLE handle, ULONG_PTR key); // ���� �ڵ� ���
	bool Dispatch(uint32_t timeoutMs = INFINITE); // �̺�Ʈ ��� �� ó��

	HANDLE GetHandle() const;
	
private:
	HANDLE m_IocpHandle;
};

