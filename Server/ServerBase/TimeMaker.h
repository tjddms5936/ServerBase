#pragma once
#include <chrono>
#include <string>

class TimeMaker
{
public:
	TimeMaker() = default;
	~TimeMaker() = default;
	TimeMaker(const TimeMaker&) = delete;
	TimeMaker& operator=(const TimeMaker&) = delete;

	static int64 GetTimeStamp_Sec();		// 초 단위 타임스탬프
	static int64 GetTimeStamp_MilSec();	// 밀리초 단위 타임스탬프
	static int64 GetTimeStamp_MicroSec();	// 마이크로초 단위 타임스탬프

	static std::chrono::steady_clock::time_point GetStartTP();
	static int64 ElapsedMs(std::chrono::steady_clock::time_point start);
};

extern TimeMaker g_TimeMaker;