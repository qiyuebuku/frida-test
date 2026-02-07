# 文档合集

> 共 2 篇文档
> 生成时间: 2026-02-04 20:01:42

## 目录

1. [[原创]某右APP分析第一弹：过检测-Android安全](#doc-1)
2. [[原创]无壳app的libmsaoaidsec.so frida反调试绕过姿势-Android安全](#doc-2)（被文档1引用）



---

<a id="doc-1"></a>

## 1. [原创]某右APP分析第一弹：过检测-Android安全

> 来源: https://bbs.kanxue.com/thread-287922.htm

# [原创]某右APP分析第一弹：过检测-Android安全

# 前言

原本是想等分析完再一起发的，但是想了想检测部分还是单独发出来吧，这个APP的检测难度对我来说是相当大的(之前没有具体的分析过检测)，记录一下

# 分析

首先正常附加frida，启动，退出，一气呵成
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_5V289WX5896K5F5.webp)

```
Frida 14.2.18 -Aworld-class dynamic instrumentation toolkit
1（-1
Commands:
/-/1-1
help
->Displaysthehelp system
object?
->Display information about'object
exit/quit->Exit
Moreinfo athttps://frida.re/docs/home/
Spawned cn.xiaochuankeji.tieba.Resuming main thread!
看雪
[AoSP on blueline::cn.xiaochuankeji.tieba]->Process terminated
[AosPonblueline::cn.xiaochuankeji.tieba]->
```


hook 一下dlopen，打印一下加载的so，然后可以发现了一个很熟悉的so(上次研究到一半的某游戏盒也是这个)，libmsaoaidsec.so
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_3K3RK6WTEZEZJBN.webp)

```
functionhook_dlopen（）：void{显示用法
//Android8.8之后加载so通过android_dLopen_ext@数
Var android_dlopen_ext : NativePointer = Module.findExportByName( moduleName: null, exportName "android_dlopen_ext");
10
console.log("addr_android_dlopen_ext",android_dlopen_ext);
11
Interceptor.attach(android_dlopen_ext, callbacksOrProbe: {
12
onEnter:function(args : InvocationArguments ) :void {
13
var pathptr :NativePointer = args[0];
if(pathptr!=null && pathptr != undefined){
15
16
console.log("android_dLopen_ext:",path);
18
19
onLeave:function(retvel :InvocationReturnValue ) :void {
20
console.log("leave!");
21
H)
23
hook,dlopen0
onEnter0
终端
本地
本地（2）×
本地（5）
本地（3）x
android_dlopen_ext:/data/app/cn.xiaochuankeji.tieba-KPdzys8RtTQghACl3zaiyQ==/Lib/arn/Libmarsxlog.so
Leave!
android_dlopen_ext:/data/app/cn.xiaochuankeji.tieba-KPdzys8RtTQghACl3zaiyQ==/Lib/arn/LibBugly-ext.so
Leave!
android_dlopen_ext:/data/app/cn.xiaochuankeji.tieba-KPdzys8RtTQghAcl3zaiyQ==/lib/arn/Libxcrash.so
leave!
android_dlopen_ext: /data/app/cn.xiaochuankeji.tieba-KPdzys8RtTQghACl3zaiyQ==/Lib/arn/Libmsaoaidsec.so
Process terninated
看雪
[AoSP on blueline::cn.xiaochuankeji.tieba]->
Thank you for using Frida!
```


一开始以为很简单，毕竟已经有很多前辈研究过了，使用前辈的笔记代码过掉他就行了，【地址：https://bbs.kanxue.com/thread-285811.htm】（> 参见本文档：[无壳app的libmsaoaidsec.so frida反调试绕过姿势](#doc-2)），没想到，还是我太天真了，直接就运行报错，地址找不到？
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_WW56349T49VVX75.webp)

```
Error:already replaced this function
at value （frida/runtime/core.js:322)
at onEnter（/hook2.js:47)
Error: already replaced this function
at value （frida/runtime/core.js:322)
at onEnter（/hook2.js:47)
hooked call_constructors
Error: already replaced this function
at value （frida/runtime/core.js:322)
at onEnter （/hook2.js:47)
hooked call_constructors
Error: already replaced this function
at value （frida/runtime/core.js:322)
淼看雪
at onEnter（/hook2.js:47)
Process terminated
```


咱们hook一下 create 进行打印看看调用的线程，这次线程出来了
address libmsaoaidsec.so 0x127f1
address libmsaoaidsec.so 0x11e1d
address libmsaoaidsec.so 0x19c09
咦，没想到这个so的地址居然变更了，没关系，咱们手动替换一下，运行，，但是五秒不到，还是直接闪退
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_DU3Z5J64UUCRD3Q.webp)

```
Spawned
cn.xiaochuankeji.tieba.Resuming
mainthread:
[A0SP on blueline::cn.xiaochuankeji.tieba]-> android_dlopen_ext:/data/app/cn.xiaochuankeji.tieba-KPdzys8RtTQghACl3zaiyQ==/lib/arm/Libmsaoaidsec.so
[pth_create]0xf2b1b081
address libmsaoaidsec.so0x127f1
address libmsaoaidsec.so 0x11e1d
addresslibmsaoaidsec.so0x19c09
Process terminated
[AoSPonblueline::cn.xiaochuankeji.tieba]->
```


![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_8PPBGR7XY25HGEH.webp)

```
Spawned`cn.xiaochuankeji.tieba'.Resuming main thread!
[A0SP on blueline::cn.xiaochuankeji.tieba]->android_dlopen_ext:/data/app/cn.xiaochuankeji.tieba-KPdzys8RtTQghACl3zaiyQ==/lib/arm/libmsaoaidsec.so
0xf5da4915
hookedcall_constructors
8x127f1：智换成功
0x11e1d：替换成功
0x19c09：管换成功
Processterminated
[AosPon blueline::cn.xiaochuankeji.tieba]->
```


打开IDA分析一下，首先进去就感觉不对劲，地址居然是0x127F0,和打印出来的线程对不上，查了一下(另外两个也是一样)
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_CWU85BDR22M3KJV.webp)

```
LOAD:000127F0;Attributes:noreturn
L0AD:000127F0
L0AD:000127F0
；void
fastcall
noreturn
sub_127F0(int)
L0AD:000127F0
sub_127F0
;DATA XREF: sub_12AC0+1D2↓o
L0AD:000127F0
；sub_12AC0+1D4↓o
L0AD:000127F0
L0AD:000127F0
var_1c
-0x1C
L0AD:000127F0
var_18
-0x18
L0AD:000127F0
L0AD:000127F0
PUSH
{R4-R7,LR}
L0AD:000127F2
SUB
SP，SP，#OxC
LOAD:000127F4
MOVS
R4,R0
L0AD:000127F6
LDR
R5,=(s_- 0x127FC)
L0AD:000127F8
ADD
R5，PC
```


![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_ZHS6WESJSTJ2CXY.webp)

```
Q为什么差了一位？
看雪
首页 课程问答
CTF
社区
招聘
峰会
发现
社区关键词回车
Q
ARM处理器有两种指令模式：
·ARM模式-指令长度4字节，功能强大但占空间
·Thumb模式-指令长度2字节，节省空间但功能相对简单
地址的"标签"机制
ARM处理器用地址的最后一位来标记指令类型：
地址最后一位=日→ARM指令模式
地址最后一位=1→Thumb指令模式
你的具体情况
Frida显示：0x127f1
（最后一位=1，意思是"这里是Thumb指令")
IDA显示：0X127F0
(实际存储代码的位置)
类比理解
就像门牌号和门铃按钮：
·0x127F0=房子的门牌号（实际位置）
·0x127f1=门铃按钮位置（门牌号+1，告诉你这是特殊类型的房子）
处理器的工作流程
1.看到地址0x127f1
2."哦，最后一位是1，这是Thumb代码"
3.去掉标志位：0x127f1→0x127Fθ
4.跳专到日x127F0执行Thumb指令
自总结
这不是错误，而是ARM的设计特色！
·0x127f1=带"标签"的地址（告诉处理器指令类型）
·0x127F0=真正的代码存放位置
就像你写信时，信封上写的地址可能比实际门牌号多个标记，但邮递员知道怎么处理！
米
凸
52
Retry
```


看来貌似是我hook错了，全部减一位，然后
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_7YRHYWB8QM4MM92.webp)

```
nooked call_constructors
Error:unable to intercept function at 0xbf2d1e1c;please file abug
at value （frida/runtime/core.js:322)
atonEnter（/hook2.js:50)
nooked call_constructors
Error:alreadyreplaced this function
at value（frida/runtime/core.js:322)
at onEnter(/hook2.js:47)hookedcall_constructors
nooked call_constructors
Error:alreadyreplaced this function
atvalue（frida/runtime/core.js:322)
atonEnter(/hook2.js:47)
榮看雪
Error:alreadyreplaced this function
```


一样报错，看来直接替换掉线程是有问题的了，那接下来我的思路就分为了两个
一：找找修改的frida或者模块以及其他的办法来绕过检测
二：直接硬刚，深入分析检查点进行处理
我首先选择的是绕过检测，毕竟之前都没有研究过，没有底，所以打算去网上找找看有没有什么过检测的办法

# 过检测

## 绕过检测

### 使用模块绕过

首先在b站找到了一个大佬发布的模块，看视频测试效果测试挺不错的，是能绕过大部分的frida检测，【地址：https://github.com/sucsand/sucsand】。
我个人按照步骤折腾了好久，两部手机都没有实现效果，不晓得啥原因，差点还折腾坏了一部手机，让我朋友测试是正常的(有点不稳定)，但是也是能够绕过的，各位有兴趣的可以试试。
PS：这个模块是作者发布在b站的，我感觉不错就放在这里了，如果对作者造成影响或者有什么不妥的话，请联系我进行删除
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_ZZ2XWU4CXX5X6CF.webp)

```
Reportrepository
ZygiskFridaGadget模块使用指南
Releases
一个基于Zygisk的Frida注入模块，绕过传统frida-server的检测方式，仅支持arm64
v1.0.6
Latest
2 weeks ago
Inject gadget.so into target app via Zygisk. Only supports arm64
Packages
功能简介
No packages published
本模块可通过注入gadget.so实现Frida脚本注入，避免frida-server进程被直接识别或检测。
适用于已Root的Android设备，要求已启用Magisk或KermelSU的Zygisk环境。
视频教程：点击观看
微信公众号：https://mp.weixin.qq.com/s/GMfiT2SkX9kyeEPouDrFKg
```

### 魔改frida绕过

正在折腾上面的sucsand的时候我朋友给我发来了一个大佬魔改过的frida
[frida16.2.1 编译patch全过程](https://bbs.kanxue.com/thread-284739.htm)
【地址：https://github.com/taisuii/rusda/?tab=readme-ov-file】
简直就是雪中送炭，测试 hook 上之后能够正常运行，成功绕过，但是一运行Java.perform()的代码就闪退，应该是有其他的检测点存在，不完美
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_SH75GXDBAB43TTV.webp)

```
frida以后你用rusda吧，魔改的
今天测试了，可以直接Frida量
app
```

### 魔改系统输出日志

frida都hook上了，但是java层还是没办法输出，有点不甘心，那么再深入，直接修改系统代码打印输出调用函数以及参数呢？
说干就干，折腾半天后(这块具体过程就不发了，有兴趣的可以自己网上找找相关的资料)我们直接看看结果，调用的函数以及传递的账密参数都能打印出来，但是不是每个人都能用这个办法的，还有没有其他的方案呢？有的，那就是
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_PZV2TYKMDJGM8UK.webp)

```
()x'Oxn PTOA
vo1d uko.k()
=Java.lang.Integer Java,lang.Integer.valueOf(int)=[Regs]vreg0=0x0oooo002
intJava.lang.tnteger.intvalue()
=[8ega]vreg0=0x7oc3tpd8/3ava.lang.1ntegex
void uko.k()
cn.izuiyou.coroutine.zy/lowse.ciiava.lang.string,java.lang.string,inc)
-Java.lang.Cbject java.util.HashMap-put(5ava.lang.Cbject,Java.lang.Cbject)
cn.lzuiyou.coroutine.zyFlow se.t(java,lang.String,Java,lang.String,int)
com,meituan.robust.PatchProxyiesult oom,leituan.robust.PatchProxy-proxy(java.lang.Objeot[],Java,lang.Cbject,oom,leituan.robust.ChangeQuickRedireot,bc
cn.izuiyou.coroutine.2y7low se.5(java.lang.5tring,Java.lang.String,int)
Java.lang.stringxd.a(iava.lang.5tring)
cn.izuiyou.coroutine.Zyrloy se.t(java.lang.String,Java.lang.String,int)
[Rega]vxeg0=0xl6f3b3e0/oxg-json.J50N0bject vregl=0xl6f3b4e0/1ava.lang.5tring"phone*vreg2=0xl6f3a9b0/1ava.lang.5tring"12345670901*
cn.izuiyou.coroutine.zyrlow se.t(java,lang.String,Java,lang.String,int)=Java,lang.String xd.a(yava.lang.String)
com,meituan.robust.PatchProxyReault con.meituan.robust.PatchProxy.proxy(java,lang.Object[],Java.lang.Object,con.meltuan.robust.ChangeQuickRedirect,boolean,int,Java.lang.Class[],Je
3ava.1asg.Strieg vk7.1(2ava.1asg.Strieg)
[Rega]vxeg0=0xl6f3b64o/iava.lang.0b5ect1]vregl=0x000ooo00vreg2=0x000ooo00vreg3=0x00ooooo1vreg4=ox0o0id166vreg5=ox16t3h650/iava.lang.Class1]vreg6=0x7095a2c)/java.1ang.Claas<java.lang.5tring>
com.meituan.zobust.PatchPzoxyResult com.meituan.zcbust.FatchPxoxy-proxy(java.lang.Cbject[]. Java.lang.Cbject,con.meituan.zcbust.ChangeQuickRedirect, boolean, int, Java.lang.Class[]. Je
Java.lang.String vt?.l(java,lang.String)
-byte[] java.leng.String-getBytes()
byte[]vk?.k(byte[1)
byte[] vk7.x(byte[])=
=con.meituan.robust.PatchFroxyResult com,meituan.robust.PatchPzoxy-proxy(java,lang.Object[l,Java,lang.Object,com,meituan.robust.ChangeQuickRedirect,boolean,1nt,Java.lang.Class[l,Java.lang.Class)
void com,android.org.conscrypt.OpenSSiKessageDigeatJDK,<init>(long,int)
Java.securiey-MessageDigest Sera.securiey.MessageDigest.getinstance(yava.leng.String)
void com,android.org.conscrypt.OpenSslMessageDigestjDk.<init>(long,int)=long con.android,org.conscrypt.NativeCrypto.EVP_MD GiX_create ()=[Regs]
void com.android.org.conserypt.NativeReftEVF_MD_ctx.<init>(lceg)
byte[] vk?.k(byte[1)-byte[]java.security.HessageDigest.digest()=[Regs]vzego=0xl6f3b7b8 /java.security.MessageDigest&Delegate
java.1ang.5tringvk7.3(bytet1)
java.lang.Stringvk7.3(byte1)
com.meituan.zobust.PatchPxoxyReault ccm.meicuan.xcbust.PatchProxy.proxy(java.lang.CbjectE],Java.lang.Object,ccm.meituan.xcbust.ChangeQuickRedixect,boolean,int,Java.lang.CiasaE],Java.lang.C]
voidjava.lang.Stringbailder.<init>(int)
[Regs]vreg0=0xi6f3b820/3ava.1ang.Stringbu11dervregl=0x00000020
```


![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_CZYRWBQYJQBGKVV.webp)

```
length : 958,006
lines :3,190
Ln : 257
Col: 96
Sel:00
Unix (LF)
```

## 分析检测点

只要定位到检查点然后过掉不就行了，先打开IDA，分析分析那三个线程干了什么
0x127f1：首先对字符串进行了一系列的解密，然后进入循环重复执行，下面的这些函数估计就是主力
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_NWS6WPEAWDSP4NT.webp)

```
219
++v41;
220
--v39;
221
++s_9;
222
++v40;
223
224
while（v53!=1）；
225
226
while（1)
227
228
v42=sub_122BC();
229
v43=sub_123F0（v42）;
230
sub_124c0(v43);
231
sub_195B8(a1);
232
sleep(4u);
233
234
00012A5E SUb 127F0:230 (12A5E)
```


​0x11e1d：这个也是直接进入循环检测的线程，貌似是检测Native层的反调试检测
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_6A648QR3WHZCTV2.webp)

```
void
noreturn sub_11E1c()
unsignedint n2000000_1;//r0
useconds_tn2000000;//r4
intv2;//r0
n2000000_1=sub_81FC（）;
8
if（n2000000_1<0x64）
9
n2000000=2000000;
10
else
11
n2000000 =n2000000_1;
12
while ( 1)
13
14
v2=sub_117F0（）;
15
if （v2 ==-11
v2&&1sub_11630（v2,v2+1)11sub_11CF8（）==777）
16
sub_BA20();
17
usleep(n2000000);
18
19
```


0x19c09：这个貌似是一个验证什么东西的
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_8DN47DVXAHGSDCN.webp)

```
int
fastcall sub_19c08(int a1, int a2)
234
int v2;// r1
unsigned int n0xD; // [sp+4h] [bp-24h]
5
6
n0xD = sub_8070(-1104431950, a2, 1443051395);
if（n0xD&& n0xD<0xD&&!((int（*)(void))loc_EC7C)(）)
00
sub_1991C(0, v2, 1443051395);
9
return0;
10
```


那我们先把重点先放在前面两个线程身上，首先第一个线程
sub_122BC()：检测系统进程
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_THVWAGH3ARZSJNK.webp)

```
步骤
操作
目的
关键代码
opendir(s.._.4)
打开/proc目录
遍历所有进程
2
readdirO循环
读取目录项
获取进程PID
3
跳过：和..
过滤特殊目录
strcmp(s1，".")
4
snprintf(s,0x200u, s__2， s1)
构造路径
生成/proc/PID/cmdline
5
openat（-100,S，0x80000,0)
打开cmdline文件
读取进程命令行
6
逐字节读取内容
获取进程启动参数
read（fd,buf，1u)
7
strstr(haystack, s_)
关键检测
搜索调试器特征字符串
8
exit(0)
反调试触发
发现调试器立即退出
```


sub_123F0()：检测当前进程的文件
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_M3HN25DVKNMZUBG.webp)

```
步骤
代码
功能
检测原理
opendir(s.__3)
打开/proc/self/fd/
获取当前进程所有文件描述符
snprintf(path, 0x200u,"/proc/self/fd/%s",...)
构造FD路径
生成具体的fd路径
3
lstat(path,(struct stat *)buf_)
获取文件状态
检查文件类型
（v8&0xF000)==0xA000
关键判断
检测是否为符号链接
readlink(path,buf,0x200u)
读取链接目标
获取真实文件路径
strstr (buf, s__1)
字符串匹配
检测可疑文件路径
```


sub_124C0：内存映射区域检测
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_KPWTGT59AMJ5URH.webp)

```
步骤
代码片段
功能
检测目的
fopen(s-_5,"r")
打开/proc/self/maps
获取内存映射信息
2
sscanf(s_,"%1x-%1x %s %1x %s %1d %s",...)
解析maps格式
提取地址、权限、路径
3
strstr(haystack, s__6)
关键过滤
检查特定库名
strchr(s__1, 120)
检查'x'权限
确认可执行权限
5
strchr(s__1, 114)
检查r权限
确认可读权限
```


sub_195B8：动态解密并执行shellcode来进行反调试检测
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_E4G85ARYQ6HU3DM.webp)

```
步骤
代码
功能
安全意图
a1&OxFFFFFFFE
地址2字节对齐
确保有效指针
2
*（_DW0RD *)（a1 &0xFFFFFFFE)==-268371745
魔数验证
防止随意调用
3
魔数-268371745=0xF00DBAFF
特殊标识
激活条件
```


可以看到直接一个检测比一个检测牛逼，当时第一反应是能不能nop掉这些，直接不运行呢，尝试了半天，还是打开APP就卡死，实在没办法（不晓得是我的代码有问题还是有其他检测），那再换一种思路，你这不是每隔几秒钟才循环检测一次吗？我把你的检测时间调大不就行了。
然后又折腾了半天，终于可以了，启动，触发检测，都正常，虽然有点小瑕疵，比如说退出附加控制台可能会卡住什么的，但是起码把检测过了
![图片描述](https://bbs.kanxue.com/upload/attach/202508/1020078_PSBGGYUW6NC7AD4.webp)

```
paumeds
cn.xiaocnuankejl.tieba
[A0SPonblueline::cn.xiaochuankeji.tieba]->目标库基址：0xbd945000
进行sleep函数Hook..
SleepHook完成！检测延迟到1小时
sleep
检测：4秒->3600秒（1小时）
测试Java.perform是否正常运行！请等待五秒钟
[AosPonblueline::cn.xiaochuankeji.tieba]->
```

# 总结

直接分析之后逻辑基本上都能看得懂，并不是很难，重点是代码部分，我最初的想法是直接nop掉那几个具体的检测函数点，但是一直都有问题，或者说，根本没办法进行nop(不晓得我的代码是不是有问题)，然后又尝试nop掉sleep的，一样不行，后面只能转变思路调大检测线程的时间

```python
/**
 * 不修改指令的延迟方案 - Hook sleep函数
 */
function hookSleepDelay() {
    let hasHooked = false;
    let moduleBase = null;

    // 监控目标库加载
    Interceptor.attach(Module.findExportByName("libc.so", "__system_property_get"), {
        onEnter: function (args) {
            if (hasHooked) return;

            const module = Process.findModuleByName("libmsaoaidsec.so");
            if (!module) return;

            const propName = args[0].readCString();
            if (propName && propName.includes("ro.build.version.sdk")) {
                moduleBase = module.base;
                console.log(`目标库基址: ${moduleBase}`);

                // Hook sleep函数实现延迟
                hookSleepFunction();
                hasHooked = true;
            }
        }
    });

    // Hook sleep函数，针对反调试调用进行延长
    function hookSleepFunction() {
        console.log("进行 sleep 函数Hook...");

        const sleepPtr = Module.findExportByName("libc.so", "sleep");

        Interceptor.replace(sleepPtr, new NativeCallback(function(seconds) {
            // 检查调用来源
            const backtrace = Thread.backtrace(this.context, Backtracer.ACCURATE);

            // 检查是否来自目标模块的反调试检测
            let isFromAntiDebug = false;
            for (let addr of backtrace) {
                if (moduleBase && addr.compare(moduleBase) >= 0 &&
                    addr.compare(moduleBase.add(0x100000))  ${extendedTime}秒 (1小时)`);
                // 调用原始sleep但用延长时间
                const originalSleep = new NativeFunction(sleepPtr, 'uint', ['uint']);
                return originalSleep(extendedTime);
            }
            // 其他正常sleep调用
            const originalSleep = new NativeFunction(sleepPtr, 'uint', ['uint']);
            return originalSleep(seconds);

        }, 'uint', ['uint']));

        console.log("Sleep Hook完成！检测延迟到1小时");

        // 测试
        setTimeout(() => {
            Java.perform(() => {
                console.log("测试 Java.perform 是否正常运行！请等待五秒钟");
            });
        }, 1000);
    }
}

hookSleepDelay();
```
---

参考
[libmsaoaidsec.so反调试及算法逆向案例(爱库存) ](https://bbs.kanxue.com/thread-284816.htm)
[libmsaoaidsec.so 过检测 ](https://bbs.kanxue.com/thread-287058.htm)



[[培训]Windows内核深度攻防：从Hook技术到Rootkit实战！](https://www.kanxue.com/book-section_list-220.htm)

[#基础理论](https://bbs.kanxue.com/forum-161-1-117.htm) [#逆向分析](https://bbs.kanxue.com/forum-161-1-118.htm) [#HOOK注入](https://bbs.kanxue.com/forum-161-1-125.htm)



---

<a id="doc-2"></a>

## 2. [原创]无壳app的libmsaoaidsec.so frida反调试绕过姿势-Android安全

> 来源: https://bbs.kanxue.com/thread-285811.htm
> 被引用于: [文档1 - 某右APP分析第一弹：过检测](#doc-1)

# [原创]无壳app的libmsaoaidsec.so frida反调试绕过姿势-Android安全

# 前言

当前许多应用通过集成 `libmsaoaidsec.so` 实现针对 Frida 的反调试机制，其核心逻辑是在应用启动过程中，当加载 `libmsaoaidsec.so` 动态库时，会通过 `pthread_create()` 创建独立线程，并在该线程内执行反调试函数，主动扫描 Frida 进程、端口、内存特征等痕迹。若检测到 Frida 存在，则触发进程终止或崩溃。
针对这种机制的绕过思路可以从破坏反调试线程的加载或执行入手，文章会对不同的app进行测试，对于使用libmsaoaidsec.so来进行反调试的app，思路是一样的。

# 定位

通过hook dlopen()函数根据最后加载的so文件来确定程序是在加载哪个so之后退出的

```python
function hook_dlopen(){
    //Android8.0之后加载so通过android_dlopen_ext函数
    var android_dlopen_ext = Module.findExportByName(null,"android_dlopen_ext");
    console.log("addr_android_dlopen_ext",android_dlopen_ext);
    Interceptor.attach(android_dlopen_ext,{
        onEnter:function(args){
            var pathptr = args[0];
            if(pathptr!=null && pathptr != undefined){
                var path = ptr(pathptr).readCString();
                console.log("android_dlopen_ext:",path);
            }
        },
        onLeave:function(retvel){
            console.log("leave!");
        }
    })
}
hook_dlopen()
```
---

如下，可以看到加载完libmsaoaidsec.so之后程序就退出了。
![](https://bbs.kanxue.com/upload/attach/202503/993971_ANPP2BM5TBFQUKY.webp)

```
Spawningcom.douban.frodo
VIR
addr_androiddlopen_ext0x7186e070ac
Spawnedcom.douban.frodo.Resuming main thread!
[Remote::com.douban.frodo]-> android_dlopen_ext:/system/framework/oat/arm64/org.apache.http.legacy.odex
leave!
androiddlopenext:/data/app/com.douban.frodo-j3ukh4wIG6DnZSKbNlvfFQ==/oat/arm64/base.odex
leave!
android_dlopen_ext:/data/app/com.douban.frodo-j3ukh4wIG6DnZSKbNlvfFQ==/1ib/arm64/1ibmsaoaidsec.so
Process
terminated
[Remote::com.douban.frodo]->
ThankyouforusingFrida!
PSD:\fridajs>
```


接下来我们找一个时机，就是在加载libmsaoaidsec.so的时候打印它里面创建的线程

```python
function hook_dlopen(){
    var android_dlopen_ext = Module.findExportByName(null,"android_dlopen_ext");
    console.log("addr_android_dlopen_ext",android_dlopen_ext);
    Interceptor.attach(android_dlopen_ext,{
        onEnter:function(args){
            var pathptr = args[0];
            if(pathptr!=null && pathptr != undefined){
                var path = ptr(pathptr).readCString();
                if(path.indexOf("libmsaoaidsec.so")!=-1){
                    console.log("android_dlopen_ext:",path);
                    hook_pthread()
                }
            }
        },
        onLeave:function(retvel){

        }
    })
}

function hook_pthread() {
    var pth_create = Module.findExportByName("libc.so", "pthread_create");
    console.log("[pth_create]", pth_create);
    Interceptor.attach(pth_create, {
        onEnter: function (args) {
            var module = Process.findModuleByAddress(args[2]);
            if (module != null) {
                console.log("address", module.name, args[2].sub(module.base));
            }
        },
        onLeave: function (retval) {}
    });
}

function main(){
    hook_dlopen()
}

main()
```
---

如下看打印结果，有兴趣的小伙伴可以利用IDA反编译libmsaoaidsec.so跳转到这些地址去看这些函数都干了什么事情
![](https://bbs.kanxue.com/upload/attach/202503/993971_VXW379PYFSY7MNX.webp)

```
Spawning
com.douban.frodo
addr_android_dlopen_ext0x7186e070ac
Spawnedcom.douban.frodo'.Resumingmainthread!
[Remote::com.douban.frodo]->androiddlopen_ext:/data/app/com.douban.frodo-j3ukh4wIG6DnZSKbNlvfFQ==/1ib/arm64/libmsaoaidsec.so
[pth_create]
0x7185504b64
address
libmsaoaidsec.so0x1c544
address 1ibmsaoaidsec.so 0x1b8d4
address 1ibmsaoaidsec.so0x26e5c
Process terminated
[Remote::com.douban.frodo]->
```

# 反调试绕过

现在已经知道了是哪些函数来检测的frida，然后一个很重要的事情就是找到一个时机去把这几个函数都替换掉或者nop掉。
如果去IDA里分析的话，跳转到这几个函数的地址，通过交叉引用查看他们的上一级函数，会发现最终他们都会聚集到`init_proc`函数里，这是一个初始化函数，在so加载时会自动被调用，而且是so加载过程中最先执行的函数，那么就要找一个比`init_proc`函数执行更早的一个时机来把检测frida的函数替换掉或者nop掉。
在so文件的链接过程中linker里的`call_constructors()`函数会触发构造函数，对初始化函数进行注册，然后执行初始化函数，注册的初始化函数会放在`.init_array`函数列表里，可以在调用`call_constructors()`函数的时候动态替换so文件里检测frida的函数的地址，使它指向自定义的空函数。

## 姿势1

替换函数

```python
function hook_dlopen(){
    //Android8.0之后加载so通过android_dlopen_ext函数
    var android_dlopen_ext = Module.findExportByName(null,"android_dlopen_ext");
    console.log("addr_android_dlopen_ext",android_dlopen_ext);
    Interceptor.attach(android_dlopen_ext,{
        onEnter:function(args){
            var pathptr = args[0];
            if(pathptr!=null && pathptr != undefined){
                var path = ptr(pathptr).readCString();
                if(path.indexOf("libmsaoaidsec.so")!=-1){
                    console.log("android_dlopen_ext:",path);
                    hook_call_constructors()
                }
            }
        },
        onLeave:function(retvel){
            //console.log("leave!");
        }
    })
}

function hook_call_constructors() {
    let linker = null;
    if (Process.pointerSize === 4) {
        linker = Process.findModuleByName("linker");
    } else {
        linker = Process.findModuleByName("linker64");
    }
    let call_constructors_addr, get_soname
    let symbols = linker.enumerateSymbols();
    for (let index = 0; index < symbols.length; index++) {
        let symbol = symbols[index];
        if (symbol.name === "__dl__ZN6soinfo17call_constructorsEv") {
            call_constructors_addr = symbol.address;
        } else if (symbol.name === "__dl__ZNK6soinfo10get_sonameEv") {
            get_soname = new NativeFunction(symbol.address, "pointer", ["pointer"]);
        }
    }
    console.log(call_constructors_addr)
    var listener = Interceptor.attach(call_constructors_addr, {
        onEnter: function (args) {
            console.log("hooked call_constructors")
            var module = Process.findModuleByName("libmsaoaidsec.so")
            if (module != null) {
                Interceptor.replace(module.base.add(0x1c544), new NativeCallback(function () {
                    console.log("0x1c544:替换成功")
                }, "void", []))
                Interceptor.replace(module.base.add(0x1b8d4), new NativeCallback(function () {
                    console.log("0x1b8d4:替换成功")
                }, "void", []))
                Interceptor.replace(module.base.add(0x26e5c), new NativeCallback(function () {
                    console.log("0x26e5c:替换成功")
                }, "void", []))
                listener.detach()
            }

        },
    })
}
function main(){
    hook_dlopen()
}

main()
```
---

如下成功绕过了
![](https://bbs.kanxue.com/upload/attach/202503/993971_H89DCEVXF45N5N5.webp)

```
Spawning
com.douban.frodo
addr_android_dlopen_ext0x7186e070ac
Spawnedcom.douban.frodo`.Resumingmainthread!
[Remote::com.douban.frodo]->android_dlopen_ext:/data/app/com.douban.frodo-j3ukh4wIG6DnZSKbNlvfFQ==/1ib/arm64/1ibmsaoaidsec.so
0x718ab9bc00
hookedcallconstructors
0x1c544：替换成功
0x1b8d4：替换成功
0x26e5c：替换成功
```

## 姿势2

nop函数

```python
function nop_addr(addr) {
    Memory.protect(addr, 4 , 'rwx');
    var w = new Arm64Writer(addr);
    w.putRet();
    w.flush();
    w.dispose();
}

function hook_dlopen(){
    //Android8.0之后加载so通过android_dlopen_ext函数
    var android_dlopen_ext = Module.findExportByName(null,"android_dlopen_ext");
    console.log("addr_android_dlopen_ext",android_dlopen_ext);
    Interceptor.attach(android_dlopen_ext,{
        onEnter:function(args){
            var pathptr = args[0];
            if(pathptr!=null && pathptr != undefined){
                var path = ptr(pathptr).readCString();
                if(path.indexOf("libmsaoaidsec.so")!=-1){
                    console.log("android_dlopen_ext:",path);
                    hook_call_constructors()
                }
            }
        },
        onLeave:function(retvel){
            //console.log("leave!");
        }
    })
}

function hook_call_constructors() {
    let linker = null;
    if (Process.pointerSize === 4) {
        linker = Process.findModuleByName("linker");
    } else {
        linker = Process.findModuleByName("linker64");
    }
    let call_constructors_addr, get_soname
    let symbols = linker.enumerateSymbols();
    for (let index = 0; index < symbols.length; index++) {
        let symbol = symbols[index];
        if (symbol.name === "__dl__ZN6soinfo17call_constructorsEv") {
            call_constructors_addr = symbol.address;
        } else if (symbol.name === "__dl__ZNK6soinfo10get_sonameEv") {
            get_soname = new NativeFunction(symbol.address, "pointer", ["pointer"]);
        }
    }
    console.log(call_constructors_addr)
    var listener = Interceptor.attach(call_constructors_addr, {
        onEnter: function (args) {
            console.log("hooked call_constructors")
            var module = Process.findModuleByName("libmsaoaidsec.so")
            if (module != null) {
                nop_addr(module.base.add(0x1c544))
                console.log("0x1c544:替换成功")
                nop_addr(module.base.add(0x1b8d4))
                console.log("0x1b8d4:替换成功")
                nop_addr(module.base.add(0x26e5c))
                console.log("0x26e5c:替换成功")
                listener.detach()
            }

        },
    })
}
function main(){
    hook_dlopen()
}

main()
```
---

如下也可以绕过
![](https://bbs.kanxue.com/upload/attach/202503/993971_GTREY75XA8EUJN9.webp)

```
Spawning
com.douban.frodo
addr_android_dlopen_extex7186e070ac
Spawnedcom.douban.frodo.Resuming
mainthread！
[Remote::com.douban.frodo]->android_dlopen_ext:/data/app/com.douban.frodo-j3ukh4wIG6DnZSKbNlvfFQ==/lib/arm64/libmsaoaidsec.so
0x718ab9bc00
hookedcall_constructors
0x1c544：替换成功
0x1b8d4：替换成功
0x26e5c：替换成功
```

参考文章：
https://mp.weixin.qq.com/s/mBJzRqP-0bGsiTqp6aJSAg
https://mp.weixin.qq.com/s/RyJiHrSO4CU9QLJitC7Tqg
https://bbs.kanxue.com/thread-284816.htm



[[培训]Windows内核深度攻防：从Hook技术到Rootkit实战！](https://www.kanxue.com/book-section_list-220.htm)

[#逆向分析](https://bbs.kanxue.com/forum-161-1-118.htm) [#HOOK注入](https://bbs.kanxue.com/forum-161-1-125.htm)
