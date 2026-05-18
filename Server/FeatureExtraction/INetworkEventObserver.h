#pragma once

#include "NetworkEvent.h"

namespace feature_extraction
{
class INetworkEventObserver
{
public:
    virtual ~INetworkEventObserver() = default;

    // 세션은 이 훅을 통해 가벼운 네트워크 이벤트만 외부로 전달한다.
    virtual void OnNetworkEvent(const NetworkEvent& event) = 0;
};
}
