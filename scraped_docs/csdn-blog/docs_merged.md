# 文档合集

> 共 1 篇文档
> 生成时间: 2026-02-04 15:14:10

## 目录

1. [Android逆向——过frida检测+so层算法逆向_frida过检测](#doc-1)



---

<a id="doc-1"></a>

## 1. Android逆向——过frida检测+so层算法逆向_frida过检测

> 来源: https://blog.csdn.net/weixin_43889136/article/details/127713563

# Android逆向——过frida检测+so层算法逆向_frida过检测

## 0x01 过frida检测

frida可以说是逆向里面很受欢迎的工具了，你可以在运行的时候得到几乎你想要的所有东西，函数地址、内存数据、java实例，根据我们的需要去修改程序的运行逻辑等等，但是太流行也不好，迎来了各种检测。

  * ptrace占坑、进程名检测、端口检测。（这绕过太简单了）
  * D-Bus通信协议的检测。
  * maps、fd检测。
  * App中线程名的检测。

直接拿出App，看看他到底怎么检测的。节省时间，直接用hluda-server，修改一下运行端口，以[spawn](https://so.csdn.net/so/search?q=spawn&spm=1001.2101.3001.7020)方式注入frida。（hluda-server的好处在于，他所生成的各种so库名字，去掉了frida等特征字段，可以很好的绕过maps和fd的检测。）

![](https://i-blog.csdnimg.cn/blog_migrate/93b2a4a74549e42163fa035165d5702f.png)

```
sailfish:/data/1ocal/tmp # clear
sai1fish:/data/1oca1/tmp #./h1uda-server-14.2.18-android-arm64 -10.0.0.0:9999
C:4
C:\WINDOWS\system32\cmd.exe
C:\Users\0ctopus_father>frida -H 192.168.2.107:9999 -f
asned_ou-.
Frida 14.2.18-A wor1d-class dymamic instrumentation too1kit
Commands:
help
Displays the help system
object?
>
Display information about ’object'
exit/quit ->Exit
More info at https://frida.re/docs/home/
Spawmed
Resuming main thread!
[Remote:
1't
>
Process terminated
[Remote::
登录后您可以享受以下权益
Thank you for using Frida!
CSD
```

直接给我干掉了？？猜测有没有可能是D-Bus[通信协议](https://so.csdn.net/so/search?q=%E9%80%9A%E4%BF%A1%E5%8D%8F%E8%AE%AE&spm=1001.2101.3001.7020)的检测，App向每一个端口都发送了D-Bus认证消息，那肯定会利用strcmp( )或者strstr( )函数进行检测回复的消息。那么就hook一下看看。

![](https://i-blog.csdnimg.cn/blog_migrate/89c9c612d496fffccad3db3f813f720d.png)

```
function hook_strcmp(){
var strcmp = Module.findExportByName("libc.so", "strcmp");
Interceptor.attach(strcmp, {
onEnter : function(args){
if(args[1].readcString().indexOf("REJECT")!=-1){
console.log(args[o].readcstring());
console.log(args[1].readcstring());
onLeave : function(retval){
1)
setImmediate(hook_strcmp)
登录后
CSDN@octopus_fa
```

同样的方法hook一下strstr函数

![](https://i-blog.csdnimg.cn/blog_migrate/aaebc56fe1bbd759aec7823a9c6b08c4.png)

```
PS C:\Users\0ctopus_father\Desktop\frida-workplace>frida-H192.168.2.107:9999-f
--no-pause-l.\sohook.js
Frida 14.2.18 - A world-class dynamic instrumentation toolkit
Commands:
help
>
Displays the help system
object?
Exit
Moreinfoat https://frida.re/docs/home/
Spawned
.Resuming main thread!
[Remote:
->Process terminated
[Remote::
CSDN@octopusfather
```

 不仅没有任何输出，app还是直接给我干掉了。

思考一下，不是D-Bus协议检测、不是ptrace占坑、进程名检测、端口检测、fd和maps检测利用hluda绕过了，emm，等等不一定，跟师傅讨论一下，又搜了几个文章，发现有的app检测非常恶心，只要是maps和fd中存在/data/local/tmp/，甚至只有tmp的字段，app就给kill掉。因为这个目录对于安卓逆向工作来说，是一个比较敏感的目录。hluda-server和frida-server都会在/data/local/tmp/目录下生成一个包含frida所需要的so库等文件。所以当app一旦发现了加载了/data/local/tmp下的任何东西，直接就挂掉。

那怎么办呢？让该文件夹生成到别的目录下，有一个-d参数，试了好多次，有些问题，一直都是在tmp目录下递归生成。所以便想到，你既然去检测maps，肯定是要读取里面的内容，然后寻找是否有该目录的字段咯。

那就直接hook open函数，将原程序的maps文件中一切带有tmp的行都过滤掉，剩余的内容输出到另一个文件中，最后修改open的返回值，指向新生成的文件。完美！

```
function main() {    const openPtr = Module.getExportByName('libc.so', 'open');    const open = new NativeFunction(openPtr, 'int', ['pointer', 'int']);    var readPtr = Module.findExportByName("libc.so", "read");    var read = new NativeFunction(readPtr, 'int', ['int', 'pointer', "int"]);    var fakePath = "/data/data/******/maps";    var file = new File(fakePath, "w");    var buffer = Memory.alloc(512);    Interceptor.replace(openPtr, new NativeCallback(function (pathnameptr, flag) {        var pathname = Memory.readUtf8String(pathnameptr);        var realFd = open(pathnameptr, flag);        if (pathname.indexOf("maps") != 0) {            while (parseInt(read(realFd, buffer, 512)) !== 0) {                var oneLine = Memory.readCString(buffer);                if (oneLine.indexOf("tmp") === -1) {                    file.write(oneLine);                }            }            var filename = Memory.allocUtf8String(fakePath);            return open(filename, flag);        }        var fd = open(pathnameptr, flag);        return fd;    }, 'int', ['pointer', 'int']));}setImmediate(main)
```

## 0x02 SO层算法逆向

### 1、抓个包看看，里面都有啥东西？

![](https://i-blog.csdnimg.cn/blog_migrate/b7c1ad7826f070a1922987db89b39a49.png)

```
抓包肉
登录后有海量资源下载哦
总览
登录可享更多权益
referer=:
将博客内容转为可运行代码提升学习效率
password=Ffo
微信登录
验证码登录
APP登录
deviceldentifie
dateline=1667
sign=df9f836b
CSDn
model=Pixel
会员
info=1
username=12
打开微信扫一扫，快速登录/注册
其他登录方式
?
HUAWEI
```

登录的时候，用户名：123456789，密码：123456

根据字段名字的分析，重点关注的是sign字段（看着像是个hash散列），其次这个password应该是经过加密处理的。

###  2、so算法逆向（passwrod参数）

既然是分析so层算法，具体的Java层的分析和定位就不浪费时间分析了。那么如何定位so文件和具体的函数呢。在Java层在和so层函数交互的时候，就是通过的JNI的机制，所以在so层函数加密数据之后，一定会把加密后的数据返回，通过JNIEnv下的NewStringUTF函数返回给Java层，所以hook一下这个函数，并且输出堆栈。

```
function hook_NewStringUTF(){     var artModule = Process.findModuleByName("libart.so");    var symbols = artModule.enumerateSymbols();    var newStringUTF = null;    for (let i = 0; i < symbols.length; i++) {        let symbol = symbols[i];        if(symbol.name.indexOf("NewStringUTF") != -1 && symbol.name.indexOf("Check") == -1){            console.log(symbol.name);            newStringUTF = symbol.address;        }            }     Interceptor.attach(newStringUTF, {        onEnter : function(args){            console.log(args[1].readCString());            console.log(Thread.backtrace(this.context, Backtracer.ACCURATE).map(DebugSymbol.fromAddress).join('\n') + '\n')        },onLeave : function(retval){         }    }) }
```

IDA反编译找到相应函数，反编译之后进行分析，怎么分析最快呢，当然是从你已经拥有的数据去反推未知的数据。（从后往前分析）

![](https://i-blog.csdnimg.cn/blog_migrate/990b4ca728d232e8482cbd0a493e71c0.png)

```
if （ v7 >= 0
v8 = v7;
v9 = v8 &0xXF
v10 = v7 - v9;
登录后有海量资源下载哦
if ( v7 == v9 
v11 = v7;
else
登录可享更多权益
v11 = v9;
v12 = (int)(v
将博客内容转为可运行代码提升学习效率
v13 = calloc(v
if (!v13 )
微信登录
验证码登录
APP登录
free(v6);
return OLL;
v14 = v13;
memset(v13, 8
v15 = strlen(
memcpy(v14, ve
v16 = v11 + 9;
CSDN
v17 = calloc(v
memset(v17, 0,
if (!v17 )
会员
return OLL;
v18 = strlen(i
memcpy(dest, 
sub_22BC(dest)
v22 = 0xEFCDAE
 sub 46co(v14.
打开微信扫一扫，快速登录/注册
登录后您可以享受以下权益：
v19 = (char 
free(v14);
免费复制代码
和博主大V互动
free(v6);
if (!v19 )
其他登录方式
下载海量资源
return OLL:
发动态/写文章/加
v20 = (*a1)->
?
(6)at 
觉得还
return v20;
立即登录
```

找到了NewStringUTF的调用，v19就是我们的FfQn1pwmgRY=，那么v19是怎么来的（猜测可能是base64，验证一下），找到上面的sub_1FEC，去里面看看。

![](https://i-blog.csdnimg.cn/blog_migrate/5dd216fd758e51b24b89ff826d0d3883.png)

```
ext:0000000000001
ext:0000000000001
登录可享更多权益
ext:0000000000001
ext:0000000000001
将博客内容转为可运行代码提升学习效率
ext:0000000000001
_10]
ext:0000000000001
ext:0000000000002
微信登录
验证码登录
APP登录
ext:0000000000002
ext:0000000000002
ext:0000000000002
ext:0000000000002
ext:0000000000002
2000000000000:0x2
ext:0000000000002
ext:0000000000002
CSDn
ext:0000000000002
ext:0000000000002
会员
ext:0000000000002
ABCDEFGHIJKLMNoPQRSTUVWXYZabcdefghijklm"
ext:0000000000002
ext:0000000000002
ext:0000000000002
b_1FEC+A8↓j
ext:0000000000002
ext:0000000000002
ext:0000000000002
打开微信扫一扫，快速登录/注册
登录后您可以享受以下权益：
ext:0000000000002
ext:0000000000002
20000000000000000
免费复制代码
和博主大V互动
ext:0000000000002
其他登录方式
```

看到这里面有一串字符，和base64很像。hook一下看看

输入

![](https://i-blog.csdnimg.cn/blog_migrate/73cf94db4b2a4f6da52a3d1ee263023b.png)

```
this.args0 onE
BCDEF
780a3634d0
15
微信登录
验证码登录
APP登录
780a3634e0
5f
EXT
780a3634f0
03
780a363500
03
780a363510
03
780a363520
03
.U.#
780a363530
03
CSDN
780a363540
05
780a363550
03
会员
780a363560
03
780a363570
03
S
.U.%
780a363580
03
.0.
780a363590
08
打开微信扫一扫，快速登录/注册
topus-fath
780a3635a0
03
ence
```

输出

![](https://i-blog.csdnimg.cn/blog_migrate/3d8bf8647a7af8f7f1fef637a201bec6.png)

```
retval
onLeave
其他登录方式
EF
下载
780a3634e0
46
G
pwmgRY=..
780a3634f0
03
HUAIWEI
觉得还
780a363540
05
Aligado0628
关注
20
```

难道上面的16进制数据，就是编码前的数据？经过验证之后，这个函数就是base64的编码函数，编码的数据以16进制形式传入。

那这个 15f427d69c268116 数据又是什么？继续向上找

![](https://i-blog.csdnimg.cn/blog_migrate/dd0ab18bc6cdfb54eaa745f07e3d96dd.png)

```
return OLL;
v18 = strlen（a
memcpy(dest,
sub_22BC(dest,
AvEECDAE
sub_46c0(v14,
61A
(char
free(v17);
CSDN
free(v14);
free(v6);
会员
if (!v19
return OLL;
v20 = （*a1)->
free（v1g)
```

进入这个函数一看，看不懂，不知道是干啥的太复杂了，hook看看。

![](https://i-blog.csdnimg.cn/blog_migrate/807fd33ffef88b71c3cf7fb5dc94c052.png)

```
this.args0
onE
B
F
77abf2c3f8
31
其他登录方式
77abf2c408
00
(B
77abf2c418
00
77abf2c428
00
HUAIWEI
opus
觉得还
```

这第一个参数不就是我们传入的密码么

![](https://i-blog.csdnimg.cn/blog_migrate/f5e9649d2b41da7a14b526ab5029cc08.png)

```
this.args1
onl
B
CD
EF
780a363de0
15
780a363df0
780a363e00
00
780a363e10
e3
00
CSDN
780a363e20
780a363e30
00
会员
780a363e40
00
780a363e50
00
topus.father
```

第二个参数不就是刚才的16进制数据吗，其他参数看不懂了，猜测该函数应该是某种加密

而还有一个v22 = 0xEFCDAB9078563412LL;难道是某种密钥或者IV？

v23里面的数据很多，这是什么东西，跟踪v23的有关函数去看看

![](https://i-blog.csdnimg.cn/blog_migrate/486de78bd1ed14f868873048bd261f6d.png)

```
returnOLL;
v18 = strlen（
0
memcnvCdest
9
sub
22BC(dest
V22
OXEFCDAI
1
sub_46c0(v14,
2
v19 = (char
free(v17);
寸
free(v14);
CSDn
free(v6);
if （ !v19
会员
7
return OLL;
0
v20 = (*a1)->l
9
free(v19);
return v20;
```

 进入函数里面查看，发现感觉有点眼熟，是DES加密？dest又是什么，dest是通过a3传过来的，经过hook之后，a3的数据是***************（程序的密钥，不方便展出）。

这个时候就有很多想法了，一个简单的数据，经过函数处理之后，出现了大量的内容，同时v23还是加密函数中的参数。难道是子密钥的生成？？感觉很强烈，进入查看果然很像！

![](https://i-blog.csdnimg.cn/blog_migrate/29fc7c7772e2139416a598ff35982c32.png)

```
do
微信登录
验证码登录
APP登录
if（（（Ox7EFCuLL>>v3)
v16-2;
else
if（（（Ox7EFCuLL>>v3)
v16 = 1;
v17=26;
else
v17 = 27;
v18 =(v14 & 0xFFFFFFF)
v19 =v15 >>v16;
v20=（v15>>v16）|（v
CSDn
v21 - dword_1AE44[((v19
v14=v18（v14<cv17
v22 = dword_1AE44[((v18
会员
result=v21& 0x3FF000
v15-v20&0xFFFFFFF;
++v3:
*a2-((unsigned
2[1]-
PAIR64
int1
a2+=2;
resu
while（v3!-16）
CSnN@nrtnniefather
```

那这么一看v23就是子密钥咯，那v22很有可能就是IV向量了，去验证一下

![](https://i-blog.csdnimg.cn/blog_migrate/9e625d29065085e7aea647febc347389.png)

```
Recipe
Input
登录后有海量资源下载哦
DES Encrypt
123456
登录可享更多权益
将博客内容转为可运行代码提升学习效率
IV
1234567890AB
微信登录
验证码登录
APP登录
Mode
CBC 
From Hex
CSDn
Delimiter
会员
Auto
To Base64
打开微信扫一扫，快速登录/注册
Alphabet
登录后您可以享受以下权益：
A-Za-z0-9+/=
ndano
免费复制代码
和博主大
其他登录方式
FfQn1pwi
下载海量资源
发动态/
?”
觉得还
立即登录
```

验证是正确的（一定要注意字节序的问题）。

最终得出结论，将我们输入的密码，经过DES/CBC模式加密后，再经过base64编码就是password的值。

## 0x03 so算法逆向（sign参数）

根据最开始的hook  NewStringUTF，找到对应的函数位置。

不再一步步的分析了，直接找到特征

![](https://i-blog.csdnimg.cn/blog_migrate/90265b5d56d63755a3bb053a3d54f078.png)

```
rodata:00000000
微信登录
验证码登录
APP登录
F：sub4A38↑0
rodata:00000000
B+4↑r
rodata:00000000
rodata:00000000
rodata:00000000
rodata:00000000
rodata:00000000
rodata:00000000
rodata:00000000
rodata:00000000
CSDn
rodata:00000000
rodata:00000000
会员
rodata:00000000
rodata:00000000
rodata:00000000
rodata:00000000
rodata:00000000
CSDN @oct
rodata:00000000
打开微信扫一扫，快速登录/注册
F:sub_1A54C+
```

好像是MD5的初始化常量啊。

hook 

![](https://i-blog.csdnimg.cn/blog_migrate/6352d300bdb2a34d1b66158fa60f9bf8.png)

```
sub_4A38(v1
v12 = strler
登录可享更多权益
sub_4A4C(v1
110 = t
将博客内容转为可运行代码提升学习效率
v15 = 0LL;
sub_596c(&
sprintf(s,
微信登录
验证码登录
APP登录
sprintf(s,
9
sprintf(s,
sprintf(s,
1
sprintf(s,
sprintf(s,
sprintf(s,
sprintf(s,
5
sprintf(s,
CSDn
sprintf(s,
sprintf(s,
会员
sprintf(s,
sprintf(s,
sprintf(s,
sprintf(s,
sprintf(s,
m
free(v10):
if (v4 )
打开微信扫一扫，快速登录/注册
登录后您可以享受以下
(*al)->Rel
return (*al)
免费复制代码
CSDN@
8
return v4;
```

![](https://i-blog.csdnimg.cn/blog_migrate/d84faf10b306ce06ae9e8c1a49f9a784.png)

```
,this.args1
onl
R
0123456789ABCDE
7758de4c00
31
aptchaReqb2
7758de4c10
63
微信登录
验证码登录
APP登录
b2fb5617730
7758de4c20
31
98359127ala
7758de4c30
30
688471F+ixe
7758de4c40
56
A=131548680
7758de4c50
39
Vx#sf*^Flkl
7758de4c60
53
df(m$&qw%d7
7758de4c70
70
CSDN
7758de4c80
00
7758de4c90
00
会员
7758de4ca0
00
7758de4cb0
00
7758de4cc0
00
7758de4cd0
00
7758de4ce0
00
打开微信扫一扫，快速登录/注册
CSDN
7758de4cf0
00
登录后您可以享受以下
```

可以得到明文，然后对比一下数据包中

![](https://i-blog.csdnimg.cn/blog_migrate/395ec549e8154ba54fd96843ac6b320a.png)

```
referer=:
登录可享更多权益
将博客内容转为可运行代码提升学
password=F+ixt
deviceldentifier
微信登录
验证码登录
AI
dateline=16677
captcha=1111
sign=49f4b3b5
CSDn
model=Pixel
会员
captchald=capt
info=1
打开微信扫一扫，快速登录/注册
username=1315
```

可以看到是 captcha + captchaId + dateline + deviceIdentifier + info + password + username + “ef2vx#sf*^FlklSD*9sdf(m$&qw%d7po”  拼接起来的数据。

![](https://i-blog.csdnimg.cn/blog_migrate/dac3033abb5172ee55e709f672daec2f.png)

```
,this.args0
on
BCDEF
0123456789ABCDEE
7fd7c59f50
49
.R$S....TY.
7fd7c59f60
. .V3tGjA=1
7fd7c59f70
33
868097ef2vx#
7fd7c59f80
73
FlklsD*9sdf(
7fd7c59f90
P9
CSDn
%d7po....
7fd7c59fa0
98
...0.yo
7fd7c59fb0
00
会员
7fd7c59fc0
00
7fd7c59fd0
00
[..cSDN@octopus_father
```

这里的数据就是经过md5加密后的内容，验证一下。

 ![](https://i-blog.csdnimg.cn/blog_migrate/a7d7ec7f5ac29ff5e52687b5257d3599.png)

```
登录可享更多权益
MD5
667798359127a1a8be2e688471F+fxeV3tGjA-13154868e97ef2vx#sf*^F1k1SD*9sdf(m$&qw&d7po
将博客内容转为可运行代码提升学习效率
微信登录
验证码登录
APP登录
CSDn
会员
start:32
end:
32
length:
tine:
12
length:
lines:
8
打开微信扫一扫，快速登录/注册
登录后您可以享受以下权益：
免费复制代码
和博主大V互动
```

 可以得到结论，sign的值就是将数据包中的

 captcha + captchaId + dateline + deviceIdentifier + info + password + username + “ef2vx#sf*^FlklSD*9sdf(m$&qw%d7po” 

拼接之后，在进行md5散列的结果。

至此，整个逆向结束。
