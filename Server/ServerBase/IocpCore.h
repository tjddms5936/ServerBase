#pragma once
#include <Windows.h>

class IocpCore
{
public:
	bool Initialize(); // I/O CP 핸들 생성
	bool Register(HANDLE handle, ULONG_PTR key); // 소켓 핸들 등록
	// bool Dispatch(uint32_t tmeoutMs = INFINITE); // 이벤트 대기 및 처리

	
private:









public:
	// 기본 프레임 설정
	IocpCore() = default;
	~IocpCore() = default;
	IocpCore(const IocpCore&) = delete;
	IocpCore& operator=(const IocpCore&) = delete;
};

