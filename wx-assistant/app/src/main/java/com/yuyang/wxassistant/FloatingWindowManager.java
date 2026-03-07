package com.yuyang.wxassistant;

import android.animation.ValueAnimator;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.animation.DecelerateInterpolator;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

public class FloatingWindowManager {

    private final Context context;
    private final WindowManager windowManager;
    private final DisplayMetrics displayMetrics;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    // Floating button
    private View floatingButton;
    private WindowManager.LayoutParams buttonParams;
    private TextView buttonText;
    private GradientDrawable buttonBg;
    private boolean buttonShowing = false;

    // Panel
    private View panelRoot;
    private WindowManager.LayoutParams panelParams;
    private boolean panelShowing = false;

    // Collection view widgets
    private LinearLayout collectionView;
    private TextView collectionTitle;
    private TextView collectionMessages;
    private ScrollView collectionScroll;
    private TextView btnSendToAi;

    // Result view widgets
    private LinearLayout resultView;
    private ProgressBar progressBar;
    private TextView resultText;
    private ScrollView resultScroll;

    // Callbacks
    private Runnable onButtonClicked;
    private Runnable onSendToAi;
    private Runnable onClearCollection;

    public FloatingWindowManager(Context context) {
        this.context = context;
        this.windowManager = (WindowManager) context.getSystemService(Context.WINDOW_SERVICE);
        this.displayMetrics = context.getResources().getDisplayMetrics();
    }

    public void setOnButtonClicked(Runnable r) { this.onButtonClicked = r; }
    public void setOnSendToAi(Runnable r) { this.onSendToAi = r; }
    public void setOnClearCollection(Runnable r) { this.onClearCollection = r; }

    // ==================== Floating Button ====================

    public void showButton() {
        if (buttonShowing) return;
        floatingButton = createFloatingButton();
        buttonParams = createButtonLayoutParams();
        windowManager.addView(floatingButton, buttonParams);
        buttonShowing = true;
    }

    public void hideButton() {
        if (!buttonShowing || floatingButton == null) return;
        windowManager.removeView(floatingButton);
        floatingButton = null;
        buttonShowing = false;
    }

    public boolean isButtonShowing() {
        return buttonShowing;
    }

    public void updateButtonBadge(int count) {
        mainHandler.post(() -> {
            if (buttonText == null || buttonBg == null) return;
            if (count > 0) {
                buttonText.setText(String.valueOf(count));
                buttonText.setTextSize(TypedValue.COMPLEX_UNIT_SP, count >= 100 ? 11 : 14);
                buttonBg.setColor(0xFF2196F3); // Blue = collecting
            } else {
                buttonText.setText("AI");
                buttonText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
                buttonBg.setColor(0xFF1AAD19); // Green = idle
            }
        });
    }

    private View createFloatingButton() {
        int sizePx = dp(48);

        FrameLayout container = new FrameLayout(context);

        buttonText = new TextView(context);
        buttonText.setText("AI");
        buttonText.setTextColor(Color.WHITE);
        buttonText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        buttonText.setTypeface(null, Typeface.BOLD);
        buttonText.setGravity(Gravity.CENTER);

        buttonBg = new GradientDrawable();
        buttonBg.setShape(GradientDrawable.OVAL);
        buttonBg.setColor(0xFF1AAD19);
        buttonText.setBackground(buttonBg);

        container.addView(buttonText, new FrameLayout.LayoutParams(sizePx, sizePx));

        // Touch: drag + click
        container.setOnTouchListener(new View.OnTouchListener() {
            private float downX, downY;
            private int downParamX, downParamY;
            private boolean isDragging;
            private long downTime;

            @Override
            public boolean onTouch(View v, MotionEvent event) {
                switch (event.getAction()) {
                    case MotionEvent.ACTION_DOWN:
                        downX = event.getRawX();
                        downY = event.getRawY();
                        downParamX = buttonParams.x;
                        downParamY = buttonParams.y;
                        isDragging = false;
                        downTime = System.currentTimeMillis();
                        return true;
                    case MotionEvent.ACTION_MOVE:
                        float dx = event.getRawX() - downX;
                        float dy = event.getRawY() - downY;
                        if (Math.abs(dx) > 10 || Math.abs(dy) > 10) isDragging = true;
                        if (isDragging) {
                            buttonParams.x = (int) (downParamX + dx);
                            buttonParams.y = (int) (downParamY + dy);
                            windowManager.updateViewLayout(floatingButton, buttonParams);
                        }
                        return true;
                    case MotionEvent.ACTION_UP:
                        if (isDragging) {
                            snapToEdge();
                        } else if (System.currentTimeMillis() - downTime < 300) {
                            if (onButtonClicked != null) onButtonClicked.run();
                        }
                        return true;
                }
                return false;
            }
        });
        return container;
    }

    private void snapToEdge() {
        int screenWidth = displayMetrics.widthPixels;
        int targetX = buttonParams.x < screenWidth / 2 ? 0 : screenWidth - dp(48);

        ValueAnimator animator = ValueAnimator.ofInt(buttonParams.x, targetX);
        animator.setDuration(200);
        animator.setInterpolator(new DecelerateInterpolator());
        animator.addUpdateListener(a -> {
            buttonParams.x = (int) a.getAnimatedValue();
            if (floatingButton != null && floatingButton.isAttachedToWindow()) {
                windowManager.updateViewLayout(floatingButton, buttonParams);
            }
        });
        animator.start();
    }

    private WindowManager.LayoutParams createButtonLayoutParams() {
        int sizePx = dp(48);
        WindowManager.LayoutParams p = new WindowManager.LayoutParams(
                sizePx, sizePx,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT);
        p.gravity = Gravity.TOP | Gravity.START;
        p.x = displayMetrics.widthPixels - sizePx - dp(8);
        p.y = displayMetrics.heightPixels / 3;
        return p;
    }

    // ==================== Panel ====================

    public void showPanel() {
        if (panelShowing) return;
        panelRoot = createPanel();
        panelParams = createPanelLayoutParams();
        windowManager.addView(panelRoot, panelParams);
        panelShowing = true;
    }

    public void hidePanel() {
        if (!panelShowing || panelRoot == null) return;
        windowManager.removeView(panelRoot);
        panelRoot = null;
        panelShowing = false;
    }

    public boolean isPanelShowing() {
        return panelShowing;
    }

    /** Show/update the collection view with formatted messages */
    public void showCollectionView(String formattedMessages, int count) {
        mainHandler.post(() -> {
            if (!panelShowing) showPanel();
            switchToCollectionView();
            collectionTitle.setText("已收集 " + count + " 条消息");
            btnSendToAi.setEnabled(count > 0);
            btnSendToAi.setAlpha(count > 0 ? 1f : 0.5f);

            if (count == 0) {
                collectionMessages.setText("暂无消息，请在微信聊天界面滚动以收集消息");
                collectionMessages.setTextColor(0xFF999999);
            } else {
                collectionMessages.setText(formattedMessages);
                collectionMessages.setTextColor(0xFF333333);
                // Auto scroll to bottom
                collectionScroll.post(() -> collectionScroll.fullScroll(View.FOCUS_DOWN));
            }
        });
    }

    /** Switch panel to loading state (result view) */
    public void showLoading() {
        mainHandler.post(() -> {
            if (!panelShowing) showPanel();
            switchToResultView();
            progressBar.setVisibility(View.VISIBLE);
            resultText.setText("正在分析聊天内容...");
            resultText.setTextColor(0xFF999999);
        });
    }

    /** Show AI result */
    public void showResult(String text) {
        mainHandler.post(() -> {
            if (!panelShowing) showPanel();
            switchToResultView();
            progressBar.setVisibility(View.GONE);
            resultText.setText(text);
            resultText.setTextColor(0xFF333333);
            resultScroll.scrollTo(0, 0);
        });
    }

    /** Show error in result view */
    public void showError(String error) {
        mainHandler.post(() -> {
            if (!panelShowing) showPanel();
            switchToResultView();
            progressBar.setVisibility(View.GONE);
            resultText.setText("错误: " + error);
            resultText.setTextColor(0xFFFF4444);
        });
    }

    private void switchToCollectionView() {
        if (collectionView != null) collectionView.setVisibility(View.VISIBLE);
        if (resultView != null) resultView.setVisibility(View.GONE);
    }

    private void switchToResultView() {
        if (collectionView != null) collectionView.setVisibility(View.GONE);
        if (resultView != null) resultView.setVisibility(View.VISIBLE);
    }

    // ==================== Panel Creation ====================

    private View createPanel() {
        // Root
        FrameLayout root = new FrameLayout(context);

        // Semi-transparent background overlay (click to minimize)
        View overlay = new View(context);
        overlay.setBackgroundColor(0x40000000);
        overlay.setOnClickListener(v -> hidePanel());
        root.addView(overlay, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        // Panel card at bottom
        int panelHeight = (int) (displayMetrics.heightPixels * 0.55);
        LinearLayout card = new LinearLayout(context);
        card.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable cardBg = new GradientDrawable();
        cardBg.setColor(0xFFF5F5F5);
        cardBg.setCornerRadii(new float[]{dp(16), dp(16), dp(16), dp(16), 0, 0, 0, 0});
        card.setBackground(cardBg);
        card.setElevation(dp(8));

        FrameLayout.LayoutParams cardLp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, panelHeight);
        cardLp.gravity = Gravity.BOTTOM;
        root.addView(card, cardLp);

        // Content switcher
        FrameLayout contentFrame = new FrameLayout(context);

        // === Collection View ===
        collectionView = createCollectionView();
        contentFrame.addView(collectionView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        // === Result View ===
        resultView = createResultView();
        resultView.setVisibility(View.GONE);
        contentFrame.addView(resultView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        card.addView(contentFrame, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT));

        return root;
    }

    private LinearLayout createCollectionView() {
        LinearLayout layout = new LinearLayout(context);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(dp(16), dp(12), dp(16), dp(12));

        // Toolbar
        LinearLayout toolbar = new LinearLayout(context);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);

        collectionTitle = new TextView(context);
        collectionTitle.setText("已收集 0 条消息");
        collectionTitle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        collectionTitle.setTextColor(0xFF333333);
        collectionTitle.setTypeface(null, Typeface.BOLD);
        toolbar.addView(collectionTitle, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView btnClear = createTextButton("清空", 0xFFFF6B6B);
        btnClear.setOnClickListener(v -> {
            if (onClearCollection != null) onClearCollection.run();
        });
        toolbar.addView(btnClear);

        TextView btnMinimize = createTextButton("最小化", 0xFF999999);
        btnMinimize.setOnClickListener(v -> hidePanel());
        toolbar.addView(btnMinimize);

        layout.addView(toolbar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        // Hint
        TextView hint = new TextView(context);
        hint.setText("↕ 最小化后滚动微信聊天可收集更多消息上下文");
        hint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12);
        hint.setTextColor(0xFFAAAAAA);
        LinearLayout.LayoutParams hintLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        hintLp.topMargin = dp(4);
        layout.addView(hint, hintLp);

        // Divider
        layout.addView(createDivider(dp(8)));

        // Messages scroll area
        collectionScroll = new ScrollView(context);
        collectionMessages = new TextView(context);
        collectionMessages.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        collectionMessages.setTextColor(0xFF333333);
        collectionMessages.setLineSpacing(dp(3), 1);
        collectionMessages.setPadding(dp(4), dp(4), dp(4), dp(4));
        collectionMessages.setTextIsSelectable(true);
        collectionScroll.addView(collectionMessages, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT));
        layout.addView(collectionScroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        // Send button
        btnSendToAi = new TextView(context);
        btnSendToAi.setText("发送给 AI");
        btnSendToAi.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        btnSendToAi.setTextColor(Color.WHITE);
        btnSendToAi.setTypeface(null, Typeface.BOLD);
        btnSendToAi.setGravity(Gravity.CENTER);
        GradientDrawable sendBg = new GradientDrawable();
        sendBg.setColor(0xFF1AAD19);
        sendBg.setCornerRadius(dp(8));
        btnSendToAi.setBackground(sendBg);
        btnSendToAi.setPadding(dp(16), dp(12), dp(16), dp(12));
        btnSendToAi.setOnClickListener(v -> {
            if (onSendToAi != null) onSendToAi.run();
        });
        LinearLayout.LayoutParams sendLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        sendLp.topMargin = dp(8);
        layout.addView(btnSendToAi, sendLp);

        return layout;
    }

    private LinearLayout createResultView() {
        LinearLayout layout = new LinearLayout(context);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(dp(16), dp(12), dp(16), dp(12));

        // Toolbar
        LinearLayout toolbar = new LinearLayout(context);
        toolbar.setOrientation(LinearLayout.HORIZONTAL);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);

        TextView title = new TextView(context);
        title.setText("AI 回复建议");
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        title.setTextColor(0xFF333333);
        title.setTypeface(null, Typeface.BOLD);
        toolbar.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView btnCopy = createTextButton("复制", 0xFF1AAD19);
        btnCopy.setOnClickListener(v -> copyResultText());
        toolbar.addView(btnCopy);

        TextView btnBack = createTextButton("返回", 0xFF999999);
        btnBack.setOnClickListener(v -> switchToCollectionView());
        toolbar.addView(btnBack);

        layout.addView(toolbar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        // Divider
        layout.addView(createDivider(dp(8)));

        // Progress
        progressBar = new ProgressBar(context);
        progressBar.setVisibility(View.GONE);
        LinearLayout.LayoutParams progressLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        progressLp.gravity = Gravity.CENTER;
        progressLp.topMargin = dp(24);
        layout.addView(progressBar, progressLp);

        // Result scroll
        resultScroll = new ScrollView(context);
        resultText = new TextView(context);
        resultText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        resultText.setTextColor(0xFF333333);
        resultText.setLineSpacing(dp(4), 1);
        resultText.setPadding(dp(4), dp(8), dp(4), dp(4));
        resultText.setTextIsSelectable(true);
        resultScroll.addView(resultText, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT));
        layout.addView(resultScroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        return layout;
    }

    // ==================== Helpers ====================

    private TextView createTextButton(String text, int color) {
        TextView btn = new TextView(context);
        btn.setText(text);
        btn.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        btn.setTextColor(color);
        btn.setPadding(dp(12), dp(6), dp(12), dp(6));
        return btn;
    }

    private View createDivider(int verticalMargin) {
        View divider = new View(context);
        divider.setBackgroundColor(0xFFDDDDDD);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 1);
        lp.topMargin = verticalMargin;
        lp.bottomMargin = verticalMargin;
        divider.setLayoutParams(lp);
        return divider;
    }

    private void copyResultText() {
        if (resultText == null || resultText.getText() == null) return;
        String text = resultText.getText().toString();
        if (text.isEmpty() || text.startsWith("正在") || text.startsWith("错误")) return;

        ClipboardManager clipboard = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
        clipboard.setPrimaryClip(ClipData.newPlainText("AI Reply", text));
        Toast.makeText(context, "已复制到剪贴板", Toast.LENGTH_SHORT).show();
    }

    private WindowManager.LayoutParams createPanelLayoutParams() {
        WindowManager.LayoutParams p = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.MATCH_PARENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        p.gravity = Gravity.TOP | Gravity.START;
        return p;
    }

    public void destroy() {
        hideButton();
        hidePanel();
    }

    private int dp(int value) {
        return (int) (value * displayMetrics.density);
    }
}
