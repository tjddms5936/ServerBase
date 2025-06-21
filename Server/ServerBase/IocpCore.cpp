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

    // completionkey : 어떤 Session 객체인지 특정 가능
    // overlapped : 무슨 작업(Recv/Send)였는지 특정 가능
    BOOL result = ::GetQueuedCompletionStatus(
        m_IocpHandle,
        &dwTransferred,
        &completionkey,
        reinterpret_cast<LPOVERLAPPED*>(&pEvent),
        timeoutMs);

    if (pEvent == nullptr)
        return false;

    // 여기서 completionkey 또는 overlapped를 이용해 이벤트 처리
    // ex) static_cast<Session*>(completionkey)->OnRecv();
   //  IocpEvent* event = reinterpret_cast<IocpEvent*>(overlapped);
   //  Session* session = pEvent->GetOwner();
    shared_ptr<IocpObject> pOwner = pEvent->GetOwner();
    
    // Accept라는건 리스너로부터 연결 통지 받았다는거.
    if (pEvent->GetType() != IocpEvent::Type::Accept
        && (!result || dwTransferred == 0))
    {
        // 예외 상황: 클라이언트가 정상적으로 종료했거나, I/O 실패
        std::cout << "[IOCP] 클라이언트 연결 종료 감지됨\n";

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
