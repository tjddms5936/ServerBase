#include "pch.h"
#include "IocpCore.h"
#include "Session.h"

IocpCore::IocpCore() :
    m_IocpHandle(INVALID_HANDLE_VALUE)
{

}

bool IocpCore::Initialize()
{
    m_IocpHandle = ::CreateIoCompletionPort(INVALID_HANDLE_VALUE, NULL, 0, 0);
    return (m_IocpHandle != INVALID_HANDLE_VALUE);
}

bool IocpCore::Register(HANDLE handle, ULONG_PTR key)
{
    // ��� ����
    /*
        SOCKET clientSocket = AcceptEx(...);
        Session* session = new Session(clientSocket);
        iocpCore->Register(clientSocket, reinterpret_cast<ULONG_PTR>(session));
    */
    HANDLE result = ::CreateIoCompletionPort(handle, m_IocpHandle, key, 0);
    if (result == nullptr)
    {
        DWORD error = ::GetLastError();
        std::cerr << "CreateIoCompletionPort failed. Error code: " << error << std::endl;
        return false;
    }
    return true;
    // return ::CreateIoCompletionPort(handle, m_IocpHandle, key, 0) != nullptr;
}

bool IocpCore::Dispatch(uint32_t timeoutMs)
{
    DWORD dwTransferred = 0;
    ULONG_PTR completionkey = 0;
    IocpEvent* pEvent = nullptr;

    // completionkey : � Session ��ü���� Ư�� ����
    // overlapped : ���� �۾�(Recv/Send)������ Ư�� ����
    BOOL result = ::GetQueuedCompletionStatus(
        m_IocpHandle,
        &dwTransferred,
        &completionkey,
        reinterpret_cast<LPOVERLAPPED*>(&pEvent),
        timeoutMs);

    if (pEvent == nullptr)
        return false;

    // ���⼭ completionkey �Ǵ� overlapped�� �̿��� �̺�Ʈ ó��
    // ex) static_cast<Session*>(completionkey)->OnRecv();
   //  IocpEvent* event = reinterpret_cast<IocpEvent*>(overlapped);
   //  Session* session = pEvent->GetOwner();
    shared_ptr<IocpObject> pOwner = pEvent->GetOwner();
    
    // Accept��°� �����ʷκ��� ���� ���� �޾Ҵٴ°�.
    if (pEvent->GetType() != IocpEvent::Type::Accept
        && (!result || dwTransferred == 0))
    {
        // ���� ��Ȳ: Ŭ���̾�Ʈ�� ���������� �����߰ų�, I/O ����
        std::cout << "[IOCP] Ŭ���̾�Ʈ ���� ���� ������\n";

        delete pEvent;
        return false;
    }

    pOwner->Dispatch(pEvent, dwTransferred);
    return true;
}

HANDLE IocpCore::GetHandle() const
{
    return m_IocpHandle;
}

void IocpEvent::Init()
{
    OVERLAPPED::hEvent = 0;
    OVERLAPPED::Internal = 0;
    OVERLAPPED::InternalHigh = 0;
    OVERLAPPED::Offset = 0;
    OVERLAPPED::OffsetHigh = 0;
}
