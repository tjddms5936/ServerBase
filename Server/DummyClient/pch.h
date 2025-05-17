#pragma once

#define WIN32_LEAN_ADD_MEAN		// 거의 사용되지 않는 내용을 Windows 헤더에서 제외합니다.

// ----------------------------------------------

// 전처리기 단계에서 실행
#ifdef _DEBUG
#pragma comment(lib, "Debug\\ServerBase.lib")
#else 
#pragma comment(lib, "Release\\ServerBase.lib")
#endif 

// ----------------------------------------------

#include "ServerPch.h"