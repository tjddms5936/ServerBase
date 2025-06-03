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

	~IocpEvent() = default; // ���� ����� �������... �޸� �������� OVERLAPPED �տ� �����Լ� ���̺��� �پ������ ������ �Ͼ
	IocpEvent(const IocpEvent&) = delete;
	IocpEvent& operator=(const IocpEvent&) = delete;

public:
	// �������̽�
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
	// �������̽�
	bool Initialize(); // I/O CP �ڵ� ����
	bool Register(HANDLE handle, ULONG_PTR key); // ���� �ڵ� ���
	bool Dispatch(uint32_t timeoutMs = INFINITE); // �̺�Ʈ ��� �� ó��

	HANDLE GetHandle() const;
	
private:
	HANDLE m_IocpHandle;
};

