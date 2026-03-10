#include <cstring>
#include <cstdlib>
#include <cerrno>
#include <string>
#include <vector>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <android/log.h>
#include <dlfcn.h>
#include "zygisk.hpp"

#define LOG_TAG "THSHook"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

static constexpr const char *TARGET_PKG = "com.hexin.plat.android";

struct FileData {
    std::vector<uint8_t> data;
};

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

class THSHookModule : public zygisk::ModuleBase {
public:
    void onLoad(zygisk::Api *api, JNIEnv *env) override {
        this->api = api;
        this->env = env;
    }

    void preAppSpecialize(zygisk::AppSpecializeArgs *args) override {
        const char *name = env->GetStringUTFChars(args->nice_name, nullptr);
        if (name && strcmp(name, TARGET_PKG) == 0) {
            is_target = true;
        }
        if (name) env->ReleaseStringUTFChars(args->nice_name, name);

        if (!is_target) {
            api->setOption(zygisk::DLCLOSE_MODULE_LIBRARY);
            return;
        }

        if (args->app_data_dir) {
            const char *dir = env->GetStringUTFChars(args->app_data_dir, nullptr);
            if (dir) {
                app_data_dir = dir;
                env->ReleaseStringUTFChars(args->app_data_dir, dir);
            }
        }

        LOGI("Target process detected, loading resources from companion");

        int companion_fd = api->connectCompanion();
        if (companion_fd < 0) {
            LOGE("Failed to connect companion");
            is_target = false;
            return;
        }

        int32_t file_count = 0;
        if (!read_full(companion_fd, &file_count, 4)) {
            LOGE("Failed to read file count");
            close(companion_fd);
            is_target = false;
            return;
        }

        LOGI("Companion sending %d files", file_count);

        files.resize(file_count);
        for (int i = 0; i < file_count; i++) {
            int32_t size = 0;
            if (!read_full(companion_fd, &size, 4)) {
                LOGE("Failed to read file %d size", i);
                close(companion_fd);
                is_target = false;
                return;
            }
            files[i].data.resize(size);
            if (!read_full(companion_fd, files[i].data.data(), size)) {
                LOGE("Failed to read file %d data (%d bytes)", i, size);
                close(companion_fd);
                is_target = false;
                return;
            }
            LOGI("File %d: %d bytes loaded", i, size);
        }

        close(companion_fd);
    }

    void postAppSpecialize(const zygisk::AppSpecializeArgs *args) override {
        if (!is_target || files.empty()) return;

        LOGI("postAppSpecialize: loading Pine and hook DEX");

        // Files: [0] = classes.dex, [1] = classes2.dex, [2] = classes3.dex, [3] = libpine.so
        if (files.size() < 4) {
            LOGE("Expected 4 files, got %zu", files.size());
            return;
        }

        // Step 1: Write libpine.so
        std::string cache_dir = app_data_dir + "/cache";
        mkdir(cache_dir.c_str(), 0755);
        std::string pine_so_path = cache_dir + "/.pine.so";
        int fd = open(pine_so_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0755);
        if (fd < 0) {
            // Fallback to /data/local/tmp
            pine_so_path = "/data/local/tmp/.thshook_pine.so";
            fd = open(pine_so_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0755);
            if (fd < 0) {
                LOGE("Failed to create pine so at both paths (errno=%d)", errno);
                return;
            }
        }
        write_full(fd, files[3].data.data(), files[3].data.size());
        close(fd);
        LOGI("libpine.so written to %s", pine_so_path.c_str());

        // Step 2: Create InMemoryDexClassLoader
        jclass byteBufferClass = env->FindClass("java/nio/ByteBuffer");
        jobjectArray dexBuffers = env->NewObjectArray(3, byteBufferClass, nullptr);

        for (int i = 0; i < 3; i++) {
            jobject buf = env->NewDirectByteBuffer(files[i].data.data(), files[i].data.size());
            env->SetObjectArrayElement(dexBuffers, i, buf);
        }

        jclass imClsLoaderClass = env->FindClass("dalvik/system/InMemoryDexClassLoader");
        if (!imClsLoaderClass) {
            LOGE("InMemoryDexClassLoader not found");
            unlink(pine_so_path.c_str());
            return;
        }

        jclass clClass = env->FindClass("java/lang/ClassLoader");
        jmethodID getSystemCL = env->GetStaticMethodID(clClass, "getSystemClassLoader", "()Ljava/lang/ClassLoader;");
        jobject systemClassLoader = env->CallStaticObjectMethod(clClass, getSystemCL);

        jmethodID imClsLoaderCtor = env->GetMethodID(imClsLoaderClass, "<init>",
            "([Ljava/nio/ByteBuffer;Ljava/lang/ClassLoader;)V");
        jobject dexClassLoader = env->NewObject(imClsLoaderClass, imClsLoaderCtor,
            dexBuffers, systemClassLoader);

        if (env->ExceptionCheck()) {
            env->ExceptionDescribe();
            env->ExceptionClear();
            LOGE("Failed to create InMemoryDexClassLoader");
            unlink(pine_so_path.c_str());
            return;
        }

        LOGI("InMemoryDexClassLoader created");

        // Step 3: Load MainHook class
        LOGI("Step 3: Loading MainHook class...");
        jmethodID loadClass = env->GetMethodID(
            env->FindClass("java/lang/ClassLoader"), "loadClass",
            "(Ljava/lang/String;)Ljava/lang/Class;");
        LOGI("Got loadClass method");

        jstring hookClassName = env->NewStringUTF("com.yuyang.thshook.MainHook");
        LOGI("Created className string");

        LOGI("Calling loadClass on dexClassLoader...");
        jclass hookClass = (jclass) env->CallObjectMethod(dexClassLoader, loadClass, hookClassName);
        LOGI("loadClass returned, checking for exceptions...");

        if (env->ExceptionCheck()) {
            LOGE("Exception occurred during loadClass!");
            env->ExceptionDescribe();  // This should print to logcat
            jthrowable exc = env->ExceptionOccurred();
            if (exc) {
                jclass throwableClass = env->FindClass("java/lang/Throwable");
                jmethodID getMessage = env->GetMethodID(throwableClass, "getMessage", "()Ljava/lang/String;");
                jstring message = (jstring) env->CallObjectMethod(exc, getMessage);
                if (message) {
                    const char* msgChars = env->GetStringUTFChars(message, nullptr);
                    LOGE("Exception message: %s", msgChars);
                    env->ReleaseStringUTFChars(message, msgChars);
                }
            }
            env->ExceptionClear();
            LOGE("Failed to load MainHook class");
            unlink(pine_so_path.c_str());
            return;
        }

        if (!hookClass) {
            LOGE("MainHook class not found (hookClass is null)");
            unlink(pine_so_path.c_str());
            return;
        }
        LOGI("MainHook class loaded successfully!");

        // Step 4: Call MainHook.entry(classLoader, pineSoPath)
        jmethodID entryMethod = env->GetStaticMethodID(hookClass, "entry",
            "(Ljava/lang/ClassLoader;Ljava/lang/String;)V");
        if (!entryMethod) {
            LOGE("MainHook.entry(ClassLoader,String) method not found");
            unlink(pine_so_path.c_str());
            return;
        }

        jstring soPathStr = env->NewStringUTF(pine_so_path.c_str());

        LOGI("Calling MainHook.entry()");
        env->CallStaticVoidMethod(hookClass, entryMethod, systemClassLoader, soPathStr);

        if (env->ExceptionCheck()) {
            env->ExceptionDescribe();
            env->ExceptionClear();
            LOGE("MainHook.entry() threw exception");
            return;
        }

        unlink(pine_so_path.c_str());
        LOGI("Hook initialization complete");
    }

private:
    zygisk::Api *api = nullptr;
    JNIEnv *env = nullptr;
    bool is_target = false;
    std::string app_data_dir;
    std::vector<FileData> files;
};

static void companion_handler(int fd) {
    const char *dex_dir = "/data/adb/modules/thshook_zygisk/dex";

    const char *file_names[] = {
        "classes.dex",
        "classes2.dex",
        "classes3.dex",
        "libpine.so"
    };
    int32_t file_count = 4;

    write_full(fd, &file_count, 4);

    for (int i = 0; i < file_count; i++) {
        char path[256];
        snprintf(path, sizeof(path), "%s/%s", dex_dir, file_names[i]);

        int file_fd = open(path, O_RDONLY);
        if (file_fd < 0) {
            __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, "Companion: failed to open %s", path);
            int32_t size = 0;
            write_full(fd, &size, 4);
            continue;
        }

        struct stat st;
        fstat(file_fd, &st);
        int32_t size = (int32_t)st.st_size;

        write_full(fd, &size, 4);

        std::vector<uint8_t> buf(size);
        read_full(file_fd, buf.data(), size);
        write_full(fd, buf.data(), size);
        close(file_fd);

        __android_log_print(ANDROID_LOG_INFO, LOG_TAG, "Companion: sent %s (%d bytes)", file_names[i], size);
    }
}

REGISTER_ZYGISK_MODULE(THSHookModule)
REGISTER_ZYGISK_COMPANION(companion_handler)
