#include <android/hardware/bluetooth/1.1/IBluetoothHci.h>
#include <android/log.h>
#include <errno.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

namespace android::hardware {
void configureRpcThreadpool(size_t maxThreads, bool callerWillJoin);
void joinRpcThreadpool();
}

using android::sp;
using android::hardware::Return;
using android::hardware::hidl_vec;
namespace bt = android::hardware::bluetooth;
using bt::V1_0::Status;

namespace {
constexpr char kLogTag[] = "RedroidIntelHci";

bool readAll(int fd, uint8_t* data, size_t length) {
    while (length != 0) {
        const ssize_t count = TEMP_FAILURE_RETRY(read(fd, data, length));
        if (count <= 0) return false;
        data += count;
        length -= count;
    }
    return true;
}

bool sendAll(int fd, const uint8_t* data, size_t length) {
    while (length != 0) {
        const ssize_t count = TEMP_FAILURE_RETRY(send(fd, data, length, MSG_NOSIGNAL));
        if (count <= 0) return false;
        data += count;
        length -= count;
    }
    return true;
}

class Bridge final : public bt::V1_1::IBluetoothHci {
public:
    Return<void> initialize(const sp<bt::V1_0::IBluetoothHciCallbacks>& cb) override {
        return start(cb, nullptr);
    }
    Return<void> initialize_1_1(const sp<bt::V1_1::IBluetoothHciCallbacks>& cb) override {
        return start(cb, cb);
    }
    Return<void> sendHciCommand(const hidl_vec<uint8_t>& data) override {
        return transmit(1, data);
    }
    Return<void> sendAclData(const hidl_vec<uint8_t>& data) override {
        return transmit(2, data);
    }
    Return<void> sendScoData(const hidl_vec<uint8_t>& data) override {
        return transmit(3, data);
    }
    Return<void> sendIsoData(const hidl_vec<uint8_t>& data) override {
        return transmit(5, data);
    }
    Return<void> close() override {
        if (fd_ >= 0) {
            shutdown(fd_, SHUT_RDWR);
            pthread_join(reader_, nullptr);
            ::close(fd_);
            fd_ = -1;
            callback_.clear();
            callback11_.clear();
        }
        return {};
    }
private:
    Return<void> start(const sp<bt::V1_0::IBluetoothHciCallbacks>& cb,
                       const sp<bt::V1_1::IBluetoothHciCallbacks>& cb11) {
        if (!cb) return {};
        if (fd_ >= 0) return cb->initializationComplete(Status::INITIALIZATION_ERROR);
        const sockaddr_un address = {AF_UNIX, "/data/local/tmp/redroid-intel-hci.sock"};
        fd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (fd_ < 0 || connect(fd_, reinterpret_cast<const sockaddr*>(&address),
                               sizeof(address)) < 0) {
            __android_log_print(ANDROID_LOG_ERROR, kLogTag, "Bridge connect failed: %d", errno);
            if (fd_ >= 0) ::close(fd_);
            fd_ = -1;
            return cb->initializationComplete(Status::INITIALIZATION_ERROR);
        }
        callback_ = cb;
        callback11_ = cb11;
        if (pthread_create(&reader_, nullptr, run, this) != 0) {
            ::close(fd_);
            fd_ = -1;
            callback_.clear();
            callback11_.clear();
            return cb->initializationComplete(Status::INITIALIZATION_ERROR);
        }
        __android_log_print(ANDROID_LOG_INFO, kLogTag, "Intel controller stream connected");
        return cb->initializationComplete(Status::SUCCESS);
    }

    Return<void> transmit(uint8_t type, const hidl_vec<uint8_t>& data) {
        if (fd_ < 0 || !sendAll(fd_, &type, 1) || !sendAll(fd_, data.data(), data.size())) {
            __android_log_print(ANDROID_LOG_ERROR, kLogTag, "HCI send failed");
        }
        return {};
    }

    static void* run(void* context) {
        static_cast<Bridge*>(context)->receive();
        return nullptr;
    }

    void receive() {
        uint8_t type;
        while (readAll(fd_, &type, 1)) {
            size_t header;
            switch (type) {
                case 2: case 5: header = 4; break;
                case 3: header = 3; break;
                case 4: header = 2; break;
                default: return;
            }
            uint8_t preamble[4];
            if (!readAll(fd_, preamble, header)) break;
            size_t length = preamble[header - 1];
            if (type == 2 || type == 5) {
                length = preamble[2] | (size_t(preamble[3]) << 8);
                if (type == 5) length &= 0x3fff;
            }
            hidl_vec<uint8_t> packet;
            packet.resize(header + length);
            for (size_t i = 0; i < header; ++i) packet[i] = preamble[i];
            if (!readAll(fd_, packet.data() + header, length)) break;
            bool delivered;
            switch (type) {
                case 2: delivered = callback_->aclDataReceived(packet).isOk(); break;
                case 3: delivered = callback_->scoDataReceived(packet).isOk(); break;
                case 4: delivered = callback_->hciEventReceived(packet).isOk(); break;
                case 5: delivered = !callback11_ || callback11_->isoDataReceived(packet).isOk(); break;
                default: return;
            }
            if (!delivered) break;
        }
        __android_log_print(ANDROID_LOG_INFO, kLogTag, "Intel controller stream closed");
    }

    int fd_ = -1;
    pthread_t reader_{};
    sp<bt::V1_0::IBluetoothHciCallbacks> callback_;
    sp<bt::V1_1::IBluetoothHciCallbacks> callback11_;
};
}

int main() {
    android::hardware::configureRpcThreadpool(1, true);
    sp<Bridge> bridge = new Bridge();
    if (bridge->registerAsService() != android::OK) return 1;
    android::hardware::joinRpcThreadpool();
    return 1;
}
