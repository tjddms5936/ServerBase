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

		// backoff ���� - ���� Ƚ���� ���������� ���� �������� ������ ������ �ð� ���� �Ͻ� �ߴ�(suspend)�ϰ�, CPU�� �ݳ��Ͽ� �ٽ� �����층 ����� �ǵ��� ��
		while (m_bLocked.compare_exchange_strong(expected, desired) == false)
		{
			expected = false;
			--count;
			if (count <= 0)
			{
				count = 5000;
				static thread_local int sleepTimsMs = 1;
				this_thread::sleep_for(chrono::milliseconds(sleepTimsMs));
				sleepTimsMs = min(sleepTimsMs * 2, 100); // �ִ� 100ms
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
	--> Game����, �ǽð� �ý��ۿ� Ȱ�� 
	��� ���� : �� ���̰� ª�� ��� �ð��� ������ context switch�� ������ �� ����
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
	
	// Try �ø���. ���ó��?
	// 1. ���� �� ��Ƶ� ������ ��
	// 2. ������ ���� or ����ȭ ���� ��� ���ư��� �� ��
	bool TryLockRead();
	bool TryLockWrite();

private:
	SpinLock m_SpinLock;
	atomic<int32> m_waitingWriters;
};

/*-----------------------
	CVReadWriteLock
	--> �����ͺ��̽� ����, �α� ���� ���, ��� �ð� ���� ����
	��� ���� : ������ ������ �� CPU�� �Ƴ��� �� ����
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
	--> ����RW�� ���� ����
-----------------------*/
class SpinRWLockWithStats : public SpinReadWriteLock
{
public:
	// ���� ī����
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