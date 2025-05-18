#include "pch.h"
#include "IocpCore.h"

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
    return ::CreateIoCompletionPort(handle, m_IocpHandle, key, 0) != nullptr;
}

bool IocpCore::Dispatch(uint32_t timeoutMs)
{
    DWORD dwTestTransferred = 0;
    ULONG_PTR completionkey = 0;
    OVERLAPPED* overlapped = nullptr;

    // completionkey : � Session ��ü���� Ư�� ����
    // overlapped : ���� �۾�(Recv/Send)������ Ư�� ����
    BOOL result = ::GetQueuedCompletionStatus(
        m_IocpHandle,
        &dwTestTransferred,
        &completionkey,
        &overlapped,
        timeoutMs);

    if (result == FALSE || overlapped == nullptr)
        return false;

    // ���⼭ completionkey �Ǵ� overlapped�� �̿��� �̺�Ʈ ó��
    // ex) static_cast<Session*>(completionkey)->OnRecv();
    auto* event = reinterpret_cast<IocpEvent*>(overlapped);
    switch (event->GetType())
    {
    case IocpEvent::Type::Recv:
    {
        // TODO
    } 
    break;
    case IocpEvent::Type::Send:
    {
        // TODO
    }
    break;
    case IocpEvent::Type::Accept:
    {
        // TODO
    }
    break;
    default:
        break;
    }

    // TODO : ������Ʈ Ǯ Ȱ���ؼ� �޸� �Ҵ�/���� ����ȭ �ʿ�
    delete event;

    return true;
}

HANDLE IocpCore::GetHandle() const
{
    return m_IocpHandle;
}
