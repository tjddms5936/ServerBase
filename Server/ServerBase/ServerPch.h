#pragma once
// �ش� ���̺귯���� �����ϰ� �� �ܺ� �������� ���� �� pch
#include <WinSock2.h>  // WinSock2�� Windows.h���� ����!
#include <Windows.h>   // �׸��� ���� Windows.h
#include <mswsock.h>
#include <ws2tcpip.h>

#pragma comment(lib, "ws2_32.lib") // �ݵ�� ���� ? WinSock2 ���� ��Ŀ �ʿ�

#include "Types.h"
#include "Defines.h"

#include <vector>
#include <list>
#include <queue>
#include <stack>
#include <map>
#include <set>
#include <unordered_map>
#include <unordered_set>


#include <thread>

#include <iostream>
using namespace std;

void HelloWorld();