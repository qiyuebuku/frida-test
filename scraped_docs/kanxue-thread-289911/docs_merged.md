# 文档合集

> 共 1 篇文档
> 生成时间: 2026-02-04 15:50:25

## 目录

1. [[原创]Frida 检测 libmsaoaidsec.so 绕过学习-Android安全](#doc-1)



---

<a id="doc-1"></a>

## 1. [原创]Frida 检测 libmsaoaidsec.so 绕过学习-Android安全

> 来源: https://bbs.kanxue.com/thread-289911.htm

# [原创]Frida 检测 libmsaoaidsec.so 绕过学习-Android安全

```js
frida 16.2.1
android 14
IDA pro 9.1
```  
---  
  
# b站 7.76版本——8.81版本（最新版）

### SO 库的加载流程详解

Android 系统加载一个 SO 库的顺序如下：

  1. **`dlopen`** **/** **`android_dlopen_ext`** ：系统调用加载器，将 SO 映射到内存。
  2. **`.init`** **/** **`.init_proc`** ：执行初始化段的代码。
  3. **`.init_array`** ：执行初始化数组中的函数（C++ 全局构造函数等）。
  4. **`JNI_OnLoad`** ：最后执行，通常用于注册 JNI 方法。

## 定位检测点

```c
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
            }, onLeave: function (retval) {
                console.log("结束");
 
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
 
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
可以看到除了`libmsaoaidsec.so`，都输出了结束，并且最后是打印`libmsaoaidsec.so`后程序退出，所以`libmsaoaidsec.so`大概率为检测的so文件，并且没有打印出“结束”，所以我们可以确定是检测点在so加载完成之前

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_NYFNW6EK6THZE7T.webp)

```
sakura@SakuradeMacBook-Pro ~ % frida -H 127.0.0.1:9898 -f tv.danmoku.bili -1 /Users/sakura/work/tools/android/unidbg-0.9.8/unidbg-android/src/test/java/com/lession5/bilibili/1.js
Frida 16.2.1-A world-class dynamic instrumentation toolkit
1(11
>-1
Commands:
/_/1_1
help
->Displays the help system
object?-> Display information about‘object’
exit/quit-> Exit
More info at https://frida.re/docs/home/
Connected to 127.0.0.1:9898 (id=socket@127.0.0.1:9898)
Spawning‘tv.danmaku.bili"...
Spawnedtv.danmaku.bili`.Resuming main thread!
[*] Hooking android_dlopen_ext at 1ibc.so!exNoN
[dlopen] libframework-connectivity-tiramisu-jni.so
[Remote::tv.danmaku.bili]->结束
[dlopen]/system/framework/oat/arm64/com.android.future.usb.accessory.odex
结束
[dlopen]/system/framework/oat/arm64/org.apache.http.legacy.odex
结束
[dlopen]/data/app/~~z0sp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/oat/arm64/base.odex
结束
[dlopen]/data/app/~zθsp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/Lib/arm64/Libb1kv.so
结束
[dlopen]/data/app/~zθsp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/Lib/arm64/Libbytehook.so
结束
[dlopen]/data/app/~zθsp1JUw5Ezui.JvjCs015g-=/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/Lib/arm64/1ibbili_core.so
结束
[dlopen]/data/app/~~z0sp1JUW5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/lib/arm64/libbilicr.88.0.4324.188.so
结束
[dlopen]/data/app/~z0sp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/lib/arm64/libijkffmpeg.so
结束
[dlopen]/data/app/~~z0sp1JUW5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA/lib/arm64/libavif-jni.so
结束
[dlopen]/data/app/~2θsp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/Lib/arm64/Libbili . so
结束
[dlopen]/data/user/@/tv.danmaku.bili/app_tribe/bundles/oaidkit/16931321e0/libs/libmsaoaidsec.so
Process terminoted
看雪
[Remote::tv.danmaku.bili ]->
```

可以通过在dlopen结束之后，去HOOK JNI_Onload函数，去判断检测函数在JNI_Onload之前还是之后

```js
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
            }, onLeave: function (retval) {
                if (this.isTarget) {
                    hook_JNI_OnLoad();
                }
 
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
 
function hook_JNI_OnLoad() {
    let module = Process.findModuleByName("libmsaoaidsec.so")
    Interceptor.attach(module.base.add(0x13A4C), {
        onEnter(args) {
            console.log("JNI_OnLoad")
        }
    })
}
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
发现最后没有打印出JNI_OnLoad，所以肯定是在JNI_OnLoad之前检测的

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_JBVZNSU7BBEUVHJ.webp)

```
sakura@SakuradeMacBook-Pro ~% frida -H 127.0.0.1:9898 -f tv.danmaku.bili -1/Users/sakura/work/tools/android/unidbg-0.9.8/unidbg-android/src/test/java/com/lession5/bilibili/1.js
Frida 16.2.1-A world-class dynamic instrumentation toolkit
1(11
>-1
Commands:
/_/1_1
help
->Displays the help system
object?-> Display information about&#x27;object&#x27;
exit/quit -> Exit
More info at https://frida.re/docs/home/
Connected to 127.0.0.1:9898 (id=socket@127.0.0.1:9898)
Spawningtv.danmaku.bili...
[*] Hooking android_dlopen_ext at libc.so!exNaN
Spawnedtv.danmaku.bili&#x27;.Resuming main thread!
[Remote::tv.danmaku.bili]-> [dlopen] libframework-connectivity-tiramisu-jni.so
[dlopen]/system/framework/oat/arm64/com.android.future.usb.accessory.odex
[dlopen]/system/framework/oat/arm64/org.apache.http.legacy.odex
[dlopen]/data/app/-~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/oat/arm64/base.odex
[dlopen]/data/app/~~z0sp1JUW5EzuiJvjCs015g=/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/lib/arm64/Libblkv.so
[dlopen]/data/app/~~z0sp1JUW5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbytehook.so
[dlopen]/data/app/~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbili_core.so
[dlopen]/data/opp/~~z0sp1JUw5EzuiJvjCs015g==/tv.donmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/1ibbilicr.88.0.4324.188.so
[dlopen]/data/app/~~z0sp1JUw5EzuiJvjCs015g=/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/lib/arm64/Libijkffmpeg.so
[dlopen]/data/app/~~zθsp13uw5EzuiJvjCs015g=/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA/lib/arm64/1ibavif-jni.so
[dlopen]/data/app/~~z0sp1JUw5EzuiJvjCs015g=/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbili.so
[dlopen]/data/app/~IgN3vCh5CX8qwzGofE1uuQ==/com.google.android.webview-fMmCqKrDChwmnzLVUMwTVw=/oat/arm64/WebViewGoogle.odex
[dlopen]/data/app/~~z0sp13UW5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/Lib/arm64/Libc++_shared.so
[dlopen] libmonochrome.so
[dlopen]/data/user/@/tv.danmaku.bili/app_tribe/bundles/apm/146840050e/1ibs/libkoom-java.so
[dlopen]/data/app/~IgN3vCh5CX8qwzG0fE1uuQ==/com.google.android.webview-fMmCqKrDChwmnzLVUMwTVwm=/WebViewGoogle.apk!/Lib/arm64-v8a/Libmonochrome .so
[dlopen]/data/user/0/tv.danmaku.bili/app_tribe/bundles/oaidkit/169313210e/libs/libmsaoaidsec.so
榮看雪
Process terminated
[Remote::tv.danmaku.bili ]->
```

如果在JNI_OnLoad加载后，执行结果应当如下：会先打印出JNI_OnLoad再退出

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_JF7WNYHM3Y53SKF.webp)

```
sakura@SakuradeMacBook-Pro~%frida-H127.0.0.1:9898-fcom.icbc-1/Users/sakura/work/tools/android/unidbg-0.9.8/unidbg-android/src/test/java/com/lession5/funeng.is
Frida 16.2.1-Aworld-classdynamicinstrumentation toolkit
1（-1
Commands:
//1！
help
->Displays the help system
object？->Displayinformationabout&#x27;object&#x27;
exit/quit->Exit
More infoathttps://frida.re/docs/home/
Connected to127.0.0.1:9898(id=socket@127.0.0.1:9898)
Spawningcom.icbc...
[*]Hooking android_dlopen_ext atlibc.so!OxNaN
Spawnedcom.icbc.Resumingmain thread!
[dlopen] libframework-connectivity-tiramisu-jni.so
[Remote::com.icbc ]-> [dlopen] libstats_jni.so
[dlopen]/system/framework/oat/arm64/org.apache.http.legacy.odex
[dlopen]/data/app/~~_Smuzej7z8eL_ZmtAT0xWQ==/com.icbc-wiCz6R6dxtwJ082jwP725Q==/oat/arm64/base.odex
[dlopen]/data/app/~~_Smuzej7z8eL_ZmtAT0xWQ==/com.icbc-wiCz6R6dxtwJ082jwP725Q==/Lib/arm64/LibDexHelper.so
JNI_OnLoad
看雪
Process terminated
[Remote::com.icbc]->
```

所以根据以上的结果，我们可以判断出，检测函数是在JNI_OnLoad之前就开始检测了

所以我们找一个靠前的时间点， 在so初始化的时候我们找一个锚点，在这个锚点再去进行后续的hook，首先我们先找锚点

## 绕过检测点

打开IDA pro，我们一般情况下都会找 **`__system_property_get`**作为我们的锚点，一般情况下参数为`ro.build.version.sdk`

我们可以直接通过IDA搜索字符串`ro.build.version.sdk`，双击跳转到这个函数

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_4QMKS5D8WNRGPBZ.webp)

```
Data
Unexplored
External symbol
Lumina function
工
IDA View-A
X
Pseudocode-B
X
Pseudocode-A
口
Strings
Hex View-1
0
Local Types
Imports
Exports
Address
Length
Type
String
LOAD:0000...
00000015
C
ro.build.version.sdk
ro.build.version.sdk
Line 1 of 1
```

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_EQRCJH6DA2BAEHV.webp)

```
国
IDA View-A
Pseudocode-B
Pseudocode-A
×
国
Strings
Hex View-1
×
Local Types
Imports
LOAD: 000000000002F8B5
LOAD:000000000002F8B5
：DATA XREF: sub_12340+14+0
LOAD:000000000002F8CE aSdk
DCB “SDK",0
：sub_12600+14t0
：DATA XREF:5ub_12340+38+0
LOAD:0eeo000002F8D2aRoBuildVersion DCB"ro.build.version.sdkA
L0AD: 000000000002F8CE
；sub_12340+44+0
LOAD:000000000002F8D2
DATA XREF:
Sub_
173F0+10+0
L0AD: 000000000002F8E7
aRoBuildVersion_θ DCB "ro.build.version
L0AD: 000000000002F8E7
；DATA XREF: Sub_12440+14+0
odero
L0AD: 000000000002F8E7
DCB"12",0
；sub_12440+1Cto
LOAD:000000000002F98C
LOAD: 000000000002F90F
a12
；DATA XREF:Sub_12440+58+0
L0AD: 000000000002F90F
:DATA XREF:Sub_12440+BC+0
L0AD: 000000000002F90F
：sub_12440+C4t0
LOAD: 000000080002F92F
a202202
DCB "2022-02",0
DATA XREF:Sub_12440+EC+0
L0AD : 000000000002F937
aPersistSysDalv_0 DCB "persist.sys.dalvik.vm.lib",0
L0AD: 000000080002F937
；DATA XREF: Sub_12550+10t0
L0AD : 000000000002F937
LOAD:000000080802F951
aPersistSysDalv DCB “persist.sys.dalvik.vm.lib.2",0
：sub_12550+18+0
LOAD: 00eee0eeee02F951
DCB "art",0
：DATA XREF:Sub_12550:loc_125ACt0
L0AD: 000000080802F96D
aArt
DCB“RELEASE",0
；DATA XREF:Sub_12550:loc_125BCt0
L0AD : 000000000002F971
LOAD: 000000000002F971
aRelease
:DATA XREF: Sub_12600+38+0
；sub_12600+44+0
L0AD : 000600000002F979
aRb
DCB “rb",0
；sub_20700+20t0...
：DATA XREF:Sub_1D63C+34t0
LOAD: 000000000002F979
L0AD : 000000000002F97C
aNaop
DCB “NAOP",0
LOAD: 000000000002F981
aRoProductModel DCB “ro.product.model",θ
LOAD: 000000000002F981
；DATA XREF: Sub_12D9C+10t0
LOAD:00ee000eee02F981
：sub_12D9C+18+0
LOAD: 000000000002F992
aFireflyRk3399
DCB "Firefly-RK3399",0
；DATA XREF:Sub_12D9C+44t0
L0AD: 000000000002F9A1
L0AD: 000000000002F9A1
：DATA XREF:Sub_18FE4+590t0
L0AD: 000000000002F9A1
：sub_18FE4+598t0...
L0AD: 000000000002F9BC
aLandroidAppApp DCB“()Landroid/app/Application;",
aCurrentapplica DCB"currentApplication",0
L0AD: 000000000002F9CF
LOAD: 000000080802F9CF
；sub_18FE4+5F8t0...
：DATA XREF:Sub_18FE4+5E8t0
L0AD: 000000000002F9CF
LOAD:000000000002F9EB byte_2F9EB
DCB 0,0x4E,0x6F，0x77,0x2C
L0AD: 000000000002F9EB
；sub_12174+58+0...
：DATA XREF:Sub_12050+1C+0
LOAD: 008800080802F9EB
LOAD:eooeoeooooo2F9Fo aLinkerDoesNotS DCB"{Linker} does not support‘dlerror!",0
LOAD:000000000002FA18
aMonoImage0penF DCB “mono_image_open_from_data_with_nane",e
L0AD: 000000000002FA3C
alwsetinterpret DCB"hwSetInterpreter",
aLibmonoSo
DCB "libmono.so"，0
L0AD: 000000080802FA47
L0AD: 000000000002FA58
LOAD:008800000002FA58
；sub_12050+74to...
；DATA XREF:Sub_12EF8+30+0
L0AD : 000000000002FA58
LOAD:000000000002FA68
aNagalinker
DCB "NagaLinker",0
：
DATA XREF: JNI_0nLoad:loc_13AA6+0
L0AD: 000000000002FA68
JNI_OnLoad+5C+o
0002F8D2 000000000002F8D2: L0AD:aRoBuildVersion (Synchronized with Hex View-1)
```

我们可以找到`sub_123F0`，发现其调用了_system_property_get，调用系统属性获取函数，获取 SDK 版本，

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_DXVVAAZ2AQVZ9HB.webp)

```
23456
int64sub123F0()
_QW0RDv1[2];//[
[xsp+0h]
[xbp-20h]BYREF
v1[1]=*（_QWORD *)（_ReadStatusReg（TPIDR_EL0）+40);
v1[0]=0；
System_property_get("ro.build.version.sdk",vl);
return atoi（（const char *)vl);
```

查看`sub_123F0`的交叉引用，可以看到在init_proc中，`sub_123F0`被调用了，我们知道init_proc是一个很靠前的时间点，所以可以确认 **`__system_property_get`**就是一个很好的锚点，此时 SO 已经在内存中（基址已确定），但后续的检测线程还没来得及创建

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_JRJW5MPGUF9M92P.webp)

```
int64
sub_123F0()
_QW0RD v1[2];//[xSp+0h][xbp-20h] BYREF
v1[1]=*（_QWORD *)（_ReadStatusReg(TPIDR_EL0)+40);
v1[0]=0；
_system_property_get("ro.build.version.sdk",v1);
return atoi((const char *)v1);
xrefstosub_123F0
Direction
TyrAddress
Text
D...
init_proc:loc_14848
BL
sub_123F0
P
sub_1678C+64C
BL
sub_123F0
p
sub_1678C:loc_1774C
BL
sub_123F0
P
sub_18FE4+540
BL
sub_123F0
Line 1 of 4
Help
Search
Cancel
OK
```

所以我们给出下面的代码先做一个测试：

```js
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                            hook_system_property_get();         //在libmsaoaidsec.so进行初始化的时候hook
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
 
            }, onLeave: function (retval) {
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
 
function hook_JNI_OnLoad() {
    let module = Process.findModuleByName("libmsaoaidsec.so")
    Interceptor.attach(module.base.add(0x13A4C), {
        onEnter(args) {
            console.log("JNI_OnLoad")
        }
    })
}
 
function hook_system_property_get() {
    var system_property_get_addr = Module.findExportByName(null, "__system_property_get");
 
    if (system_property_get_addr !== null && system_property_get_addr !== undefined) {
        Interceptor.attach(system_property_get_addr, {
            onEnter: function (args) {
                var nameptr = args[0];
                if (nameptr) {
                    var name = ptr(nameptr).readCString();
                    if (name.indexOf("ro.build.version.sdk") >= 0) {
                        console.log("Found ro.build.version.sdk, need to patch");
                        //这里可以开始进行HOOK
                    }
                }
            }
        })
    }
 
}
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
可以看到结果打印了`Found ro.build.version.sdk, need to patch`，说明`hook_system_property_get()`在检测函数之前就执行了，说明我们的推断是合理的

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_GHNFHKJMGFCHH59.webp)

```
sakura@SakuradeMacBook-Pro~% frida-H 127.0.0.1:9898-f tv.danmaku.bili -1/Users/sakura/work/tools/android/unidbg-0.9.8/unidbg-android/src/test/java/com/Lession5/bilibili/1.js
/
Frida 16.2.1-Aworld-class dynamicinstrumentation toolkit
1(_11
>-1
Commands:
/_/1_1
help
->Displays the help system
object?->Display information about&#x27;object&#x27;
exit/quit->Exit
More info athttps://frida.re/docs/home/
Connected to127.0.0.1:9898 (id=socket@127.0.0.1:9898)
Spawningtv.danmaku.bili..
[*]Hooking android_dlopen_ext at libc.so!OxNaN
Spawnedtv.danmaku.bili`.Resuming main thread!
[dlopen] libframework-connectivity-tiramisu-jni.so
[Remote::tv.danmaku.bili]->[dlopen]libstats_jni.so
[dlopen]/system/framework/oat/arm64/com.android.future.usb.accessory.odex
[dlopen]/system/framework/oat/arm64/org.apache.http.legacy.odex
[dlopen]/data/app/~~z0sp1JUW5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/oat/arm64/base.odex
[dlopen]/data/app/~~z0sp1JUW5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libblkv.so
[dlopen]/data/app/~~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbytehook.so
[dlopen]/data/app/-~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA/lib/arm64/Libbili_core.so
[dlopen]/data/app/~~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbilicr.88.0.4324.188.s0
[dlopen]/data/app/~~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libijkffmpeg.so
[dlopen]/data/app/~~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriGl7Kxhy9yg6MCfGqA==/lib/arm64/libavif-jni.so
[dlopen]/data/app/~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbili.so
[dlopen]/data/user/0/tv.danmaku.bili/app_tribe/bundles/oaidkit/1693132100/libs/libmsaoaidsec.so
Found ro.build.version.sdk,need to patch
看雪
Process termtnated
[Remote::tv.danmaku.bili ]->
```

接下来就是去hook检测线程了，我们在这个锚点尝试hook `pthread_create`，打印出检测线程的地址

```js
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                            hook_system_property_get();
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
 
            }, onLeave: function (retval) {
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
 
function hook_JNI_OnLoad() {
    let module = Process.findModuleByName("libmsaoaidsec.so")
    Interceptor.attach(module.base.add(0x13A4C), {
        onEnter(args) {
            console.log("JNI_OnLoad")
        }
    })
}
 
function hook_pthread_create() {
    var pthread_create_addr = Module.findExportByName("libc.so", "pthread_create");
    console.log("pthread_create addr: ", pthread_create_addr);
    Interceptor.attach(pthread_create_addr, {
        onEnter: function (args) {
            var thread_func_addr = args[2];
            var module = Process.findModuleByAddress(thread_func_addr);
            console.log(`pthread_create thread func: ${module.name}+0x${(thread_func_addr - module.base).toString(16)}`);
        }, onLeave: function (retval) {
        }
    });
}
 
 
function hook_system_property_get() {
    var system_property_get_addr = Module.findExportByName(null, "__system_property_get");
 
    if (system_property_get_addr !== null && system_property_get_addr !== undefined) {
        Interceptor.attach(system_property_get_addr, {
            onEnter: function (args) {
                var nameptr = args[0];
                if (nameptr) {
                    var name = ptr(nameptr).readCString();
                    if (name.indexOf("ro.build.version.sdk") >= 0) {
                        console.log("Found ro.build.version.sdk, need to patch");
                        // hook_pthread_create();
                        // bypass()
                        //这里可以开始进行HOOK
                        hook_pthread_create();
                    }
                }
            }
        })
    }
 
}
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_Y9WS8K4AWUKRC87.webp)

```
sakura@SakuradeMacBook-Pro ~ % frida -H 127.0.0.1:9898 -f tv.danmaku.bili -1/Users/sakura/work/tools/android/unidbg-0.9.8/unidbg-android/src/test/java/com/lession5/bilibili/1.js
Frida 16.2.1-A world-class dynamic instrumentation toolkit
1（11
Commands:
//1_1
help
exit/quit ->Exit
More info at https://frida.re/docs/home/
Connected to 127.0.0.1:9898 (id=sockete127.0.0.1:9898)
Spawning`tv.danmaku.bili`...
[*] Hooking android_dlopen_ext at libc.so!exNaN
Spawned tv.danmaku.bili.Resuming main thread!
[Remote::tv.danmaku.bili ]->[dlopen] libframework-connectivity-tiramisu-jni.so
[dlopen]libstats_jni.so
[dlopen]/system/framework/oat/anm64/com.android. future.usb.accessory.odex
[dlopen]/system/framework/oat/arm64/org.apache.http.legacy.odex
[dlopen]/data/app/~z0sp1JUW5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/oat/arm64/base.odex
[dlopen]/data/app/~z0sp1JUw5Ezui.JvjCs015g==/tv.danmaku.biliw_VriG17Kxhy9yg6MCfGqA==/lib/arm64/Libb1kv.so
[dlopen]/data/app/z0sp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/Libbytehook.so
[dlopen]/data/app/~z0sp1JUWSEzui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/Libbili_core.so
[d1open]/data/app/-z0sp1JUW5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/1ibbilicr.88.0.4324.188.so
[dlopen]/data/app/~20sp1JUw5EzuiJvjCs015g/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA=/1ib/arm64/Libijkffmpeg.so
[dlopen]/data/app/z0sp1JUwSEzuiJvjCs015g==/tv.danmaku.biliw_VriG17Kxhy9yg6MCfGqA==/lib/arm64/Libavif-jni . so
[dlopen]/data/user/@/tv.danmaku.bili/app_tribe/bundles/oaidkit/169313210e/Libs/libmsaoaidsec.so
Found ro.build.version.sdk, need to patch
pthread create_oddr:Ox78484afa80
pthread_create thread func:1ibmsaoaidsec.so+0x1c544
pthread_create thread func:1ibart.so+0x53b314
pthread_create thread func: 1ibart.so+0x53b314
pthread_create thread func:libmsooaidsec.so+0x1b8d4
pthread_create thread func: 1ibart.so+0x53b314
pthread_create thread func: libmsaoaidsec.so+ex26e5c
pthreud_creute tlreud furc:tiburt.so+ox53b314
pthread_create thread func: 1ibart.so+0x53b314
pthread_create thread func: 1libart.so+0x53b314
pthread_create thread func: 1ibart.so+ex53b314
pthread_create thread func:1ibart.so+0x53b314
pthread_create thread func: 1ibart.so+0x53b314
pthread_create thread func:1ibart.so+@x53b314
Process terminated
[Remote::tv.danmaku.bili ]->
```

我们直接尝试去nop这些线程

```js
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                            hook_system_property_get();
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
 
            }, onLeave: function (retval) {
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
 
function hook_JNI_OnLoad() {
    let module = Process.findModuleByName("libmsaoaidsec.so")
    Interceptor.attach(module.base.add(0x13A4C), {
        onEnter(args) {
            console.log("JNI_OnLoad")
        }
    })
}
 
function hook_pthread_create() {
    var pthread_create_addr = Module.findExportByName("libc.so", "pthread_create");
    console.log("pthread_create addr: ", pthread_create_addr);
    Interceptor.attach(pthread_create_addr, {
        onEnter: function (args) {
            var thread_func_addr = args[2];
            var module = Process.findModuleByAddress(thread_func_addr);
            console.log(`pthread_create thread func: ${module.name}+0x${(thread_func_addr - module.base).toString(16)}`);
        }, onLeave: function (retval) {
        }
    });
}
 
function nopFunc(addr) {
    Memory.protect(addr, 4, 'rwx');  // 修改该地址的权限为可读可写
    var writer = new Arm64Writer(addr);
    writer.putRet();   // 直接将函数首条指令设置为ret指令
    writer.flush();    // 写入操作刷新到目标内存，使得写入的指令生效
    writer.dispose();  // 释放 Arm64Writer 使用的资源
    console.log("nop " + addr + " success");
}
function bypass_detect_func() {
    var base = Module.findBaseAddress("libmsaoaidsec.so")
    // jxbank
    nopFunc(base.add(0x1c544));
    nopFunc(base.add(0x1b8d4));
    nopFunc(base.add(0x26e5c));
}
 
 
function hook_system_property_get() {
    var system_property_get_addr = Module.findExportByName(null, "__system_property_get");
 
    if (system_property_get_addr !== null && system_property_get_addr !== undefined) {
        Interceptor.attach(system_property_get_addr, {
            onEnter: function (args) {
                var nameptr = args[0];
                if (nameptr) {
                    var name = ptr(nameptr).readCString();
                    if (name.indexOf("ro.build.version.sdk") >= 0) {
                        console.log("Found ro.build.version.sdk, need to patch");
                        // hook_pthread_create();
                        // bypass()
                        //这里可以开始进行HOOK
                        // hook_pthread_create();
                        bypass_detect_func();
                    }
                }
            }
        })
    }
 
}
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
frida已经不退出了

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_P6G6445NFARMTKC.webp)

```
[*] Hooking android_dlopen_ext at 1ibc.solexNoN
Spowned *tv.darmoku.bili*. Resumring moin threod!
[Remote::tv.darmaku.bi1t ]> [dlopen] 1ibframework-connectivity-tiramisu-jni .so
[dlopen] libstats_jni .so
[dlopen]
/system/framework/oot/anm6A/com,androi.d. future.usb .occessory .odex
17:09 
[dlopen]/system/framework/oat/anm64/org.apoche.http.legocy.odex
[dlopen]
[dopen]/dota/opp/2θsp13UM5Ezut.JvjCse15g/tv.donmoku.bi1i-=_VriG17Kxhy9yg6MCfGqA/1ib/orm64/1ibb1kv.so
/dota/opp/zesp1JUSEzui.JvjCs015g/tv.donoku.bi11m_VriG1.7Kxhy9yg6MfGqA/oot/orm64/base.odex
登录
一次性手机
[dlopen]/dota/opp/~20sp1JUM5Ezuf.JvjCse15g=/tv.daonmoku.bi1i-=_VriG17Kxhy9yg6MCfGqA=/1 ib/arm64/1.ibbytehook. so
[dopen]/dota/opp/20sp13U5EzuivjCse15g/tv.danmcku.bi1i-VriG17Kxhy9yg6MfGqA/ib/orm64/Libbi1i_core.so
[dlopen]/dota/opp/~z0sp13UM5Ezui.JvjCse15g=/tv.donmoku.bi1i-=_VriG17Kxhy9yg6MCfGqA=/1ib/arm64/1ibbi1icr.88.0.4324.188.so
直播
推荐
热门
动画
影视
新征程
[dlopen]/dta/opp/zsp13U5EzuvjCs15g/tv.dmkubi1VriG17Kxhy9yg6MCfGA/b/om6/bijffeeg.so
[dopen]/dta/opp/zesp13U5EzuvjCs15g/tv.donmkub1-_VriG17Kxy9yg6MCfGA/b/om64/bvif-jiso
慕尼黑会议
[dloptn]/
[dlop]/data/user//tv.donmoku.bi1i/opp_tribe/bundles/ooidkit/16931321ee/ibs/libmsoidsec.so
/dota/opp/IgN3vCh5CX8qnzGefE1uuQ/com.google.androtd.webview-fMCqKrDChmzLVUMxTVw/oat/anmfA/WiebVienfGoogle.odex
难道要三战了吗
Found ro.buri1d.version.sdk, need to potch
nop ex6bfd42e544 success
nop ex6bfd42d8d4 success
nop ex6bfd438e5c success
[dlopen]/dota/opp/~z0sp1JUmSEzut.JvjCse15g=/tv.daonmoku .bi1i -=_VriG17Kxhy9yg6MCfGqA=/L ib/arm64/1 ibbreflect so
[dlopen] libmonochrome so
[dlopen]
[dlopen]
/dota/opp/~zesp13UMSEzui.JvjCse15g/tv.donmoku.bi11_VriG17Kxhy9yg6MCfGqA=/1ib/arm64/1Libbi11.so
/dota/opp/2sp13UM5EzuiJvCse15g/tv.danmku.bi1VriG17Kxhy9yg6MCfGgA/ib/orm64/1ibBuglyso
[udoP]
/dota/opp/20sp13UM5Ezuf.JvjCse15g/tv.danmoku.bi1i=_VriG17Kxhy9yg6MCfGqA/Lib/am64/1ibc++-_shared.so
68.4万
[dlopen] /dota/user/e/tv.donmcku.bi1i/opp_tribe/bundles/ooidkit/16931321ee/libs/libmsooi.douth.so
前
Found ro.buri 1d.version.sdk, need to potch
nop ex6bd4fo4544 success
nop ex6bd4fa38d4 success
Hytale和Minecraft 跨平台联机演示视频
nop ex6bd4foee5c success
[dl.open]/dota/user/@/tv.danmcku.bi1i/opp_tribe/bundles/apm/146840e5ee/1ibs/1ibkoom-jova.so
nop ex6bd4fo4544 success
Found ro.build.version.sdk, need to potch
nop @x6bd4fa38d4 success
nop @x6bd4foee5c success
[dlopen]/dota/opp/IgN3vChSCX8qkzGefE1uQ=/com.google. android.webview-fMmCcKrDChwmnzLVUMTVw/WiebVienGoogle.apk!/Lib/anm64v8a/Libmonochrome.s0
[dlopen]/system/ib64/1ibwebviewchronium_plat_support.so
[dlopen]/dota/pp/zesp13UEzuivjCse15g/tv.donmckubi11_VriG17Kxhy9yg6MCfGqA/b/erm64/ibi11idso
正在视频
Found ro.bui1d.version.sdk, need to potch
44
0:25
21.万
nop @x6bd4fo4544 success
等爷不泡茶以捏为卖点奶茶被
nop ex6bd4fa38d4 success
b站审核员吓笑了
nop ex6bd4fee5c success
多位顾客捏爆
Found ro.burild.version.sdk, need to patch
nop ex6bd4fo4544 success
竖暴正在新闻
nop @x6bd4fa38d4 success
[dopen]/dota/opp/zesp13U5EzuvjCse15g/tv.donmcku.bi1i_VriG17Kxy9yg6MCfGgA/ib/om64/ibodjniso
nop ex6bd4foee5c success
[dl.open]/dota/opp/2esp13USEzu.vjCse15g/tv.donmcku.bi11-m_VriG17Kxy9yg6MCfGqA/b/arm64/bimogepipeline.so
Found ro.build.version.sdk,need to patch
64位边境
nop @x6bd4fo4544 success
nop 0x6bd4fa38d4 success
nop ex6bd4foee5c success
[dopn] /vendor/Lib64/hw/android.hordsre.graphics.mopperl4.0inp1.so
1.2万
Found ro.burild.version,sdk, need to potch
3
nop @x6bd4fo4544 success
边境之墙竟然从来被移除？
座默南理工巨婴
干活分工
nop @x6bd4fa38d4 success
nop ex6bd4foee5c success
[dl.open] /vendor/1 ib64/hm/androi.d.,hardsgre. graphi.cs.mopper84.0inp1 so
+
[udop]
/vendor/Lib64/hm/android,hordngre.graphi.cs.mopperl4.8inp1.so
首
关注
会员约
[dlopen]
/dota/opp/~2sp13U5EzuiJvjCse15g/tv.danmkubim_VriG17Kxhy9ygMCfGqA/ib/arm64/1ibchronoss0
/vendor/1ib64/egl/LibGLES_nolf.so
[dl.open]/dota/app/~zesp1JUmSEzui.JvjCse15g=/tv.donmoku.bi1i=_VriG17Kxhy9yg6MCfGqA==/Lib/arm64/Libnirvona.so
[dlopen]
```

## 其他锚点

这种寻找锚点的方式，不只可以使用`__system_property_get`作为我们的锚点，还可以使用其他的函数，这边使用gemini找了几个可以尝试作为锚点的函数

**锚点函数** | **推荐指数** | **触发时机** | **适用场景**  
---|---|---|---  
**__system_property_get** | ⭐⭐⭐⭐⭐ | 极早 | 几乎所有加固都会读取`ro.build.version`或厂商信息  
**dlsym** | ⭐⭐⭐⭐⭐ | 极早 | 壳需要隐藏 API 调用时（如隐藏`ptrace`等）  
**prctl** | ⭐⭐⭐ | 较早 | 防止 Dump 或 允许 Ptrace 时  
  
### **dlsym**

首先我们在IDA中看dlsym是否在init_proc阶段被调用：找到dlsym，查看它的引用

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_FRYNYHS2FW4539T.webp)

```
//attributes:thunk
void *dlsym（void *handle，const char *name）
returnoff_47c90(handle,name);
xrefs to dlsym
Direction
TyrAddress
Text
D...
P
sub_9150+1FC
BL
dlsym
p
SUb_11A6U+394
BL
aisym
p
sub_1B380+268
BL
dlsym
p
sub_1B924+3F4
BL
dlsym
p
sub_1CEF8+224
dlsym
P
sub_2701C+4A4
dlsym
Line 1 of 6
Help
Search
Cancel
OK
```

定位到`sub_9150`，继续查看引用，可以发现在init_proc中被调用了，所以在初始化阶段确实存在dlsym

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_WWCQ6P8SMMKWKZY.webp)

```
void
sub_9150()
xrefstosub_9150
Direction
Tyr Address
Text
D.
init_proc:loc_14740
BL
sub_9150
Line 1 of 1
Help
Search
Cancel
OK
```

SO 加壳或做对抗时，为了隐藏导入表，往往会通过 `dlopen`/`dlsym` 动态获取系统函数地址（如 `ptrace`, `open`, `pthread_create`）。**特征参数** ：第二个参数是**函数名称字符串，这里编写如下的代码，测试是否会动态导入**`pthread_create`

```js
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                            // hook_system_property_get();
                            // hook_prctl_anchor()
                            hook_dlsym_anchor();
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
 
            }, onLeave: function (retval) {
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
function hook_dlsym_anchor() {
    const dlsym_addr = Module.findExportByName(null, "dlsym");
    if (dlsym_addr) {
        Interceptor.attach(dlsym_addr, {
            onEnter: function (args) {
                this.symbolName = args[1].readCString();
                // 监听壳是否在动态获取pthread_create
                if (this.symbolName && (this.symbolName.indexOf("pthread_create") >= 0)) {
 
                    console.log("[Anchor] dlsym finding: " + this.symbolName);
                    // 触发核心 Bypass 逻辑
                    // bypass_detect_func();
                }
            }
        });
    }
}
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
可以看到打印了[Anchor] dlsym finding: pthread_create

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_E6GKW6HAJHRD6SM.webp)

```
sakura@SakuradeMacBook-Pro ~% frida -H 127.0.0.1:9898 -f tv.danmaku.bili -1/Users/sakura/work/tools/android/unidbg-0.9.8/unidbg-android/src/test/java/com/lession5/bilibili/1.js
Frida 16.2.1-Aworld-class dynamicinstrumentation toolkit
1(_11
>-1
Commands:
/_/1_1
help
->Displays the helpsystem
object?->Displayinformation about&#x27;object&#x27;
exit/quit->Exit
More info at https://frida.re/docs/home/
Connected to 127.0.0.1:9898 (id=socket@127.0.0.1:9898)
Spawningtv.danmaku.bili...
[*] Hooking android_dlopen_ext at libc.so!OxNaN
Spawnedtv.danmaku.bili.Resumingmain thread!
[dlopen]libframework-connectivity-tiramisu-jni.so
[Remote::tv.danmaku.bili ]-> [dlopen] libstats_jni.so
[dlopen]/system/framework/oat/arm64/com.android.future.usb.accessory.odex
[dlopen]/system/framework/oat/arm64/org.apache.http.legacy.odex
[dlopen]/data/app/~~z0sp1JUW5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/oat/arm64/base.odex
[dlopen]/data/app/-~z0sp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriGl7Kxhy9yg6MCfGqA==/lib/arm64/libblkv.so
[dlopen]/data/app/~~z0sp1Juw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbytehook.so
[dlopen]/data/app/~~z0sp1JUW5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/Lib/arm64/Libbili_core.so
[dlopen]/data/app/~z0sp1JUw5Ezui.JvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbilicr.88.0.4324.188.so
[dlopen]/data/app/~~z0sp1JUw5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/Lib/arm64/Libavif-jni.so
[dlopen]/data/app/~~z0sp1JUW5EzuiJvjCs015g==/tv.danmaku.bili-w_VriG17Kxhy9yg6MCfGqA==/lib/arm64/libbili.so
[dlopen]/data/user/0/tv.danmaku.bili/app_tribe/bundles/oaidkit/1693132100/Libs/libmsaoaidsec.s0
[Anchor] dlsym finding:pthread_create
[Anchor] dlsym finding: pthread_create
看雪
Process terminated
[Remote::tv.danmaku.bili ]->
```

我们在这个锚点执行我们的bypass方法

```js
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                            // hook_system_property_get();
                            // hook_prctl_anchor()
                            hook_dlsym_anchor();
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
 
            }, onLeave: function (retval) {
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
function hook_dlsym_anchor() {
    const dlsym_addr = Module.findExportByName(null, "dlsym");
    if (dlsym_addr) {
        Interceptor.attach(dlsym_addr, {
            onEnter: function (args) {
                this.symbolName = args[1].readCString();
                // 监听壳是否在动态获取pthread_create
                if (this.symbolName && (this.symbolName.indexOf("pthread_create") >= 0)) {
 
                    console.log("[Anchor] dlsym finding: " + this.symbolName);
                    // 触发核心 Bypass 逻辑
                    bypass_detect_func();
                }
            }
        });
    }
}
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
可以看到已经成功绕过，frida未退出

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_UU7WP25UKS8VWJ7.webp)

```
sokurolSakurodeMocBook-Pro ~ % fri.da -H 127.0.0.1:9898 -f tv.dormaku.bi1t -1 /Users/sokura/mork/too1s/android/unidbg-0.9.8/unidbg-android/src/test/java/com/lession5/bilibi1i/1. js
1 (11
Frida 16.2.1 - A world-class dynamic instrumentation toolkit
/_/1_1
Commonds:
help
> Displays the help system
09:28 
object?
-> Display informotion about &#x27;object*
exit/quit -> Exit
叠录
Q复测玄戒o1上市时的问题
More info at https:/frido.re/docs/home/
Connected to 127.0.0.1:9898 (id=s0ckete127.0.0.1:9898)
直播
推荐
热门
影视
新征程
三
[*] Hooking android_dlopen_ext ot 1ibc solexNoN
Sponed “tv.donmoku.bili*. Resumring moin thread!
[Remote::tv.donmoku.bi1i ]> [dlopen] 1ibframework-conmectivity-tiramisu-jni so
[dlepen] libstats_jni.so
[dliopen] /system/framework/oot/anm64/com.android. future.usb.occessory -odex
[dl.open]/system/framework/oot/arm64/org.opoche.http.1egocy.odex
[dlpen]/data/opp/zsp1JUSEzuL3vjCs15/tv.dnmku.bi1w_VriG17Kxhy9yg6MfGgA/ot/erm64/bse.odex
[dpen]/data/opp/2sp1JUWSEzuvjCse15g/tv.donmkubi1w_VriG17Kxy9yg6MCfGA/b/arm64/b1kvso
[dopen]/data/app/~2sp1JUSEzui.JvjCse15g/tv.donmgku.bi1w_VriG17Kxy9yg6MfGqA/tb/arm64/Libbytehook.so
[dlopen]/dat/opp/~2esp1JUSEzuivjCs015g/tv.donmdku.bi1iw_VriG17Kxy9yg6MfGqA/lib/arm64/1ibbii_core.so
[dlcopen]/dota/opp/-2esp1JUWSEzuri.3vjCse15g-/tv.donmoku.bi1i-w_VriG17Kxhy9yg6MCfGgAmm/lib/orm64/Libbilicr.88.0.4324.188.so0
[dlopen]
/data/pp/esp1JUWSEzuiJvjCs15g/tv.dnmku.bi1_VriG17Kxy9yg6MCfGgA/1ib/er64/1ibjkffpeg.so
[dlopen]/dota/app/zesp1JUwSEzuiJvjCse15g-/tv.donmoku.bi1i-w_VriG17Kxhy9yg6MCfGqA-/1ib/arm64/Libovif-jni.so
2518万990
[dlepen]/data/user/@/tv.denmoku.bi1i/app_tribe/bundles/ooidkit/169313210e/libs/Libmsoidsec.s0
[Anchor] dlsym finding: pthreod_creote
nop ex7a5df31544 success
种了一辈子地，穷怕了
nop ex7a5df388d4 success
nop ex7a5df3be5c success
[Anchor] dlsym finding: pthreod_creote
nop ex7a5df31544 success
nop ex7a5df388d4 success
:
nop ex7a5df3be5c success
[Anchor] dlsym finding: pthreod_creote
不
！
nop ex7a5df31544 success
nop ex7a5df388d4 success
0
加入北晶
nop ex7a5df3be5c success
[dl.cpen]/data/opp/-IgN3vCh5OX8gyzGefE1luQ/com.google.android.webview-fMmCcgkrDChmnzLVUMsTVw/ot/orm64/lebVienGoogle.odex
[dopen] /data/opp/2sp1JUWSEzuiLJvjCse15g/tv.donmaku.bi1iw_VriG17Kxhy9yg6MCfGq/1ib/arm64/1ibbi1 i.so
网易MC服主消费观belike:
2009年8月9号
[dopen] /data/app/~2sp1JUWSEzu.vjCse15g/tv.donmdkubi1m_VriG17Kxy9yg6MCfGA/ib/erm64/breflect s0
屏九一黄主任
[dlopen] libmonochrome.so
[dlopen]/data/opp/2sp1JUSEzuiJvjCse15g/tv.donmdku.bi1im_VriG17Kxhy9yg6MfGgA/ib/orm64/Libugly.so
[dlcpen] /data/opp/zesp1JUSEzuL3vjCse15g/tv.donmoku.bi11w_VriG17Kxhy9ygMCfGqA/ib/orm64/1ib++_shored.so
冻龄药水
[dopen]/dota/user/@/tv.donmaku.bili/app_tribe/bundles/ooidkit/16931321ee/Libs/libmsoooidouth.so
[Anchor] dlsym finding: pthreod_creote
青春永驻
nop ex7a21f95544 success
nop ex7a21f948d4 success
nop ex7a21f9fe5c success
[dlcpen] /data/opp/IgN3vCh5OX8gqkzGefE1uQ-/com.google. android.webview-fMeCcgkrDChemnzLVUMsTVw/lebvienGoogle.apk1/Lib/arm64-v8a/ ibmonochrome.so0
[dlcpen]/data/user/0/tv.donmaku.bi1/opp_tribe/bundles/apm/1468405ee/ibs/Libkoom-jovo.50
44.1万552
437
11.1万
1231
2:52
[dlopen]/dat/opp/~2sp1J5EzuvjCs015g/tv.donmku.bi11w_VriG17Kxhy9ygMfGgA/ib/arm64/Libi1id.so
[dlopen] /system/1 ib64/Libwebviewchromium_plat_support.so
世界最薄的纸，究竟能被对折
新植物金蒲公英=冻龄药水！
[dlcpen]/data/opp/2esp1JUSEzuL3vjCs15g/tv.donmaku.bi11w_VriG17Kxhy9yg6MCfGgA/b/orm64/1bodjni.so
多少次？
幼年生物现可永葆青春！26.
[dpen] /data/opp/2sp1JUWSEzuL.vjCse15/tv.donmku.bi1w_VriG17Kxhy9yg6MCfGqA/tb/erm64/tbimogepipeline.so
[dlopen] /vendor/L ib64/hw/android.hardwore.graphi.cs.mapper@4.0-inp1 . so
[dlopen] /vendor/L ib64/hw/android.hardwore graphi.cs.mopper@4.0-imp1 . so
[dlopen] /vendor/L ib64/hw/ndroid.hardwore.graphi.cs.moppere4 .0-inpl .so
关注
会员购
成的
[dlcpen]/data/opp/~zesp1JUwSEzui.JvjCse15g=/tv.donmoku.bi1iw_VriG1.7Kxhy9yg6MC fGqA=/1 ib/arm64/1 ibnirvono.so
[dopen] /data/app/~zsp1JWSEzui.JvjCse15g/tv.donmdku.bi1 m_VriG17Kxy9yg6MCfGqA/b/erm64/Libchronos.so
[dlepen]/
/vendor/1ib64/egl/1ibGLES_moli.so
```

### **prctl**

这里验证了**`prctl`**，IDA中的调用路径如下：

通过function中搜索函数，定位到函数

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_9YSP9UYH94FS3EQ.webp)

```
Functions
IDA View-A
X
Pseudocode-C
Ps
 2 3 4 5
attributes: thunk
Function name
int prctl（int option，..
prctl
imp_prctl
returnoff_47E50(option);
```

开始查看引用，找到`sub_1B144`

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_D3RXPHG5GP7HZWW.webp)

```
xrefs to prctl
Direction
Tyr Address
Text
D...
sub_1B144+178
BL
prctl
sub_1B380+54
prctl
sub_25CD4+84
prctl
sub_25CD4+9C
prctl
Line 1 of 4
Help
Search
Cancel
OK
```

查看`sub_1B144`的引用，找到了`sub_1B380`：

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_ZW4UMY37KPWCTE3.webp)

```
xrefs to sub_1B144
Direction
TyF
Address
Text
D...
P
sub_1B380+2D0
BL
sub_1B144
Line 1 of 1
Help
Search
Cancel
OK
```

继续查看引用，找到`sub_1B924`：

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_A95PDQP2UWXHFD8.webp)

```
xrefstosub_1B380
Direction
TyF
Address
Text
D...
P
sub_1B924+1F8
BL
sub_1B380
Line 1 of 1
Help
Search
Cancel
OK
```

继续往上跟进，找到`sub_1BEC4`：

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_QXUQ766TYZAX636.webp)

```
xrefstosub_1B924
Direction
Typ
Address
Text
D...
P
sub_1BEC4:loc_1BF78
BL
sub_1B924
Line 1 of 1
Help
Search
Cancel
OK
```

发现在init_proc中调用了`sub_1BEC4`，说明了`prctl`确实是在初始化阶段被调用了

![图片描述](https://bbs.kanxue.com/upload/attach/202601/1041753_9KUNG3JQCMR6SGE.webp)

```
xrefs tosub_1BEC4
Direction
TyF
Address
Text
Upp.init_proc+220
BL
sub_1BEC4
Line 1 of 1
Help
Search
Cancel
OK
```

具体代码如下：

```js
function hook_dlopen() {
    const funcName = "android_dlopen_ext";
    const libc = Module.findBaseAddress("libc.so");
    var funcPtr = Module.findExportByName(null, funcName);
 
    if (funcPtr !== null && funcPtr !== undefined) {
        console.log(`[*] Hooking ${funcName} at libc.so!0x${(funcPtr - libc.base).toString(16)}`);
 
        Interceptor.attach(funcPtr, {
            onEnter: function (args) {
                this.pathPtr = args[0];
                if (this.pathPtr !== null && this.pathPtr !== undefined) {
                    try {
                        // 读取加载的so名称字符串并打印
                        var path = this.pathPtr.readCString();
                        console.log("\x1b[36m[dlopen] \x1b[0m" + path);
                        if (path.indexOf("libmsaoaidsec.so") !== -1) {
                            this.isTarget = true;
                            // hook_system_property_get();
                            hook_prctl_anchor()
                        }
                    } catch (e) {
                        console.log("[!] Error reading path string in " + this.funcName);
                    }
                }
 
            }, onLeave: function (retval) {
            }
        });
    } else {
        console.log("[-] Warning: " + funcName + " not found in exports.");
    }
}
 
 
function hook_pthread_create() {
    var pthread_create_addr = Module.findExportByName("libc.so", "pthread_create");
    console.log("pthread_create addr: ", pthread_create_addr);
    Interceptor.attach(pthread_create_addr, {
        onEnter: function (args) {
            var thread_func_addr = args[2];
            var module = Process.findModuleByAddress(thread_func_addr);
            console.log(`pthread_create thread func: ${module.name}+0x${(thread_func_addr - module.base).toString(16)}`);
        }, onLeave: function (retval) {
        }
    });
}
 
function nopFunc(addr) {
    Memory.protect(addr, 4, 'rwx');  // 修改该地址的权限为可读可写
    var writer = new Arm64Writer(addr);
    writer.putRet();   // 直接将函数首条指令设置为ret指令
    writer.flush();    // 写入操作刷新到目标内存，使得写入的指令生效
    writer.dispose();  // 释放 Arm64Writer 使用的资源
    console.log("nop " + addr + " success");
}
function bypass_detect_func() {
    var base = Module.findBaseAddress("libmsaoaidsec.so")
    // jxbank
    nopFunc(base.add(0x1c544));
    nopFunc(base.add(0x1b8d4));
    nopFunc(base.add(0x26e5c));
}
 
 
function hook_prctl_anchor() {
    const prctl_ptr = Module.findExportByName(null, "prctl");
    const PR_SET_DUMPABLE = 4;
 
    if (prctl_ptr) {
        Interceptor.attach(prctl_ptr, {
            onEnter: function (args) {
                const option = args[0].toInt32();
                // 锚点：检测到尝试禁止内存 dump
                if (option === 15) {
                    console.log(`[Anchor] prctl(PR_SET_DUMPABLE) detected!`);
                    bypass_detect_func();
                }
            }
        });
    }
}
 
function main() {
    hook_dlopen();
}
setImmediate(main);
```  
---  
  
但是存在一个问题，这个锚点虽然能执行`bypass_detect_func`，但是实测下来无法执行`hook_pthread_create()`

## 其他绕过方法

这时候就有兄弟要问了，有没有更轮椅的方法，有的兄弟，有的

只需要去下载一个[florida](https://bbs.kanxue.com/elink@5faK9s2c8@1M7s2y4Q4x3@1q4Q4x3V1k6Q4x3V1k6Y4K9i4c8Z5N6h3u0Q4x3X3g2U0L8$3#2Q4x3V1k6k6L8r3q4J5L8$3c8Q4x3V1k6r3L8r3!0J5K9h3c8S2i4K6u0r3M7X3g2D9k6h3q4K6k6i4x3`.) 就可以一键绕过检测了

下载对应的版本，直接替换原本的frida-server即可。

## 参考文章

[绕过最新版bilibili app反frida机制](https://bbs.kanxue.com/thread-281584.htm)  
[[原创]经典 Frida 检测 libmsaoaidsec.so 绕过](https://bbs.kanxue.com/thread-289359.htm)  
[[原创]某加固新版frida检测绕过-trace一把嗦](https://bbs.kanxue.com/thread-289545.htm)  
[[原创] bilibili frida检测分析绕过](https://bbs.kanxue.com/thread-285893.htm)

**小白第一次发帖，可能分析和描述中存在错漏，望大佬指点~**

  

[传播安全知识、拓宽行业人脉——看雪讲师团队等你加入！](https://bbs.kanxue.com/thread-275828.htm)

[#HOOK注入](https://bbs.kanxue.com/forum-161-1-125.htm)
