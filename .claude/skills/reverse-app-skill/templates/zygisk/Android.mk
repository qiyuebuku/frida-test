LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)
LOCAL_MODULE := hook_module
LOCAL_SRC_FILES := main.cpp
LOCAL_LDLIBS := -llog -ldl
LOCAL_CPPFLAGS := -std=c++17 -O2
include $(BUILD_SHARED_LIBRARY)
