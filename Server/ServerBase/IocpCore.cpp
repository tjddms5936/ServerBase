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
    // 사용 예시
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

    // completionkey : 어떤 Session 객체인지 특정 가능
    // overlapped : 무슨 작업(Recv/Send)였는지 특정 가능
    BOOL result = ::GetQueuedCompletionStatus(
        m_IocpHandle,
        &dwTestTransferred,
        &completionkey,
        &overlapped,
        timeoutMs);

    if (result == FALSE || overlapped == nullptr)
        return false;

    // 여기서 completionkey 또는 overlapped를 이용해 이벤트 처리
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

    // TODO : 오브젝트 풀 활용해서 메모리 할당/해제 최적화 필요
    delete event;

    return true;
}

HANDLE IocpCore::GetHandle() const
{
    return m_IocpHandle;
}
