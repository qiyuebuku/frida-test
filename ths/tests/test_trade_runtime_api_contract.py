from pathlib import Path


MAIN_HOOK = (
    Path(__file__).resolve().parents[1]
    / "app/src/main/java/com/yuyang/thshook/MainHook.java"
)


def test_trade_runtime_api_is_app_internal_idempotent_and_read_only() -> None:
    source = MAIN_HOOK.read_text(encoding="utf-8")

    assert '"trade.runtime.status".equals(api.id)' in source
    assert '"trade.runtime.ensure".equals(api.id)' in source
    assert 'handleTradeRuntimeStatus()' in source
    assert 'handleTradeRuntimeEnsure(body)' in source
    assert 'startLegacyCommunicationService(appInstance, cl)' in source
    assert 'String[] requiredReadQueries = {"funds", "positions", "today_order"}' in source
    assert "invokeTradeQueryByName(queryName)" in source
    assert 'actions.put("readonly_" + queryName + "_probe_passed")' in source

    ensure_body = source.split(
        "private static String handleTradeRuntimeEnsure", 1
    )[1].split("private static String handleTradeLogin", 1)[0]
    assert "handleTradeOrder(" not in ensure_body
    assert "handleTradeCancel(" not in ensure_body
    assert "handleTradeTransfer(" not in source


def test_runtime_write_ready_requires_hook_session_and_recent_probe() -> None:
    source = MAIN_HOOK.read_text(encoding="utf-8")

    assert "moduleReady && hookReady" in source
    assert "accountReady && sessionReady && probeReady" in source
    assert 'out.put("write_ready", writeReady)' in source
    assert "probeAgeMs <= 300000L" in source
    assert "hookTradingSdkBridge(cl)" in source


def test_every_trade_write_uses_app_internal_write_ready_gate() -> None:
    source = MAIN_HOOK.read_text(encoding="utf-8")
    write_executor = source.split(
        "private static String executeWriteWithConfirm", 1
    )[1].split("private static JSONObject queryTodayOrdersBestEffort", 1)[0]

    assert "requireTradeWriteReady(out)" in write_executor
    assert "call POST /stock/trade/runtime/ensure first" in source
    assert write_executor.index("requireTradeWriteReady(out)") < write_executor.index(
        "queryTodayOrdersBestEffort()"
    )
    assert "handleTradeTransfer(" not in source
    assert 'POST /stock/trade/transfer —' not in source
