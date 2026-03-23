#pragma once
#include <string_view>
class OutputMemoryStream
{
public:
	OutputMemoryStream() = default;
	~OutputMemoryStream() = default;
	OutputMemoryStream(const OutputMemoryStream&) = delete;
	OutputMemoryStream& operator=(const OutputMemoryStream&) = delete;


public:
	// -------------------------
	// 인터페이스 Write
	// -------------------------
	template<typename... Args>
	void Serialize(const Args&... args)
	{
		(WriteDispatch(args), ...);
	}

	const vector<uint8>& GetBuffer() const { return m_Buffer; }

private:
	// -------------------------
	// 기본 타입 Write
	// -------------------------
	template<typename T, typename = std::enable_if_t<std::is_trivially_copyable_v<T>>>
	void WriteOne(const T& value)
	{
		// static_assert(std::is_trivially_copyable_v<T>, "Write type Error!");
		// assert(std::is_trivially_copyable_v<T>);
		const size_t size = sizeof(T);
		const size_t oldSize = m_Buffer.size();

		m_Buffer.resize(oldSize + size);
		
		// 엔디안 변환
		T temp = ToNetworkEndian(value);
		
		std::memcpy(m_Buffer.data() +oldSize, &temp, size);
	}

	// 문자열 Write
	void Write(std::string_view value)
	{
		cout << "size : " << value.size() << "\tpivot size : " << std::numeric_limits<uint16_t>::max() << endl;
		assert(value.size() <= std::numeric_limits<uint16_t>::max());	// Debug용
		if (value.size() >= std::numeric_limits<uint16_t>::max())		// Release용
		{
			throw std::length_error("string_view length over uint16_Maximum");
		}

		uint16_t len = static_cast<uint16_t>(value.size());
		WriteOne(len); // 기존 템플릿 재사용

		const size_t oldSize = m_Buffer.size();
		m_Buffer.resize(oldSize + len);

		std::memcpy(m_Buffer.data() + oldSize, value.data(), len);
	}
	
	template<typename T>
	T ToNetworkEndian(const T& value)
	{

		// float/doule : endian 변환 안 함
		T temp = value;

		if constexpr (std::is_enum_v<T>)
		{
			using U = std::underlying_type_t<T>;
			return static_cast<T>(ToNetworkEndian(static_cast<U>(value)));
		}
		else if constexpr (std::is_integral_v<T> && sizeof(T) == 2)
		{
			temp = static_cast<T>(htons(static_cast<uint16>(value)));
		}
		else if constexpr (std::is_integral_v<T> && sizeof(T) == 4)
		{
			temp = static_cast<T>(htonl(static_cast<uint32>(value)));
		}
		else if constexpr (std::is_integral_v<T> && sizeof(T) == 8)
		{
			uint64 u = static_cast<uint64>(value);
			#if defined(_WIN32)
			u = _byteswap_uint64(u);
			#else 
			u = htobe64(u);
			#endif
			 
			temp = static_cast<T>(u);
		}

		return temp;
	}

	// template<typename T, typename = std::enable_if_t<std::is_enum_v<T>>>
	template<typename T, typename = std::enable_if_t<std::is_trivially_copyable_v<T>>>
	void WriteDispatch(const T& v)
	{
		WriteOne(v);
	}

	void WriteDispatch(std::string_view sv)
	{
		Write(sv);
	}

private:
	vector<uint8> m_Buffer;
};

class InputMemoryStream
{
public:
	InputMemoryStream(const uint8* data, ullong size)
		: m_Data(data), m_size(size), m_offset(0) {}

	~InputMemoryStream() = default;
	InputMemoryStream(const InputMemoryStream&) = delete;
	InputMemoryStream& operator=(const InputMemoryStream&) = delete;

private:
	// 위치 관련
	ullong Offset() const { return m_offset; }
	ullong Size() const { return m_size; }
	ullong Remaining() const { return (m_offset <= m_size) ? (m_size - m_offset) : 0; }

	bool CanRead(ullong n) const { return Remaining() >= n; }

public:
	// -------------------------
	// 인터페이스 Read
	// -------------------------
	template<typename... Args>
	bool DeSerialize(Args&... args)
	{
		// 하나라도 실패하면 false
		return (ReadDispatch(args) && ...);
	}

private:
	template<typename T, typename = std::enable_if_t<std::is_trivially_copyable_v<T>>>
	bool ReadOne(T& value)
	{
		if (!CanRead(sizeof(T)))
			return false;

		memcpy(&value, m_Data + m_offset, sizeof(T));
		m_offset += sizeof(T);

		// 엔디안 역변환
		value = FromNetworkEndian(value);
		return true;
	}

	bool Read(std::string& value)
	{
		uint16 len = 0;
		if (!ReadOne(len))
			return false;
		if (!CanRead(len))
			return false;

		value.assign(reinterpret_cast<const char* const>(m_Data + m_offset), len);
		m_offset += len;
		return true;
	}

	template<typename T>
	T FromNetworkEndian(const T& value)
	{

		// float/doule : endian 변환 안 함
		T temp = value;

		if constexpr (std::is_enum_v<T>)
		{
			using U = std::underlying_type_t<T>;
			return static_cast<T>(FromNetworkEndian(static_cast<U>(value)));
		}
		else if constexpr (std::is_integral_v<T> && sizeof(T) == 2)
		{
			temp = static_cast<T>(ntohs(static_cast<uint16>(value)));
		}
		else if constexpr (std::is_integral_v<T> && sizeof(T) == 4)
		{
			temp = static_cast<T>(ntohl(static_cast<uint32>(value)));
		}
		else if constexpr (std::is_integral_v<T> && sizeof(T) == 8)
		{
			uint64 u = static_cast<uint64>(value);
#if defined(_WIN32)
			u = _byteswap_uint64(u);
#else 
			u = be64toh(u);
#endif

			temp = static_cast<T>(u);
		}

		return temp;
	}

	bool ReadDispatch(std::string& s)
	{
		return Read(s);
	}

	template<typename T, typename = std::enable_if_t<std::is_trivially_copyable_v<T>>>
	bool ReadDispatch(T& v)
	{
		return ReadOne(v);
	}

private:
	const uint8* m_Data = nullptr;
	ullong m_size = 0;
	ullong m_offset = 0;
};