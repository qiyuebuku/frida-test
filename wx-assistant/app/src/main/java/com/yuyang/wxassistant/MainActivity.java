package com.yuyang.wxassistant;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.view.accessibility.AccessibilityManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import android.app.Activity;

import java.util.List;

public class MainActivity extends Activity {

    private AppPreferences prefs;

    private TextView statusAccessibility;
    private TextView statusOverlay;
    private Button btnEnableAccessibility;
    private Button btnEnableOverlay;

    private EditText editBaseUrl;
    private EditText editApiKey;
    private EditText editModel;
    private EditText editSystemPrompt;
    private CheckBox checkDebugMode;
    private Button btnSave;
    private Button btnTestApi;
    private TextView textTestResult;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        prefs = new AppPreferences(this);

        // Permission views
        statusAccessibility = findViewById(R.id.status_accessibility);
        statusOverlay = findViewById(R.id.status_overlay);
        btnEnableAccessibility = findViewById(R.id.btn_enable_accessibility);
        btnEnableOverlay = findViewById(R.id.btn_enable_overlay);

        // Config views
        editBaseUrl = findViewById(R.id.edit_base_url);
        editApiKey = findViewById(R.id.edit_api_key);
        editModel = findViewById(R.id.edit_model);
        editSystemPrompt = findViewById(R.id.edit_system_prompt);
        checkDebugMode = findViewById(R.id.check_debug_mode);
        btnSave = findViewById(R.id.btn_save);
        btnTestApi = findViewById(R.id.btn_test_api);
        textTestResult = findViewById(R.id.text_test_result);

        // Load saved config
        editBaseUrl.setText(prefs.getApiBaseUrl());
        editApiKey.setText(prefs.getApiKey());
        editModel.setText(prefs.getModel());
        editSystemPrompt.setText(prefs.getSystemPrompt());
        checkDebugMode.setChecked(prefs.isDebugMode());

        // Permission buttons
        btnEnableAccessibility.setOnClickListener(v -> {
            Intent intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS);
            startActivity(intent);
        });

        btnEnableOverlay.setOnClickListener(v -> {
            Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        });

        // Save button
        btnSave.setOnClickListener(v -> saveConfig());

        // Test API button
        btnTestApi.setOnClickListener(v -> testApi());
    }

    @Override
    protected void onResume() {
        super.onResume();
        updatePermissionStatus();
    }

    private void updatePermissionStatus() {
        // Check accessibility service
        boolean accessibilityEnabled = isAccessibilityServiceEnabled();
        statusAccessibility.setText(accessibilityEnabled ? "已开启" : "未开启");
        statusAccessibility.setTextColor(accessibilityEnabled ? 0xFF1AAD19 : 0xFFFF4444);
        btnEnableAccessibility.setEnabled(!accessibilityEnabled);

        // Check overlay permission
        boolean overlayEnabled = Settings.canDrawOverlays(this);
        statusOverlay.setText(overlayEnabled ? "已开启" : "未开启");
        statusOverlay.setTextColor(overlayEnabled ? 0xFF1AAD19 : 0xFFFF4444);
        btnEnableOverlay.setEnabled(!overlayEnabled);
    }

    private boolean isAccessibilityServiceEnabled() {
        AccessibilityManager am = (AccessibilityManager) getSystemService(Context.ACCESSIBILITY_SERVICE);
        List<AccessibilityServiceInfo> services = am.getEnabledAccessibilityServiceList(
                AccessibilityServiceInfo.FEEDBACK_ALL_MASK);
        for (AccessibilityServiceInfo info : services) {
            if (info.getResolveInfo().serviceInfo.packageName.equals(getPackageName())) {
                return true;
            }
        }
        return false;
    }

    private void saveConfig() {
        prefs.setApiBaseUrl(editBaseUrl.getText().toString().trim());
        prefs.setApiKey(editApiKey.getText().toString().trim());
        prefs.setModel(editModel.getText().toString().trim());
        prefs.setSystemPrompt(editSystemPrompt.getText().toString().trim());
        prefs.setDebugMode(checkDebugMode.isChecked());
        Toast.makeText(this, "配置已保存", Toast.LENGTH_SHORT).show();
    }

    private void testApi() {
        saveConfig();
        textTestResult.setText("测试中...");
        textTestResult.setTextColor(0xFF999999);
        btnTestApi.setEnabled(false);

        AiApiClient client = new AiApiClient(prefs);
        client.testConnection(new AiApiClient.Callback() {
            @Override
            public void onSuccess(String reply) {
                textTestResult.setText("连接成功!");
                textTestResult.setTextColor(0xFF1AAD19);
                btnTestApi.setEnabled(true);
            }

            @Override
            public void onError(String error) {
                textTestResult.setText("连接失败: " + error);
                textTestResult.setTextColor(0xFFFF4444);
                btnTestApi.setEnabled(true);
            }
        });
    }
}
