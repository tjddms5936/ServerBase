#pragma once

#define WIN32_LEAN_ADD_MEAN		// ���� ������ �ʴ� ������ Windows ������� �����մϴ�.

// ----------------------------------------------

// ��ó���� �ܰ迡�� ����
#ifdef _DEBUG
#pragma comment(lib, "Debug\\ServerBase.lib")
#else 
#pragma comment(lib, "Release\\ServerBase.lib")
#endif 

// ----------------------------------------------

#include "ServerPch.h"