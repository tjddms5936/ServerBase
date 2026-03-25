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
    // 사용 예시
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

    if (!Dequeue(dwTransferred, completionkey, pEvent, timeoutMs))
        return false;

    shared_ptr<IocpObject> pOwner = pEvent->GetOwner();
    pOwner->Dispatch(pEvent, dwTransferred);
    return true;
}

bool IocpCore::Dequeue(DWORD& _dwTransferred, ULONG_PTR& completionkey, IocpEvent*& pEvent, uint32 timeoutMs)
{
    _dwTransferred = 0;
    completionkey = 0;
    pEvent = nullptr;

    // completionkey : 어떤 Session 객체인지 특정 가능
    // overlapped : 무슨 작업(Recv/Send)였는지 특정 가능
    BOOL result = ::GetQueuedCompletionStatus(
        m_IocpHandle,
        &_dwTransferred,
        &completionkey,
        reinterpret_cast<LPOVERLAPPED*>(&pEvent),
        timeoutMs);

    DWORD completionError = (result == TRUE) ? 0 : ::GetLastError();

    if (pEvent == nullptr)
        return false;

    pEvent->m_stIoData.bCompletionSuccess = (result == TRUE);
    pEvent->m_stIoData.dwCompletionError = completionError;
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

void IocpEvent::ResetOverlapped()
{
    Init();
    m_stIoData.bCompletionSuccess = true;
    m_stIoData.dwCompletionError = 0;
}
