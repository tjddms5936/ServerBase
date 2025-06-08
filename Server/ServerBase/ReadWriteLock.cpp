#include "pch.h"
#include "ReadWriteLock.h"

void SpinReadWriteLock::LockRead()
{
	int32 i32Spin = 5000;
	while (true)
	{
		m_SpinLock.lock();

		// Writer가 없고, 대기 중인 Writer도 없을 때만 통과
		if (m_writing.load() == false && m_waitingWriters.load() == 0)
		{
			m_readerCount.fetch_add(1);
			m_SpinLock.unlock();
			return;
		}

		m_SpinLock.unlock();

		--i32Spin;
		if (i32Spin <= 0)
		{
			i32Spin = 5000;

			// CPU 낭비 막기, 부하 분산
			this_thread::sleep_for(50ms);
		}
		else
		{
			// CPU를 다른 쓰레드에 양보
			// 현재 스레드는 바로 다음 번 스케줄링에 다시 참가. latency 최소화
			this_thread::yield();
		}
	}
}

void SpinReadWriteLock::UnLockRead()
{
	m_SpinLock.lock();
	m_readerCount.fetch_sub(1);
	m_SpinLock.unlock();
}

void SpinReadWriteLock::LockWrite()
{
	int32 i32Spin = 5000;
	m_waitingWriters.fetch_add(1);
	while (true)
	{
		m_SpinLock.lock();
		if (m_writing.load() == false && m_readerCount.load() == 0)
		{
			m_writing.store(true);
			m_waitingWriters.fetch_sub(1);
			m_SpinLock.unlock();
			return;
		}

		m_SpinLock.unlock();

		--i32Spin;
		if (i32Spin <= 0)
		{
			i32Spin = 5000;
			this_thread::sleep_for(50ms);
		}
		else
		{
			this_thread::yield();
		}
	}
}

void SpinReadWriteLock::UnLockWrite()
{
	m_SpinLock.lock();
	m_writing.store(false);
	m_SpinLock.unlock();
}

bool SpinReadWriteLock::TryLockRead()
{
	m_SpinLock.lock();
	if (m_writing.load() == false && m_waitingWriters.load() == 0)
	{
		m_readerCount.fetch_add(1);
		m_SpinLock.unlock();
		return true;
	}

	m_SpinLock.unlock();
	return false;
}

bool SpinReadWriteLock::TryLockWrite()
{
	m_SpinLock.lock();
	if (m_writing.load() == false && m_readerCount.load() == 0)
	{
		m_writing.store(true);
		m_SpinLock.unlock();
		return true;
	}

	m_SpinLock.unlock();
	return false;
}

void CVReadWriteLock::LockRead()
{
	unique_lock<mutex> lock(m_mutex);
	m_readCV.wait(lock, [&]() {return m_writerCount.load() == 0; });
	m_readerCount.fetch_add(1);
}

void CVReadWriteLock::UnLockRead()
{
	unique_lock<mutex> lock(m_mutex);
	m_readerCount.fetch_sub(1);
	if (m_readerCount.load() == 0)
	{
		// 대기 중인 writer 하나 깨우기
		m_writeCV.notify_one();
	}
}

void CVReadWriteLock::LockWrite()
{
	unique_lock<mutex> lock(m_mutex);
	m_writerCount.fetch_add(1);
	m_writeCV.wait(lock, [&]() {return m_readerCount.load() == 0; });
	m_writing.store(true);
}

void CVReadWriteLock::UnLockWrite()
{
	unique_lock<mutex> lock(m_mutex);
	m_writing.store(false);
	m_writerCount.fetch_sub(1);
	if (m_writerCount.load() == 0)
		m_readCV.notify_all(); // reader 다 깨워주기
	else
		m_writeCV.notify_one(); // 다음 writer 하나만
}
