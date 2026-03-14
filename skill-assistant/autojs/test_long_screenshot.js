/**
 * 自动化测试长截图功能 (AutoJs6)
 *
 * 流程：
 * 1. 打开 Screenshot Assistant app → 授权截屏权限
 * 2. 切到同花顺持仓页面
 * 3. 点击查询 tab 再回到持仓 tab（重置滚动位置到顶部）
 * 4. 点击悬浮球 → 点击「持仓分析」触发自动长截图
 * 5. 等待完成
 *
 * 同花顺 UI 坐标参考（1080x2378 屏幕）：
 * - 顶部 tab (A股/基金/期货): y=143~240
 *   - "基金" center: (378, 191)
 * - 操作栏 (买入/卖出/定投/持仓/查询): y=1053~1113
 *   - "持仓" center: (745, 1083)
 *   - "查询" center: (916, 1083)
 * - 底部导航 (首页/行情/自选/交易/资讯/理财): y=2280~2378
 *   - "交易" center: (420, 2330)
 *
 * 悬浮球：56dp, gravity=TOP|START, x=0, y=200
 *   - center ≈ (77, 277)
 */

var PKG = "com.example.screenshotassistant";
var THS_PKG = "com.hexin.plat.android";

auto.waitFor();

// ===== 工具函数 =====

function clickCenter(widget) {
    if (!widget) return false;
    var b = widget.bounds();
    click(b.centerX(), b.centerY());
    return true;
}

function step(msg) {
    log("\n======== " + msg + " ========");
    toast(msg);
}

// ===== Step 1: 启动 Screenshot Assistant 并授权 =====

function launchAndAuthorize() {
    step("1. 启动截屏助手并授权");

    // 强制启动 MainActivity
    var launchIntent = app.intent({
        packageName: PKG,
        className: PKG + ".MainActivity",
        flags: ["activity_new_task"]
    });
    app.startActivity(launchIntent);
    sleep(4000);

    // 检查是否已在运行
    if (text("停止服务").exists()) {
        log("服务已在运行，跳过授权");
        return true;
    }

    var startBtn = text("启动服务").findOne(8000);
    if (!startBtn) {
        log("未找到'启动服务'按钮，截图看当前状态");
        return false;
    }
    clickCenter(startBtn);
    sleep(3000);

    for (var i = 0; i < 8; i++) {
        sleep(2000);

        var fullScreen = text("整个屏幕").findOne(500);
        var singleApp = text("单个应用").findOne(500);

        if (fullScreen && singleApp) {
            clickCenter(fullScreen);
            log("已选择'整个屏幕'");
            sleep(2000);
            continue;
        }

        if (singleApp && !fullScreen) {
            clickCenter(singleApp);
            log("展开下拉框");
            sleep(2000);
            continue;
        }

        var startW = className("android.widget.Button").text("开始").findOne(1500);
        if (startW) {
            clickCenter(startW);
            log("点击'开始'");
            sleep(3000);
            break;
        }

        var nextW = text("下一步").findOne(1000);
        if (nextW) {
            clickCenter(nextW);
            log("点击'下一步'");
            sleep(2000);
            continue;
        }

        log("[attempt " + (i+1) + "] 等待...");
    }

    log("授权流程完成");
    return true;
}

// ===== Step 2: 导航到同花顺持仓 =====

function navigateToHoldings() {
    step("2. 导航到同花顺持仓");

    app.launchPackage(THS_PKG);
    sleep(3000);

    // 处理 AutoJs6 打开确认弹窗
    var allowBtn = text("仅本次允许").findOne(2000);
    if (allowBtn) {
        clickCenter(allowBtn);
        log("点击'仅本次允许'");
        sleep(2000);
    }
    var allow30 = text("30天内允许").findOne(1000);
    if (allow30) {
        clickCenter(allow30);
        log("点击'30天内允许'");
        sleep(2000);
    }

    sleep(3000); // 等待同花顺完全加载

    // 检查当前页面状态
    if (text("产品类型").exists() || text("持有收益").exists()) {
        log("已在持仓页面");
        return true;
    }

    // 完整导航: 交易 → 基金 → 持仓
    log("开始导航: 交易 → 基金 → 持仓");

    // 1. 点底部"交易" tab
    var tradeTab = text("交易").findOne(3000);
    if (tradeTab) {
        clickCenter(tradeTab);
        log("点击'交易' tab");
    } else {
        log("未找到'交易'，坐标点击");
        click(420, 2330);
    }
    sleep(3000);

    // 2. 点顶部"基金" tab
    var fundTab = text("基金").findOne(3000);
    if (fundTab) {
        var fundList = text("基金").find();
        var found = false;
        for (var i = 0; i < fundList.length; i++) {
            if (fundList[i].bounds().centerY() < 250) {
                clickCenter(fundList[i]);
                log("点击'基金' tab (y=" + fundList[i].bounds().centerY() + ")");
                found = true;
                break;
            }
        }
        if (!found) {
            log("基金 tab 不在预期位置，坐标点击");
            click(378, 191);
        }
    } else {
        log("未找到'基金'，坐标点击");
        click(378, 191);
    }
    sleep(4000);

    // 3. 点"持仓"
    var holdTab = text("持仓").findOne(3000);
    if (holdTab) {
        clickCenter(holdTab);
        log("点击'持仓' (y=" + holdTab.bounds().centerY() + ")");
    } else {
        log("未找到'持仓'，坐标点击");
        click(745, 1083);
    }
    sleep(3000);

    log("导航完成");
    return true;
}

// ===== Step 3: 重置滚动位置（点查询再回持仓） =====

function resetScrollPosition() {
    step("3. 重置滚动位置");

    // 点"查询" tab
    var queryTab = text("查询").findOne(2000);
    if (queryTab) {
        clickCenter(queryTab);
        log("点击'查询' tab (y=" + queryTab.bounds().centerY() + ")");
    } else {
        log("未找到'查询'，坐标点击");
        click(916, 1083);
    }
    sleep(2000);

    // 回到"持仓" tab
    var holdTab = text("持仓").findOne(2000);
    if (holdTab) {
        clickCenter(holdTab);
        log("点击'持仓' tab (y=" + holdTab.bounds().centerY() + ")");
    } else {
        log("未找到'持仓'，坐标点击");
        click(745, 1083);
    }
    sleep(2000);

    log("滚动位置已重置");
}

// ===== Step 4: 通过悬浮球触发长截图 =====

function triggerLongScreenshot() {
    step("4. 通过悬浮球触发长截图");

    // 点击悬浮球 (56dp, gravity TOP|START, x=0, y=200)
    // 屏幕密度 ~2.75，56dp ≈ 154px，center ≈ (77, 277)
    log("点击悬浮球 (77, 277)");
    click(77, 277);
    sleep(1500);

    // 悬浮球菜单是 TYPE_APPLICATION_OVERLAY，AutoJs6 的 text() 可能找不到
    // 先尝试无障碍树查找
    var menuItem = text("持仓分析").findOne(1500);
    if (menuItem) {
        clickCenter(menuItem);
        log("点击'持仓分析'菜单项（通过文本查找）");
    } else {
        // 坐标点击：菜单在悬浮球右侧
        // 菜单 3 列网格，"持仓分析" 是第 5 项 = row1, col1 (第2行第2列)
        // 菜单位置: x ≈ ballX + dp(60) = 165, y ≈ ballY - dp(20) = 145
        // 每个格子: dp(72)=198px + dp(8)=22px margin
        // 标题区 ≈ 62px, padding ≈ 33px
        // Row1 Col1 center ≈ (528, 570)
        log("坐标点击'持仓分析' (528, 570)");
        click(528, 570);
    }
    sleep(1000);

    waitForCompletion();
}

function triggerViaBroadcast() {
    var intent = new android.content.Intent("com.example.screenshotassistant.CAPTURE");
    intent.setComponent(new android.content.ComponentName(
        "com.example.screenshotassistant",
        "com.example.screenshotassistant.service.CaptureCommandReceiver"
    ));
    intent.putExtra("action", "fund_holdings");
    context.sendBroadcast(intent);
    log("broadcast 已发送");
    waitForCompletion();
}

function waitForCompletion() {
    step("5. 等待截图完成");
    var startTime = new Date().getTime();
    var maxWait = 90000; // 90秒超时

    while (new Date().getTime() - startTime < maxWait) {
        sleep(5000);
        var elapsed = Math.round((new Date().getTime() - startTime) / 1000);

        // 检查 logcat
        var logs = shell("logcat -d -s FloatingWindow:D ImageStitcher:D 2>/dev/null | tail -30", false);
        var logText = logs.result || "";

        if (logText.indexOf("assembled:") >= 0) {
            var match = logText.match(/assembled: (\d+) frames -> (\d+x\d+)/);
            if (match) {
                log("[" + elapsed + "s] 拼接完成! " + match[1] + " 帧 -> " + match[2]);
            } else {
                log("[" + elapsed + "s] 拼接完成!");
            }
            return true;
        }

        if (logText.indexOf("sendOrSave") >= 0) {
            log("[" + elapsed + "s] 截图已发送!");
            return true;
        }

        if (logText.indexOf("bitmap is null") >= 0) {
            log("[" + elapsed + "s] 截屏失败 (bitmap is null)，需重新授权");
            return false;
        }

        if (logText.indexOf("accessibility service not connected") >= 0) {
            log("[" + elapsed + "s] 无障碍服务未连接");
            return false;
        }

        // 显示进度
        var capturedCount = (logText.match(/captured #/g) || []).length;
        log("[" + elapsed + "s] 等待... (" + capturedCount + " 帧)");
    }

    log("超时 (" + maxWait/1000 + "s)");
    return false;
}

// ===== 主流程 =====

function main() {
    log("===== 开始自动化测试 =====");
    var startTime = new Date().getTime();

    launchAndAuthorize();
    navigateToHoldings();
    resetScrollPosition();
    triggerLongScreenshot();

    var elapsed = Math.round((new Date().getTime() - startTime) / 1000);
    step("测试完成，耗时 " + elapsed + "s");
}

main();
