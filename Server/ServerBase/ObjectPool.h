#pragma once
#include <memory>
#include <cassert>

template<typename T>
class ObjectPool
{
	constexpr uint64 PAGE_SIZE = 4096; // OS 기본 페이지 크기
	constexpr uint64 OBJECT_SIZE = sizeof(T); // 풀링할 객체의 크기
	constexpr uint64 OBJECTS_PER_PAGE = PAGE_SIZE / OBJECT_SIZE;

	struct PoolPage
	{
		PoolPage()
		{
			rawMemory = static_cast<char*>(::VirtualAlloc(nullptr, PAGE_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
			assert(rawMemory && "VirtualAlloc failed");

			for (uint32 i = 0; i < OBJECTS_PER_PAGE; ++i)
			{
				T* obj = reinterpret_cast<T*>(rawMemory + i * OBJECT_SIZE);
				objects.push_back(obj);
			}
		}

		~PoolPage()
		{
			::VirtualFree(rawMemory, 0, MEM_RELEASE);
		}

		vector<T*> objects;
		char* rawMemory = nullptr;
	};


public:
	ObjectPool()
	{
		static_assert(std::is_default_constructible<T>::value, "T must be default constructible");
	}

	~ObjectPool() = default; // unique_ptr이 알아서 PoolPage 소멸자 호출
	
	T* Acquire()
	{
		lock_guard<mutex> lock(m_mutex);

		if (m_freeList.empty())
			AllocatePage();

		T* obj = m_freeList.back();
		m_freeList.pop_back();
		return new (obj) T(); // placement new 
	}

	void Release(T* obj)
	{
		lock_guard<mutex> lock(m_mutex);
		obj->~T(); // 명시적 소멸자 호출
		m_freeList.push_back(obj);
	}


private:
	void AllocatePage()
	{
		auto* page = make_unique<PoolPage>();
		m_pages.push_back(page);
		for (T* obj : page->objects)
		{
			m_freeList.push_back(obj);
		}
	}

private:
	mutex m_mutex;
	vector<T*> m_freeList;
	vector<unique_ptr<PoolPage>> m_pages;
};

