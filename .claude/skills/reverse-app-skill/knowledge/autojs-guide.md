# AutoJS 自动化指南

## 定位：日志是眼睛，AutoJS 是手

逆向 App 时，日志驱动分析让我们"看到" App 内部行为，但触发这些行为需要大量重复操作：
打开 App → 导航到目标页面 → 触发网络请求 → 切换 tab → 等待加载...

每次修改 Hook 后都要手动重复这些步骤，效率极低。AutoJS 就是把这些"手动操作"固化为脚本，让你专注于日志分析。

## 为什么用 AutoJS 而不是纯 adb input

| 特性 | AutoJS6（无障碍服务） | adb input tap/swipe |
|------|----------------------|---------------------|
| 控件查找 | `text("持仓").findOne().click()` 语义化 | 只能硬编码坐标 |
| 等待元素 | `waitFor()` 等待元素出现 | 只能 `sleep` 盲等 |
| 适应性 | 分辨率变化不影响文本查找 | 坐标必须重新计算 |
| 运行位置 | 手机端，不受 WSL2 影响 | 依赖 adb，WSL2 下可能不稳定 |
| 复杂流程 | 条件判断、循环、异常处理 | 只能线性执行 |

## 两种自动化方案对比

| | AutoJS6（手机端） | Python+ADB（PC端） |
|--|--|--|
| 运行位置 | 手机 | WSL2 |
| 控件查找 | 无障碍 text/id/className | UIAutomator dump + XML 解析 |
| WSL2 兼容 | 无问题 | adb 命令可能不稳定 |
| 适用场景 | **固化的重复流程** | **探索阶段** + 需要截屏分析 |
| 调试 | AutoJS6 内置日志查看器 | Python print + 截屏 |
| 启动方式 | `am start` 或 AutoJS6 内运行 | 直接 `python script.py` |

**推荐工作流**：
1. 探索阶段用截屏 + Python/ADB 摸清 UI 结构
2. 流程确定后写成 AutoJS 脚本固化
3. 后续每次 Hook 迭代直接运行 AutoJS 脚本

## AutoJS 脚本开发模式

### 第一步：探索 UI 结构

```bash
# 截屏查看当前界面
$ADB exec-out screencap -p > /tmp/screenshot.png

# 或用 UIAutomator dump 获取控件树
$ADB shell uiautomator dump /sdcard/ui.xml
$ADB pull /sdcard/ui.xml /tmp/ui.xml
```

### 第二步：编写 AutoJS 脚本

基于探索结果，使用 `templates/autojs_template.js` 模板创建脚本。

### 第三步：部署运行

```bash
# 方法 1：推送到手机，在 AutoJS6 中运行
$ADB push script.js /sdcard/Scripts/

# 方法 2：通过 am start 启动 AutoJS6
$ADB shell am start -n org.autojs.autojs6/.ui.main.MainActivity
```

## 常用 AutoJS API 速查

### 选择器

```javascript
// 文本查找（最常用）
text("持仓").findOne()          // 找到第一个，阻塞等待
text("持仓").findOne(5000)      // 5秒超时
text("持仓").find()             // 找所有匹配的
text("持仓").exists()           // 是否存在（不阻塞）

// ID 查找
id("btn_submit").findOne()

// 类名查找
className("android.widget.Button").findOne()

// 组合条件
className("android.widget.TextView").text("确定").findOne()
```

### 操作

```javascript
// 点击控件
widget.click()

// 点击控件中心坐标（更可靠）
var b = widget.bounds();
click(b.centerX(), b.centerY());

// 坐标点击（无障碍树找不到时的降级方案）
click(540, 1200);

// 滑动
swipe(540, 1500, 540, 500, 500);  // 上滑
scrollDown();                       // 向下滚动
```

### 等待与控制

```javascript
auto.waitFor();              // 等待无障碍服务就绪（脚本开头必加）
sleep(2000);                 // 等待 2 秒
waitForActivity("MainActivity");  // 等待某个 Activity
```

### 日志与调试

```javascript
log("消息");                  // 输出到 AutoJS6 日志
toast("提示");                // 屏幕 toast 提示
console.show();               // 显示悬浮日志窗口
```

### App 操作

```javascript
app.launchPackage("com.example.app");     // 启动 App
app.startActivity(intent);                 // 启动 Activity

// 构造 Intent
var intent = app.intent({
    packageName: "com.example.app",
    className: "com.example.app.MainActivity",
    flags: ["activity_new_task"]
});
```

## 从现有项目提取的脚本模式

### clickCenter 模式

直接调用 `widget.click()` 有时不生效（比如 ListView 内的控件），用坐标点击更可靠：

```javascript
function clickCenter(widget) {
    if (!widget) return false;
    var b = widget.bounds();
    click(b.centerX(), b.centerY());
    return true;
}
```

### step 日志模式

清晰标记每个步骤，方便定位问题：

```javascript
function step(msg) {
    log("\n======== " + msg + " ========");
    toast(msg);
}
```

### 超时等待模式

等待某个条件满足，而不是盲目 sleep：

```javascript
function waitForText(text, timeout) {
    var t = timeout || 10000;
    var w = text(text).findOne(t);
    if (!w) {
        log("等待 '" + text + "' 超时 (" + t/1000 + "s)");
        return null;
    }
    return w;
}
```

### 坐标参考注释规范

在脚本头部记录屏幕分辨率和关键坐标，方便维护：

```javascript
/**
 * 屏幕坐标参考（1080x2378）：
 * - 底部导航 "交易": (420, 2330)
 * - 顶部 tab "基金": (378, 191)
 * - 操作栏 "持仓": (745, 1083)
 */
```

## 注意事项

1. **`auto.waitFor()` 必须在脚本开头调用**，否则无障碍 API 不可用
2. **优先用文本/ID 查找控件，坐标作为降级方案**，因为不同分辨率坐标不同
3. **每步操作后加适当 sleep**，给 App 反应时间（通常 1-3 秒）
4. **处理弹窗和权限对话框**，AutoJS6 本身可能触发权限确认弹窗
5. **脚本推送路径**：`/sdcard/Scripts/` 是 AutoJS6 的默认脚本目录
