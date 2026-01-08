#pragma once
// 해당 라이브러리를 참조하게 될 외부 서버에서 참조 할 pch
#include <WinSock2.h>  // WinSock2는 Windows.h보다 먼저!
#include <Windows.h>   // 그리고 나서 Windows.h
#include <mswsock.h>
#include <ws2tcpip.h>

#pragma comment(lib, "ws2_32.lib") // 반드시 포함 ? WinSock2 관련 링커 필요

#include "Types.h"
#include "Defines.h"
#include "CoreGlobal.h"

#include <vector>
#include <list>
#include <queue>
#include <stack>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>
#include <mutex>


#include <thread>

#include <iostream>
using namespace std;

#include "TimeMaker.h"

void HelloWorld();