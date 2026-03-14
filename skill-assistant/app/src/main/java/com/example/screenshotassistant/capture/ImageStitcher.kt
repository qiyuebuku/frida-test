package com.example.screenshotassistant.capture

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Rect
import android.util.Log

object ImageStitcher {

    private const val TAG = "ImageStitcher"

    fun stitch(frames: List<Bitmap>): Bitmap {
        if (frames.size == 1) return frames[0]

        val H = frames[0].height
        val W = frames[0].width
        Log.d(TAG, "stitch: ${frames.size} frames, each ${W}x$H")

        // 1) 检测内容区域边界（对比前两帧，找出固定的顶部/底部）
        val (contentTop, contentBottom) = detectContentBounds(frames[0], frames[1])
        val contentH = contentBottom - contentTop
        Log.d(TAG, "contentBounds: top=$contentTop, bottom=$contentBottom, contentH=$contentH, navBar=${H - contentBottom}px")

        if (contentH <= 0) {
            Log.w(TAG, "Cannot detect content bounds, fallback to simple concat")
            return frames[0]
        }

        // 2) 对每对相邻帧，用全局行哈希匹配测量实际滚动距离
        val scrollDistances = mutableListOf<Int>()
        val validFrameIndices = mutableListOf(0)

        for (i in 1 until frames.size) {
            val d = measureScrollDistance(frames[validFrameIndices.last()], frames[i], contentTop, contentBottom)
            if (d <= 0) {
                Log.d(TAG, "frame $i: scroll=0, skipped")
                continue
            }
            scrollDistances.add(d)
            validFrameIndices.add(i)
            Log.d(TAG, "frame $i: scroll=$d px (overlap=${contentH - d}px)")
        }

        if (scrollDistances.isEmpty()) return frames[0]

        val validFrames = validFrameIndices.map { frames[it] }
        return assembleFrames(validFrames, scrollDistances, contentTop, contentBottom)
    }

    /**
     * 组装最终长图。
     * frame1: [0, contentBottom) — 状态栏 + 标签栏 + 全部内容区
     * 中间帧: 只取新出现的内容行
     * 最后帧: 新内容 + 底部导航栏
     */
    private fun assembleFrames(
        frames: List<Bitmap>,
        scrollDistances: List<Int>,
        contentTop: Int,
        contentBottom: Int
    ): Bitmap {
        val H = frames[0].height
        val W = frames[0].width
        val navBarH = H - contentBottom

        var totalHeight = contentBottom
        for (d in scrollDistances) totalHeight += d
        totalHeight += navBarH

        val result = Bitmap.createBitmap(W, totalHeight, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(result)

        // Frame 1: [0, contentBottom)
        canvas.drawBitmap(frames[0], Rect(0, 0, W, contentBottom), Rect(0, 0, W, contentBottom), null)
        var y = contentBottom

        for (i in scrollDistances.indices) {
            val frame = frames[i + 1]
            val d = scrollDistances[i]
            // 新内容 = 当前帧内容区底部 d 像素
            val cropTop = contentBottom - d

            if (d > 0 && cropTop >= contentTop) {
                val isLast = (i == scrollDistances.size - 1)
                val cropBottom = if (isLast) H else contentBottom

                canvas.drawBitmap(frame,
                    Rect(0, cropTop, W, cropBottom),
                    Rect(0, y, W, y + (cropBottom - cropTop)),
                    null)
                y += cropBottom - cropTop
                Log.d(TAG, "  paste frame ${i+1}: crop [$cropTop, $cropBottom) -> y=$y")
            }
        }

        Log.d(TAG, "assembled: ${frames.size} frames -> ${W}x$y (expected $totalHeight)")
        return if (y < totalHeight) Bitmap.createBitmap(result, 0, 0, W, y) else result
    }

    // ========== 固定区域检测 ==========

    private fun detectContentBounds(frame1: Bitmap, frame2: Bitmap): Pair<Int, Int> {
        val H = frame1.height
        val W = frame1.width
        val pixels1 = IntArray(W)
        val pixels2 = IntArray(W)

        var contentTop = 0
        for (y in 0 until H) {
            frame1.getPixels(pixels1, 0, W, 0, y, W, 1)
            frame2.getPixels(pixels2, 0, W, 0, y, W, 1)
            if (!rowPixelsMatch(pixels1, pixels2, W)) {
                contentTop = y
                break
            }
        }

        var contentBottom = H
        for (y in H - 1 downTo contentTop + 1) {
            frame1.getPixels(pixels1, 0, W, 0, y, W, 1)
            frame2.getPixels(pixels2, 0, W, 0, y, W, 1)
            if (!rowPixelsMatch(pixels1, pixels2, W)) {
                contentBottom = y + 1
                break
            }
        }

        return Pair(contentTop, contentBottom)
    }

    private fun rowPixelsMatch(row1: IntArray, row2: IntArray, width: Int): Boolean {
        for (x in 0 until width step 8) {
            if (!pixelClose(row1[x], row2[x])) return false
        }
        return true
    }

    // ========== 滚动距离测量（全局行哈希评分法） ==========

    /**
     * 核心改进：对每个可能的滚动距离 d，计算重叠区域的行哈希匹配数。
     * 匹配数最多的 d 就是真实滚动距离。
     * 比单行匹配可靠得多，不会因单行巧合匹配而误判。
     */
    private fun measureScrollDistance(
        prev: Bitmap, curr: Bitmap,
        contentTop: Int, contentBottom: Int
    ): Int {
        val W = prev.width
        val contentH = contentBottom - contentTop

        // 计算两帧内容区域的行哈希
        val prevHashes = LongArray(contentH)
        val currHashes = LongArray(contentH)
        val rowBuf = IntArray(W)

        for (r in 0 until contentH) {
            prev.getPixels(rowBuf, 0, W, 0, contentTop + r, W, 1)
            prevHashes[r] = rowHash(rowBuf, W)
            curr.getPixels(rowBuf, 0, W, 0, contentTop + r, W, 1)
            currHashes[r] = rowHash(rowBuf, W)
        }

        // 对每个可能的滚动距离 d，计算重叠行数中哈希匹配的比例
        val minD = contentH / 10
        val maxD = contentH * 95 / 100
        var bestD = 0
        var bestScore = 0

        for (d in minD..maxD) {
            val overlapH = contentH - d
            if (overlapH < 20) continue

            var score = 0
            // prev 的 [d, contentH) 应该与 curr 的 [0, overlapH) 匹配
            // 采样检查（每隔几行检查一次，加速）
            val step = maxOf(1, overlapH / 100)
            var checked = 0
            for (r in 0 until overlapH step step) {
                checked++
                if (prevHashes[d + r] == currHashes[r]) score++
            }

            if (score > bestScore) {
                bestScore = score
                bestD = d
            }
        }

        // 匹配率阈值：至少 60% 的行匹配
        val overlapH = contentH - bestD
        val step = maxOf(1, overlapH / 100)
        val totalChecked = (overlapH + step - 1) / step
        val matchRate = if (totalChecked > 0) bestScore.toFloat() / totalChecked else 0f

        Log.d(TAG, "  measureScroll: bestD=$bestD, matchRate=${(matchRate * 100).toInt()}% ($bestScore/$totalChecked)")

        if (matchRate < 0.5f) {
            Log.w(TAG, "  low match rate, trying block match fallback")
            val fallbackD = measureScrollDistanceBlockMatch(prev, curr, contentTop, contentBottom)
            if (fallbackD > 0) {
                Log.d(TAG, "  fallback block match: d=$fallbackD")
                return fallbackD
            }
        }

        return if (matchRate >= 0.25f) bestD else 0
    }

    /**
     * 备选方法：取 prev 底部内容区的一个 20 行块，在 curr 中搜索位置。
     */
    private fun measureScrollDistanceBlockMatch(
        prev: Bitmap, curr: Bitmap,
        contentTop: Int, contentBottom: Int
    ): Int {
        val W = prev.width
        val contentH = contentBottom - contentTop
        val blockSize = 20
        val rowBuf = IntArray(W)

        // 从 prev 内容区的中间偏下取一个 20 行的指纹块
        val blockStart = contentTop + contentH * 6 / 10
        if (blockStart + blockSize > contentBottom) return 0

        val blockHashes = LongArray(blockSize)
        for (r in 0 until blockSize) {
            prev.getPixels(rowBuf, 0, W, 0, blockStart + r, W, 1)
            blockHashes[r] = rowHash(rowBuf, W)
        }

        // 在 curr 内容区中搜索这个块
        for (y in contentTop until contentBottom - blockSize) {
            curr.getPixels(rowBuf, 0, W, 0, y, W, 1)
            if (rowHash(rowBuf, W) != blockHashes[0]) continue

            // 首行匹配，验证整个块
            var match = true
            for (r in 1 until blockSize) {
                curr.getPixels(rowBuf, 0, W, 0, y + r, W, 1)
                if (rowHash(rowBuf, W) != blockHashes[r]) {
                    match = false
                    break
                }
            }
            if (match) {
                val d = blockStart - y
                if (d > 0 && d < contentH) return d
            }
        }
        return 0
    }

    // ========== 辅助方法 ==========

    private fun rowHash(pixels: IntArray, width: Int): Long {
        var hash = 0L
        for (x in 0 until width step 4) {
            val p = pixels[x]
            // 量化像素值（每通道除以 32），容忍微小颜色差异（如实时数据更新）
            val r = ((p shr 16) and 0xFF) / 32
            val g = ((p shr 8) and 0xFF) / 32
            val b = (p and 0xFF) / 32
            val q = (r shl 6) or (g shl 3) or b
            hash = hash * 31 + q.toLong()
        }
        return hash
    }

    private fun pixelClose(p1: Int, p2: Int): Boolean {
        val r1 = (p1 shr 16) and 0xFF; val r2 = (p2 shr 16) and 0xFF
        val g1 = (p1 shr 8) and 0xFF;  val g2 = (p2 shr 8) and 0xFF
        val b1 = p1 and 0xFF;           val b2 = p2 and 0xFF
        return kotlin.math.abs(r1 - r2) < 20 &&
               kotlin.math.abs(g1 - g2) < 20 &&
               kotlin.math.abs(b1 - b2) < 20
    }
}
