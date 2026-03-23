#include "pch.h"
#include "TimeMaker.h"

TimeMaker g_TimeMaker;


int64 TimeMaker::GetTimeStamp_Sec()
{
    using namespace std::chrono;
    return duration_cast<seconds>(system_clock::now().time_since_epoch()).count();
}

int64 TimeMaker::GetTimeStamp_MilSec()
{
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

int64 TimeMaker::GetTimeStamp_MicroSec()
{
    using namespace std::chrono;
    return duration_cast<microseconds>(system_clock::now().time_since_epoch()).count();
}

std::chrono::steady_clock::time_point TimeMaker::GetStartTP()
{
    using Clock = std::chrono::steady_clock;
    return Clock::now();
}

int64 TimeMaker::ElapsedMs(std::chrono::steady_clock::time_point start)
{
    using namespace std::chrono;
    using Clock = steady_clock;
    return duration_cast<milliseconds>(Clock::now() - start).count();
}
