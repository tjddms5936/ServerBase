#pragma once
#include <condition_variable>
#include <chrono>

/*------------------
	SpinLock
------------------*/
class SpinLock
{
public:
	SpinLock() = default;
	~SpinLock() = default;
	SpinLock(const SpinLock& c) = delete;
	SpinLock& operator=(const SpinLock&) = delete;

	void lock()
	{
		bool expected = false;
		bool desired = true;

		int count = 5000;

		// backoff 전략 - 실패 횟수가 많아질수록 현재 스레드의 실행을 지정한 시간 동안 일시 중단(suspend)하고, CPU를 반납하여 다시 스케쥴링 대상이 되도록 함
		while (m_bLocked.compare_exchange_strong(expected, desired) == false)
		{
			expected = false;
			--count;
			if (count <= 0)
			{
				count = 5000;
				static thread_local int sleepTimsMs = 1;
				this_thread::sleep_for(chrono::milliseconds(sleepTimsMs));
				sleepTimsMs = min(sleepTimsMs * 2, 100); // 최대 100ms
			}
		}
	}

	void unlock()
	{
		m_bLocked.store(false);
	}

private:
	atomic<bool> m_bLocked = false;
};


/*-----------------------
	ReadWriteLockBase
-----------------------*/
class ReadWriteLockBase
{
public:
	ReadWriteLockBase()
		: m_readerCount(0), m_writing(false) {}

	virtual ~ReadWriteLockBase() {};

	virtual void LockRead() = 0;
	virtual void UnLockRead() = 0;
	virtual void LockWrite() = 0;
	virtual void UnLockWrite() = 0;

protected:
	atomic<int32> m_readerCount;
	atomic<bool> m_writing;
};

/*-----------------------
	SpinReadWriteLock
	--> Game서버, 실시간 시스템에 활용 
	사용 이유 : 락 길이가 짧고 대기 시간이 작으면 context switch가 오히려 더 느림
-----------------------*/
class SpinReadWriteLock : public ReadWriteLockBase
{
public:
	SpinReadWriteLock()
		: m_waitingWriters(0) {}

	~SpinReadWriteLock() = default;

	virtual void LockRead() override;
	virtual void UnLockRead() override;
	virtual void LockWrite() override;
	virtual void UnLockWrite() override;
	
	// Try 시리즈. 사용처는?
	// 1. 락을 못 잡아도 괜찮을 때
	// 2. 데이터 갱신 or 동기화 없이 계속 돌아가도 될 때
	bool TryLockRead();
	bool TryLockWrite();

private:
	SpinLock m_SpinLock;
	atomic<int32> m_waitingWriters;
};

/*-----------------------
	CVReadWriteLock
	--> 데이터베이스 접근, 로그 파일 기록, 대기 시간 많은 구조
	사용 이유 : 스레드 잠재우는 게 CPU를 아끼는 데 도움
-----------------------*/
class CVReadWriteLock : public ReadWriteLockBase
{
public:
	CVReadWriteLock()
		: m_writerCount(0) {}

	~CVReadWriteLock() = default;

	virtual void LockRead() override;
	virtual void UnLockRead() override;
	virtual void LockWrite() override;
	virtual void UnLockWrite() override;

private:
	mutex  m_mutex;
	std::condition_variable m_readCV;
	std::condition_variable m_writeCV;
	atomic<int32> m_writerCount;
};



/*-----------------------
	SpinRWLockWithStats 
	--> 스핀RW락 성능 통계용
-----------------------*/
class SpinRWLockWithStats : public SpinReadWriteLock
{
public:
	// 통계용 카운터
	std::atomic<int> readTryCount{ 0 };
	std::atomic<int> readFailCount{ 0 };
	std::atomic<int> writeTryCount{ 0 };
	std::atomic<int> writeFailCount{ 0 };

	bool TryLockReadStat()
	{
		++readTryCount;
		if (TryLockRead())
			return true;

		++readFailCount;
		return false;
	}

	bool TryLockWriteStat()
	{
		++writeTryCount;
		if (TryLockWrite())
			return true;

		++writeFailCount;
		return false;
	}
};