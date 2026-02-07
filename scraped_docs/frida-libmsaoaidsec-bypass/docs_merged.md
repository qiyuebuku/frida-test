# Frida进阶——libmsaoaidsec.so反调试机制绕过

## 一、反调试分析

许多应用通过集成 libmsaoaidsec.so 库来防范 Frida 调试。在应用启动时，加载该库并通过 pthread_create() 创建一个新线程，在此线程中执行反调试检查，包括扫描 Frida 的进程、端口和内存特征。如果检测到 Frida，程序会终止或崩溃。

### 1.1、Hook android_dlopen_ext

这里我们就需要先hook Android的动态链接库加载函数 `android_dlopen_ext`，观察它加载到哪个 so 库文件的时候崩溃的（本文案例加载 libmsaoaidsec.so 时会崩溃）。
      
```javascript
function hook_dlopen() {
Interceptor.attach(Module.findExportByName(null, "android_dlopen_ext"),
    {
        onEnter: function (args) {
            this.fileName = args[0].readCString()
            console.log(`dlopen onEnter: ${this.fileName}`)
        }, onLeave: function(retval){
            console.log(`dlopen onLeave fileName: ${this.fileName}`)
            if(this.fileName != null && this.fileName.indexOf("libmsaoaidsec.so") >= 0){
                let JNI_OnLoad = Module.getExportByName(this.fileName, 'JNI_OnLoad')
                console.log(`dlopen onLeave JNI_OnLoad: ${JNI_OnLoad}`)
            }
        }
    }
);
}

setImmediate(hook_dlopen)
```
如下图所示，可以看到最后一个加载的 so 是`libmsaoaidsec.so`，并且没有调用`onLeave`，由此可知崩溃点就在`libmsaoaidsec.so`中，并且是在`JNI_OnLoad`之前检测的，so在加载之后会先调用`.init_proc`函数，接着调用`.init_array`中的函数，最后才是`JNI_OnLoad`函数。所以根据下图的日志信息可以确定检测点在`JNI_OnLoad`之前，接下来选择的注入时机可以选择在`dlopen`加载`libmsaoaidsec.so`之后。

![](https://i-blog.csdnimg.cn/img_convert/debf900cbf6314b784bd69ce4bd26be4.png)

需要注意的一点是在`dlopen`函数调用完成之后`.init_xxx`已经执行完成了。所以我们这里选择hook`call_constructors`函数。

关于 so 库文件加载相关函数介绍如下：

**函数名** |  **描述**  
---|---  
`android_dlopen_ext()` 、`dlopen()`、`do_dlopen()` |  这三个函数主要用于加载库文件。`android_dlopen_ext` 是系统的一个函数，用于在运行时动态加载共享库。与标准的 `dlopen()` 函数相比，`android_dlopen_ext` 提供了更多的参数选项和扩展功能，例如支持命名空间、符号版本等特性。  
`find_library()` |  `find_library()` 函数用于查找库，基本的用途是给定一个库的名字，然后查找并返回这个库的路径。  
`call_constructors()` |  `call_constructors()` 是用于调用动态加载库中的构造函数的函数。  
`init` |  库的构造函数，用于初始化库中的静态变量或执行其他需要在库被加载时完成的任务。如果没有定义`init`函数，系统将不会执行任何动作。需要注意的是，`init`函数不应该有任何参数，并且也没有返回值。  
`init_array` |  `init_array`是ELF（Executable and Linkable Format，可执行和可链接格式）二进制格式中的一个特殊段（section），这个段包含了一些函数的指针，这些函数将在`main()`函数执行前被调用，用于初始化静态局部变量和全局变量。  
`jni_onload` |  这是Android JNI(Java Native Interface)中的一个函数。当一个native库被系统加载时，该函数会被自动调用。`JNI_OnLoad`可以做一些初始化工作，例如注册你的native方法或者初始化一些数据结构。如果你的native库没有定义这个函数，那么JNI会使用默认的行为。`JNI_OnLoad`的返回值应该是需要的JNI版本，一般返回`JNI_VERSION_1_6`。  
  
### 1.2、Hook call_constructors

Hook`call_constructors`函数代码如下：
      
```javascript
function hook_call_constructors() {
let linker = Process.findModuleByName("linker64");
let call_constructors_addr, get_soname;
let symbols = linker.enumerateSymbols();
for (let index = 0; index < symbols.length; index++) {
    let symbol = symbols[index];
    if (symbol.name === "__dl__ZN6soinfo17call_constructorsEv") {
        call_constructors_addr = symbol.address;
    } else if (symbol.name === "__dl__ZNK6soinfo10get_sonameEv") {
        get_soname = new NativeFunction(symbol.address, "pointer", ["pointer"]);
    }
}

console.log("call_constructors_addr: " + call_constructors_addr)
var listener = Interceptor.attach(call_constructors_addr, {
    onEnter: function (args) {
        var module = Process.findModuleByName("libmsaoaidsec.so")
        if (module != null) {
            console.log("libmsaoaidsec.so base address: " + module.base);
            listener.detach()
        }
    },
    onLeave: function (retval) {
        console.log("onLeave call_constructors")
    }
})
}
```
### 1.3、Hook pthread_create

使用 IDA 载入 libmsaoaidsec.so，发现没有导入 pthread_create 符号。

![](https://i-blog.csdnimg.cn/img_convert/02f75b088df4ba234fe6bc754200cd0d.png)

使用 Frida 脚本尝试 HOOK [pthread_create 函数](<https://so.csdn.net/so/search?q=pthread_create%20%E5%87%BD%E6%95%B0&spm=1001.2101.3001.7020>)，也没有找到来自 libmsaoaidsec.so 的调用。
      
```javascript

function hook_pthread_create() {
Interceptor.attach(Module.findExportByName("libc.so", "pthread_create"), {
onEnter: function (args) {
  var threadFunctionAddress = args[2];
  // baseAddress 应为 libmsaoaidsec.so 基地址
  var offset = threadFunctionAddress.sub(baseAddress);
  console.log("The thread function offset is " + offset);
},
});
}
```
### 1.4、Hook dlsym

所以接下来我们尝试hook dlsym函数，在加载 libmsaoaidsec.so 之前hook dlsym函数，代码如下：
      
```javascript
function hook_dlopen() {
Interceptor.attach(Module.findExportByName(null, "android_dlopen_ext"),
    {
        onEnter: function (args) {
            this.fileName = args[0].readCString()
            console.log(`dlopen onEnter: ${this.fileName}`)
            if (fileName.indexOf("libmsaoaidsec.so") !== -1) {
                hook_call_constructors()
                hook_dlsym()
            }
        }, onLeave: function(retval){
            console.log(`dlopen onLeave fileName: ${this.fileName}`)
            if(this.fileName != null && this.fileName.indexOf("libmsaoaidsec.so") >= 0){
                let JNI_OnLoad = Module.getExportByName(this.fileName, 'JNI_OnLoad')
                console.log(`dlopen onLeave JNI_OnLoad: ${JNI_OnLoad}`)
            }
        }
    }
);
}


function hook_call_constructors() {
let linker = Process.findModuleByName("linker64");
let call_constructors_addr, get_soname;
let symbols = linker.enumerateSymbols();
for (let index = 0; index < symbols.length; index++) {
    let symbol = symbols[index];
    if (symbol.name === "__dl__ZN6soinfo17call_constructorsEv") {
        call_constructors_addr = symbol.address;
    } else if (symbol.name === "__dl__ZNK6soinfo10get_sonameEv") {
        get_soname = new NativeFunction(symbol.address, "pointer", ["pointer"]);
    }
}

console.log("call_constructors_addr: " + call_constructors_addr)
var listener = Interceptor.attach(call_constructors_addr, {
    onEnter: function (args) {
        var module = Process.findModuleByName("libmsaoaidsec.so")
        if (module != null) {
            console.log("libmsaoaidsec.so base address: " + module.base);
            listener.detach()
        }
    },
    onLeave: function (retval) {
        console.log("onLeave call_constructors")
    }
})
}


function hook_dlsym() {
var interceptor = Interceptor.attach(Module.findExportByName(null, "dlsym"),
    {
        onEnter: function (args) {
            const name = ptr(args[1]).readCString()
            if (name == "pthread_create") {
                console.log("[dlsym]: ", name, " address: ", this.context.lr)
            }
        }
    }
)
return Interceptor
}

setImmediate(hook_dlopen)
```
输出结果如下图所示，证明它确实会调用 pthread_create 函数，只是调用方式不是直接调用。

![](https://i-blog.csdnimg.cn/img_convert/4bc98990cfeb021b8f53ab9b4cf03a49.png)

通过上图打印的日志信息可以计算 pthread_create 函数的调用位置为 0x6eb4eb2d1c、0x6eb4ebe4c4，相对于 libmsaoaidsec.so 库文件基地址的偏移地址分别为 0x1bd1c、0x274c4。

通过 IDA 查看这两个偏移地址 0x1bd1c、0x274c4 所在的函数位置分别为 0x1B924 和 0x2701C。

![](https://i-blog.csdnimg.cn/img_convert/a35f495798ef27c00057757400c016dd.png)

![](https://i-blog.csdnimg.cn/img_convert/8abbfa732df6d6db641ab6a66964cd08.png)

那么我们就可以在 hook_call_constructors 函数中，使用 Interceptor.replace 将 0x1B924 和 0x2701C 这两个函数替换为空函数。
      
```javascript
function hook_call_constructors() {
let linker = null;
linker = Process.findModuleByName("linker64");
let call_constructors_addr, get_soname
let symbols = linker.enumerateSymbols();
for (let index = 0; index < symbols.length; index++) {
    let symbol = symbols[index];
    if (symbol.name === "__dl__ZN6soinfo17call_constructorsEv") {
        console.log(symbol.name)
        call_constructors_addr = symbol.address;
    } else if (symbol.name === "__dl__ZNK6soinfo10get_sonameEv") {
        console.log(symbol.name)
        get_soname = new NativeFunction(symbol.address, "pointer", ["pointer"]);
    }
}

console.log("call_constructors_addr: " + call_constructors_addr)
let hookCount = 0;
var listener = Interceptor.attach(call_constructors_addr, {
    onEnter: function (args) {
        var module = Process.findModuleByName("libmsaoaidsec.so")
        if (module != null) {
            console.log("libmsaoaidsec.so base address: " + module.base);
            Interceptor.replace(module.base.add(0x1B924), new NativeCallback(function () {
                console.log("0x1B924: nop成功", ++hookCount)
            }, "void", []))
            Interceptor.replace(module.base.add(0x2701C), new NativeCallback(function () {
                console.log("0x2701C: nop成功", ++hookCount)
            }, "void", []))
            listener.detach()
        }
    },
    onLeave: function (retval) {
        console.log("hookCount", hookCount)
    }
})
}
```
这样就可以绕过 ibmsaoaidsec.so 库文件的反调试机制了。

## 二、完整代码
      
```javascript
function hook_dlopen() {
Interceptor.attach(Module.findExportByName(null, "android_dlopen_ext"),
    {
        onEnter: function (args) {
            this.fileName = args[0].readCString()
            console.log(`dlopen onEnter: ${this.fileName}`)
            if (fileName.indexOf("libmsaoaidsec.so") !== -1) {
                hook_call_constructors()
                hook_dlsym()
            }
        }, onLeave: function(retval){
            console.log(`dlopen onLeave fileName: ${this.fileName}`)
            if(this.fileName != null && this.fileName.indexOf("libmsaoaidsec.so") >= 0){
                let JNI_OnLoad = Module.getExportByName(this.fileName, 'JNI_OnLoad')
                console.log(`dlopen onLeave JNI_OnLoad: ${JNI_OnLoad}`)
            }
        }
    }
);
}


function hook_call_constructors() {
let linker = null;
linker = Process.findModuleByName("linker64");
let call_constructors_addr, get_soname
let symbols = linker.enumerateSymbols();
for (let index = 0; index < symbols.length; index++) {
    let symbol = symbols[index];
    if (symbol.name === "__dl__ZN6soinfo17call_constructorsEv") {
        console.log(symbol.name)
        call_constructors_addr = symbol.address;
    } else if (symbol.name === "__dl__ZNK6soinfo10get_sonameEv") {
        console.log(symbol.name)
        get_soname = new NativeFunction(symbol.address, "pointer", ["pointer"]);
    }
}

console.log("call_constructors_addr: " + call_constructors_addr)
let hookCount = 0;
var listener = Interceptor.attach(call_constructors_addr, {
    onEnter: function (args) {
        var module = Process.findModuleByName("libmsaoaidsec.so")
        if (module != null) {
            console.log("libmsaoaidsec.so base address: " + module.base);
            Interceptor.replace(module.base.add(0x1B924), new NativeCallback(function () {
                console.log("0x1B924: nop成功", ++hookCount)
            }, "void", []))
            Interceptor.replace(module.base.add(0x2701C), new NativeCallback(function () {
                console.log("0x2701C: nop成功", ++hookCount)
            }, "void", []))
            listener.detach()
        }
    },
    onLeave: function (retval) {
        console.log("hookCount", hookCount)
    }
})
}


function hook_dlsym() {
var interceptor = Interceptor.attach(Module.findExportByName(null, "dlsym"),
    {
        onEnter: function (args) {
            const name = ptr(args[1]).readCString()
            if (name == "pthread_create") {
                console.log("[dlsym]: ", name, " address: ", this.context.lr)
            }
        }
    }
)
return Interceptor
}

setImmediate(hook_dlopen)
```
