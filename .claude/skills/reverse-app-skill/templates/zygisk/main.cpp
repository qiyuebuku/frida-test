// Zygisk 模块模板 — 适用于任何 Android App 的 Hook 注入
// 使用方法：将 TARGET_PACKAGE 替换为目标 App 包名
//          将 MODULE_ID 替换为模块标识
//          将 HOOK_CLASS 替换为 MainHook 类的全限定名

#include <android/log.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <jni.h>
#include <string>
#include <unistd.h>
#include <vector>

#include "zygisk.hpp"

#define TAG "ZygiskHook"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

// ==================== 配置区 ====================
#define TARGET_PACKAGE "com.target.package"       // 替换为目标包名
#define HOOK_CLASS     "com.yuyang.hook.MainHook"  // 替换为 Hook 类名
// ================================================

struct FileData {
    std::vector<uint8_t> data;
};

static bool write_full(int fd, const void *buf, size_t count) {
    const uint8_t *p = (const uint8_t *)buf;
    while (count > 0) {
        ssize_t n = write(fd, p, count);
        if (n <= 0) return false;
        p += n;
        count -= n;
    }
    return true;
}

static bool read_full(int fd, void *buf, size_t count) {
    uint8_t *p = (uint8_t *)buf;
    while (count > 0) {
        ssize_t n = read(fd, p, count);
        if (n <= 0) return false;
        p += n;
        count -= n;
    }
    return true;
}

class HookModule : public zygisk::ModuleBase {
public:
    void onLoad(zygisk::Api *api, JNIEnv *env) override {
        this->api = api;
        this->env = env;
    }

    void preAppSpecialize(zygisk::AppSpecializeArgs *args) override {
        const char *name = env->GetStringUTFChars(args->nice_name, nullptr);
        if (!name || strcmp(name, TARGET_PACKAGE) != 0) {
            env->ReleaseStringUTFChars(args->nice_name, name);
            api->setOption(zygisk::DLCLOSE_MODULE_LIBRARY);
            return;
        }
        LOGI("Target app detected: %s", name);
        env->ReleaseStringUTFChars(args->nice_name, name);
        is_target = true;

        if (args->app_data_dir) {
            const char *dir = env->GetStringUTFChars(args->app_data_dir, nullptr);
            if (dir) {
                app_data_dir = dir;
                env->ReleaseStringUTFChars(args->app_data_dir, dir);
            }
        }

        // 从 companion 读取 DEX 和 libpine.so
        int fd = api->connectCompanion();
        if (fd < 0) {
            LOGE("Failed to connect companion");
            return;
        }

        int32_t file_count = 0;
        if (!read_full(fd, &file_count, 4) || file_count <= 0 || file_count > 10) {
            LOGE("Invalid file count: %d", file_count);
            close(fd);
            return;
        }

        files.resize(file_count);
        for (int i = 0; i < file_count; i++) {
            int32_t size = 0;
            if (!read_full(fd, &size, 4) || size <= 0 || size > 100 * 1024 * 1024) {
                LOGE("Invalid file size [%d]: %d", i, size);
                close(fd);
                return;
            }
            files[i].data.resize(size);
            if (!read_full(fd, files[i].data.data(), size)) {
                LOGE("Failed to read file [%d]", i);
                close(fd);
                return;
            }
            LOGI("Received file [%d]: %d bytes", i, size);
        }
        close(fd);
    }

    void postAppSpecialize(const zygisk::AppSpecializeArgs *args) override {
        if (!is_target || files.empty()) return;

        // 最后一个文件是 libpine.so，其余是 DEX
        int dex_count = (int)files.size() - 1;
        if (dex_count <= 0) {
            LOGE("No DEX files");
            return;
        }

        // 写 libpine.so 到 cache
        std::string pine_so_path = app_data_dir + "/cache/.pine.so";
        int fd = open(pine_so_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0755);
        if (fd < 0) {
            LOGE("Failed to create pine.so");
            return;
        }
        write_full(fd, files[dex_count].data.data(), files[dex_count].data.size());
        close(fd);
        LOGI("Wrote libpine.so: %zu bytes", files[dex_count].data.size());

        // 创建 InMemoryDexClassLoader
        jclass byteBufferClass = env->FindClass("java/nio/ByteBuffer");
        jmethodID allocDirect = env->GetStaticMethodID(byteBufferClass, "allocateDirect", "(I)Ljava/nio/ByteBuffer;");

        jobjectArray dexBuffers = env->NewObjectArray(dex_count, byteBufferClass, nullptr);
        for (int i = 0; i < dex_count; i++) {
            jobject buf = env->NewDirectByteBuffer(files[i].data.data(), files[i].data.size());
            env->SetObjectArrayElement(dexBuffers, i, buf);
        }

        jclass imClsLoaderClass = env->FindClass("dalvik/system/InMemoryDexClassLoader");
        jmethodID imClsLoaderCtor = env->GetMethodID(imClsLoaderClass, "<init>",
            "([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V");

        jclass clClass = env->FindClass("java/lang/ClassLoader");
        jmethodID getSystemCL = env->GetStaticMethodID(clClass, "getSystemClassLoader", "()Ljava/lang/ClassLoader;");
        jobject systemCL = env->CallStaticObjectMethod(clClass, getSystemCL);

        jobject dexClassLoader = env->NewObject(imClsLoaderClass, imClsLoaderCtor, dexBuffers, systemCL);

        // 加载 MainHook 类
        jmethodID loadClass = env->GetMethodID(clClass, "loadClass", "(Ljava/lang/String;)Ljava/lang/Class;");
        jstring hookClassName = env->NewStringUTF(HOOK_CLASS);
        jclass hookClass = (jclass)env->CallObjectMethod(dexClassLoader, loadClass, hookClassName);

        if (!hookClass || env->ExceptionCheck()) {
            LOGE("Failed to load MainHook class");
            env->ExceptionClear();
            unlink(pine_so_path.c_str());
            return;
        }

        // 调用 MainHook.entry(ClassLoader, String)
        jmethodID entryMethod = env->GetStaticMethodID(hookClass, "entry",
            "(Ljava/lang/ClassLoader;Ljava/lang/String;)V");
        jstring soPathStr = env->NewStringUTF(pine_so_path.c_str());
        env->CallStaticVoidMethod(hookClass, entryMethod, systemCL, soPathStr);

        if (env->ExceptionCheck()) {
            LOGE("MainHook.entry() threw exception");
            env->ExceptionDescribe();
            env->ExceptionClear();
        } else {
            LOGI("MainHook.entry() completed successfully");
        }

        // 清理 libpine.so
        unlink(pine_so_path.c_str());
    }

private:
    zygisk::Api *api = nullptr;
    JNIEnv *env = nullptr;
    bool is_target = false;
    std::string app_data_dir;
    std::vector<FileData> files;
};

// Companion handler — 以 root 身份读取 DEX 和 SO 文件
static void companion_handler(int fd) {
    // 模块文件目录（替换 MODULE_ID）
    const char *dex_dir = "/data/adb/modules/MODULE_ID/dex";
    const char *file_names[] = {
        "classes.dex", "classes2.dex", "classes3.dex", "libpine.so"
    };
    int32_t file_count = 4;

    write_full(fd, &file_count, 4);

    for (int i = 0; i < file_count; i++) {
        char path[256];
        snprintf(path, sizeof(path), "%s/%s", dex_dir, file_names[i]);

        int file_fd = open(path, O_RDONLY);
        if (file_fd < 0) {
            LOGE("Failed to open: %s", path);
            int32_t zero = 0;
            write_full(fd, &zero, 4);
            continue;
        }

        off_t size = lseek(file_fd, 0, SEEK_END);
        lseek(file_fd, 0, SEEK_SET);

        int32_t size32 = (int32_t)size;
        write_full(fd, &size32, 4);

        char buf[4096];
        ssize_t remaining = size;
        while (remaining > 0) {
            ssize_t n = read(file_fd, buf, std::min((ssize_t)sizeof(buf), remaining));
            if (n <= 0) break;
            write_full(fd, buf, n);
            remaining -= n;
        }
        close(file_fd);
        LOGI("Sent file [%d] %s: %d bytes", i, file_names[i], size32);
    }
}

REGISTER_ZYGISK_MODULE(HookModule)
REGISTER_ZYGISK_COMPANION(companion_handler)
