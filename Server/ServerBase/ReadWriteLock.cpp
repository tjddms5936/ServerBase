#include "pch.h"
#include "ReadWriteLock.h"

void SpinReadWriteLock::LockRead()
{
	int32 i32Spin = 5000;
	while (true)
	{
		m_SpinLock.lock();

		// Writer�� ����, ��� ���� Writer�� ���� ���� ���
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

			// CPU ���� ����, ���� �л�
			this_thread::sleep_for(50ms);
		}
		else
		{
			// CPU�� �ٸ� �����忡 �纸
			// ���� ������� �ٷ� ���� �� �����ٸ��� �ٽ� ����. latency �ּ�ȭ
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
		// ��� ���� writer �ϳ� �����
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
		m_readCV.notify_all(); // reader �� �����ֱ�
	else
		m_writeCV.notify_one(); // ���� writer �ϳ���
}
