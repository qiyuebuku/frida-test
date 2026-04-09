/**
 * AutoJS6 自动化脚本模板
 *
 * 目标 App: <APP_NAME>
 * 包名: <PACKAGE_NAME>
 * 用途: <DESCRIPTION>
 *
 * 屏幕坐标参考（1080x2378）：
 * - <元素描述>: (x, y)
 * - <元素描述>: (x, y)
 */

var PKG = "<PACKAGE_NAME>";

// 等待无障碍服务就绪（必须）
auto.waitFor();

// ===== 工具函数 =====

/**
 * 点击控件中心坐标（比 widget.click() 更可靠）
 */
function clickCenter(widget) {
    if (!widget) return false;
    var b = widget.bounds();
    click(b.centerX(), b.centerY());
    return true;
}

/**
 * 步骤日志（标记当前执行阶段）
 */
function step(msg) {
    log("\n======== " + msg + " ========");
    toast(msg);
}

/**
 * 等待指定文本出现并返回控件
 * @param {string} txt - 要查找的文本
 * @param {number} [timeout=10000] - 超时时间(ms)
 * @returns {UiObject|null}
 */
function waitForText(txt, timeout) {
    var t = timeout || 10000;
    var w = text(txt).findOne(t);
    if (!w) {
        log("等待 '" + txt + "' 超时 (" + t / 1000 + "s)");
        return null;
    }
    log("找到: '" + txt + "' at (" + w.bounds().centerX() + ", " + w.bounds().centerY() + ")");
    return w;
}

/**
 * 坐标点击（无障碍树找不到控件时的降级方案）
 */
function tapXY(x, y, desc) {
    log("坐标点击: " + (desc || "") + " (" + x + ", " + y + ")");
    click(x, y);
}

/**
 * 带重试的点击操作
 * @param {string} txt - 控件文本
 * @param {number} [retries=3] - 重试次数
 * @param {number} [interval=2000] - 重试间隔(ms)
 * @returns {boolean}
 */
function clickText(txt, retries, interval) {
    var r = retries || 3;
    var iv = interval || 2000;
    for (var i = 0; i < r; i++) {
        var w = text(txt).findOne(iv);
        if (w) {
            clickCenter(w);
            log("点击: '" + txt + "'");
            return true;
        }
        log("第 " + (i + 1) + " 次未找到 '" + txt + "'，重试...");
    }
    log("点击失败: '" + txt + "' 未找到");
    return false;
}

// ===== 主流程 =====

function step1_launchApp() {
    step("1. 启动 App");
    app.launchPackage(PKG);
    sleep(3000);

    // TODO: 处理可能的弹窗（权限、广告、更新提示等）
    // var closeBtn = text("关闭").findOne(2000);
    // if (closeBtn) clickCenter(closeBtn);
}

function step2_navigate() {
    step("2. 导航到目标页面");

    // TODO: 根据 App UI 实现导航逻辑
    // 优先用文本查找，坐标作为降级方案
    //
    // 示例：
    // clickText("我的");
    // sleep(2000);
    // clickText("设置");
    // sleep(2000);
}

function step3_triggerAction() {
    step("3. 触发目标操作");

    // TODO: 触发需要分析的操作（发送请求、加载数据等）
    //
    // 示例：
    // clickText("刷新");
    // sleep(3000);
}

function main() {
    log("===== 开始自动化 =====");
    var startTime = new Date().getTime();

    step1_launchApp();
    step2_navigate();
    step3_triggerAction();

    var elapsed = Math.round((new Date().getTime() - startTime) / 1000);
    step("完成，耗时 " + elapsed + "s");
}

main();
