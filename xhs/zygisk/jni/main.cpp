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

#define LOG_TAG "XHSHook"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

static constexpr const char *TARGET_PKG = "com.xingin.xhs";

// Protocol: companion sends file count, then for each file: size (4 bytes) + data
// Files order: classes.dex, classes2.dex, classes3.dex, libpine.so

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

class QDHookModule : public zygisk::ModuleBase {
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

        // Save app data dir for later use
        if (args->app_data_dir) {
            const char *dir = env->GetStringUTFChars(args->app_data_dir, nullptr);
            if (dir) {
                app_data_dir = dir;
                env->ReleaseStringUTFChars(args->app_data_dir, dir);
            }
        }

        LOGI("Target process detected, loading resources from companion");

        // Connect to companion to read files
        int companion_fd = api->connectCompanion();
        if (companion_fd < 0) {
            LOGE("Failed to connect companion");
            is_target = false;
            return;
        }

        // Read file count
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

        // Step 1: Write libpine.so to app nativeLibraryDir
        // Use app_data_dir to construct a path the app can load from
        std::string pine_so_path = app_data_dir + "/cache/.pine.so";
        int fd = open(pine_so_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0755);
        if (fd < 0) {
            LOGE("Failed to create pine so at %s (errno=%d)", pine_so_path.c_str(), errno);
            return;
        }
        write_full(fd, files[3].data.data(), files[3].data.size());
        close(fd);
        LOGI("libpine.so written to %s", pine_so_path.c_str());

        // Step 2: Create InMemoryDexClassLoader FIRST (before loading native lib)
        // Pine's JNI_OnLoad needs to find Pine Java classes via the classloader
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

        // Use boot classloader as parent so Pine classes are found
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

        // Step 3: Load MainHook class from our DEX classloader
        jmethodID loadClass = env->GetMethodID(
            env->FindClass("java/lang/ClassLoader"), "loadClass",
            "(Ljava/lang/String;)Ljava/lang/Class;");

        jstring hookClassName = env->NewStringUTF("com.yuyang.qdhook.MainHook");
        jclass hookClass = (jclass) env->CallObjectMethod(dexClassLoader, loadClass, hookClassName);

        if (env->ExceptionCheck()) {
            env->ExceptionDescribe();
            env->ExceptionClear();
            LOGE("Failed to load MainHook class");
            unlink(pine_so_path.c_str());
            return;
        }

        if (!hookClass) {
            LOGE("MainHook class not found");
            unlink(pine_so_path.c_str());
            return;
        }
        LOGI("MainHook class loaded");

        // Step 4: Call MainHook.entry(classLoader, pineSoPath)
        // At postAppSpecialize, Application hasn't been created yet
        // Pass the system classloader; MainHook can access framework classes through it
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

        // Clean up the .so file after loading
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

// Companion handler: reads files from module directory and sends to target process
static void companion_handler(int fd) {
    // Get module directory - companion runs as root
    // Files are at: /data/adb/modules/xhshook_zygisk/dex/
    const char *dex_dir = "/data/adb/modules/xhshook_zygisk/dex";

    const char *file_names[] = {
        "classes.dex",
        "classes2.dex",
        "classes3.dex",
        "libpine.so"
    };
    int32_t file_count = 4;

    // Send file count
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

        // Read and send file data
        std::vector<uint8_t> buf(size);
        read_full(file_fd, buf.data(), size);
        write_full(fd, buf.data(), size);
        close(file_fd);

        __android_log_print(ANDROID_LOG_INFO, LOG_TAG, "Companion: sent %s (%d bytes)", file_names[i], size);
    }
}

REGISTER_ZYGISK_MODULE(QDHookModule)
REGISTER_ZYGISK_COMPANION(companion_handler)
