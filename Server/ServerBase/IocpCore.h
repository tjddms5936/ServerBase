#pragma once
#include <Windows.h>

class IocpCore
{
public:
	bool Initialize(); // I/O CP �ڵ� ����
	bool Register(HANDLE handle, ULONG_PTR key); // ���� �ڵ� ���
	// bool Dispatch(uint32_t tmeoutMs = INFINITE); // �̺�Ʈ ��� �� ó��

	
private:









public:
	// �⺻ ������ ����
	IocpCore() = default;
	~IocpCore() = default;
	IocpCore(const IocpCore&) = delete;
	IocpCore& operator=(const IocpCore&) = delete;
};

