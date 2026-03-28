package com.example.aura_project

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipboardManager
import android.content.ClipData
import android.content.Context
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONObject
import kotlin.math.abs
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * ActionExecutor - Executes atomic UI actions (primitives)
 * 
 * The 5 core primitives:
 * 1. click(element_id) - Click a button/clickable element
 * 2. type(element_id, text) - Type text into a field
 * 3. scroll(direction) - Scroll in a direction (up/down/left/right)
 * 4. wait(duration_ms) - Wait for N milliseconds
 * 5. global_action(action_type) - HOME, BACK, RECENTS, POWER, etc.
 * 
 * Handles:
 * - Finding elements by ID in the accessibility tree
 * - Executing actions with proper error handling
 * - Retrying failed actions (delegated to caller via result)
 * - Returning action results with new UI state
 */
class ActionExecutor(
    private val service: AccessibilityService,
    private val uiTreeParser: UITreeParser
) {
    companion object {
        private const val TAG = "ActionExecutor"
        private const val PASTE_DELAY_MS = 300L
        private const val ACTION_EXECUTE_DELAY_MS = 500L
    }

    /**
     * Main execution entry point
     * Takes a JSON action and executes it
     * Returns the result as a JSON object
     */
    fun executeAction(actionJson: JSONObject): JSONObject {
        val startTime = System.currentTimeMillis()
        val actionId = actionJson.optString("action_id", "unknown")
        val actionType = actionJson.optString("action_type", "unknown")

        Log.d(TAG, "🎯 Executing action: $actionType (ID: $actionId)")

        val result = JSONObject()
        result.put("action_id", actionId)

        try {
            val success = when (actionType) {
                "click" -> handleClick(actionJson)
                "type" -> handleType(actionJson)
                "scroll" -> handleScroll(actionJson)
                "swipe" -> handleSwipe(actionJson)
                "wait" -> handleWait(actionJson)
                "global_action" -> handleGlobalAction(actionJson)
                "long_click" -> handleLongClick(actionJson)
                "double_click" -> handleDoubleClick(actionJson)
                else -> {
                    Log.w(TAG, "Unknown action type: $actionType")
                    false
                }
            }

            result.put("success", success)
            
            // Capture new UI state after action
            Thread.sleep(ACTION_EXECUTE_DELAY_MS)
            val newRootNode = service.rootInActiveWindow
            if (newRootNode != null) {
                val newTree = uiTreeParser.parseAccessibilityTree(newRootNode)
                result.put("new_tree", newTree)
            }

        } catch (e: Exception) {
            Log.e(TAG, "❌ Error executing action $actionType: ${e.message}", e)
            result.put("success", false)
            result.put("error", e.message)
        }

        val executionTime = System.currentTimeMillis() - startTime
        result.put("execution_time_ms", executionTime)
        
        Log.d(TAG, "✅ Action $actionType completed in ${executionTime}ms: ${result.optBoolean("success")}")
        return result
    }

    /**
     * PRIMITIVE 1: Click
     * Finds an element by ID and clicks it
     */
    private fun handleClick(actionJson: JSONObject): Boolean {
        val elementId = actionJson.optInt("element_id", -1)
        if (elementId < 0) {
            Log.e(TAG, "Click: Invalid element ID")
            return false
        }

        Log.d(TAG, "Click: Looking for element $elementId")
        val rootNode = service.rootInActiveWindow ?: return false
        
        val element = findElementById(rootNode, elementId)
        if (element == null) {
            Log.e(TAG, "Click: Element $elementId not found")
            return false
        }

        if (!element.isClickable) {
            Log.e(TAG, "Click: Element $elementId is not clickable")
            return false
        }

        Log.d(TAG, "Click: Performing click on element $elementId")
        return element.performAction(AccessibilityNodeInfo.ACTION_CLICK)
    }

    /**
     * PRIMITIVE 2: Type
     * Finds a text field by ID and types text into it
     * Uses clipboard to handle special characters
     */
    private fun handleType(actionJson: JSONObject): Boolean {
        val elementId = actionJson.optInt("element_id", -1)
        val text = actionJson.optString("text", "")
        val clearFirst = actionJson.optBoolean("clear_first", false)

        if (elementId < 0 || text.isEmpty()) {
            Log.e(TAG, "Type: Invalid element ID or text")
            return false
        }

        Log.d(TAG, "Type: Looking for element $elementId")
        val rootNode = service.rootInActiveWindow ?: return false
        
        val element = findElementById(rootNode, elementId)
        if (element == null) {
            Log.e(TAG, "Type: Element $elementId not found")
            return false
        }

        if (!element.isFocusable) {
            Log.e(TAG, "Type: Element $elementId is not focusable")
            return false
        }

        Log.d(TAG, "Type: Typing '$text' into element $elementId")
        
        // Click to focus
        element.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        element.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        Thread.sleep(300)

        var clearSuccess = true
        if (clearFirst) {
            // Select existing text, then clear the field.
            val existingLength = element.text?.length ?: 0
            if (existingLength > 0) {
                val selectionArgs = Bundle().apply {
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, 0)
                    putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, existingLength)
                }
                element.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selectionArgs)
            }

            val clearArgs = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "")
            }
            clearSuccess = element.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, clearArgs)
            Thread.sleep(120)
        }

        // Prefer direct set-text for deterministic replacement.
        val setTextArgs = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val setTextSuccess = element.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, setTextArgs)
        Thread.sleep(120)
        val setApplied = verifyElementTextContains(elementId, text)

        if (setTextSuccess && setApplied) {
            Log.d(TAG, "Type: SetText action result: true (clear=$clearSuccess)")
            return true
        }

        // Copy text to clipboard
        val clipboard = service.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = ClipData.newPlainText("text", text)
        clipboard.setPrimaryClip(clip)
        
        Thread.sleep(PASTE_DELAY_MS)

        // Paste
        val pasteSuccess = element.performAction(AccessibilityNodeInfo.ACTION_PASTE)
        Thread.sleep(300)
        val pasteApplied = verifyElementTextContains(elementId, text)

        Log.d(TAG, "Type: Paste action result: $pasteSuccess (clear=$clearSuccess, setText=$setTextSuccess, setApplied=$setApplied, pasteApplied=$pasteApplied)")
        return pasteSuccess && pasteApplied
    }

    /**
     * PRIMITIVE 3: Scroll
     * Scrolls in the specified direction on the current view
     * Note: Left/right scrolling is limited - primarily supports up/down
     */
    private fun handleScroll(actionJson: JSONObject): Boolean {
        val direction = actionJson.optString("direction", "down").lowercase()
        
        Log.d(TAG, "Scroll: Direction=$direction")

        val rootNode = service.rootInActiveWindow
        if (rootNode == null) {
            Log.e(TAG, "Scroll: Root node is null")
            return false
        }

        // Find scrollable container
        val scrollableNode = findFirstScrollableNode(rootNode)
        if (scrollableNode == null) {
            Log.w(TAG, "Scroll: No scrollable element found — falling back to swipe gesture")
            return when (direction) {
                "up" -> performSwipeByPercent(50, 90, 50, 10, actionJson.optInt("duration", 800))
                "down" -> performSwipeByPercent(50, 10, 50, 90, actionJson.optInt("duration", 800))
                "left" -> performSwipeByPercent(90, 50, 10, 50, actionJson.optInt("duration", 800))
                "right" -> performSwipeByPercent(10, 50, 90, 50, actionJson.optInt("duration", 800))
                else -> performSwipeByPercent(50, 90, 50, 10, actionJson.optInt("duration", 800))
            }
        }

        // Accessibility direction: FORWARD usually corresponds to finger swipe up.
        val action = when (direction) {
            "up", "left" -> AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            "down", "right" -> AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            else -> AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
        }

        Log.d(TAG, "Scroll: Performing scroll action on scrollable element")
        return scrollableNode.performAction(action)
    }

    /**
     * PRIMITIVE: Swipe
     * Executes gesture by percentages of the display dimensions.
     */
    private fun handleSwipe(actionJson: JSONObject): Boolean {
        val startXPercent = actionJson.optInt("start_x_percent", 50)
        val startYPercent = actionJson.optInt("start_y_percent", 80)
        val endXPercent = actionJson.optInt("end_x_percent", 50)
        val endYPercent = actionJson.optInt("end_y_percent", 20)
        val duration = actionJson.optInt("duration", 600)

        return performSwipeByPercent(
            startXPercent,
            startYPercent,
            endXPercent,
            endYPercent,
            duration,
        )
    }

    private fun performSwipeByPercent(
        startXPercent: Int,
        startYPercent: Int,
        endXPercent: Int,
        endYPercent: Int,
        durationMs: Int,
    ): Boolean {
        val dm = service.resources.displayMetrics
        val startX = (dm.widthPixels * (startXPercent.coerceIn(0, 100) / 100f))
        val startY = (dm.heightPixels * (startYPercent.coerceIn(0, 100) / 100f))
        val endX = (dm.widthPixels * (endXPercent.coerceIn(0, 100) / 100f))
        val endY = (dm.heightPixels * (endYPercent.coerceIn(0, 100) / 100f))

        Log.d(
            TAG,
            "Swipe: (${startXPercent}%,${startYPercent}%) -> (${endXPercent}%,${endYPercent}%) " +
                "| px=(${startX.toInt()},${startY.toInt()}) -> (${endX.toInt()},${endY.toInt()}) " +
                "| duration=${durationMs}ms"
        )

        return performSwipeGesture(startX, startY, endX, endY, durationMs.toLong())
    }

    private fun performSwipeGesture(
        startX: Float,
        startY: Float,
        endX: Float,
        endY: Float,
        durationMs: Long,
    ): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N) {
            Log.e(TAG, "Swipe: Gesture API requires Android N+")
            return false
        }

        val path = Path().apply {
            moveTo(startX, startY)
            lineTo(endX, endY)
        }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.coerceAtLeast(100L)))
            .build()

        val latch = CountDownLatch(1)
        var completed = false

        val dispatched = service.dispatchGesture(
            gesture,
            object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    completed = true
                    latch.countDown()
                }

                override fun onCancelled(gestureDescription: GestureDescription?) {
                    completed = false
                    latch.countDown()
                }
            },
            Handler(Looper.getMainLooper())
        )

        if (!dispatched) {
            Log.e(TAG, "Swipe: dispatchGesture returned false")
            return false
        }

        val done = latch.await((durationMs + 1200L).coerceAtLeast(1500L), TimeUnit.MILLISECONDS)
        if (!done) {
            Log.w(TAG, "Swipe: gesture callback timeout")
        }
        return done && completed
    }

    /**
     * PRIMITIVE 4: Wait
     * Simple delay to allow UI to update
     */
    private fun handleWait(actionJson: JSONObject): Boolean {
        val duration = actionJson.optInt("duration", 1000)
        
        Log.d(TAG, "Wait: Waiting ${duration}ms")
        Thread.sleep(duration.toLong())
        Log.d(TAG, "Wait: Done")
        return true
    }

    /**
     * PRIMITIVE 5: Global Action
     * Performs system-level actions: HOME, BACK, RECENTS, POWER, etc.
     */
    private fun handleGlobalAction(actionJson: JSONObject): Boolean {
        val globalActionStr = actionJson.optString("global_action", "BACK").uppercase()

        val action = when (globalActionStr) {
            "HOME" -> AccessibilityService.GLOBAL_ACTION_HOME
            "BACK" -> AccessibilityService.GLOBAL_ACTION_BACK
            "RECENTS" -> AccessibilityService.GLOBAL_ACTION_RECENTS
            "POWER" -> AccessibilityService.GLOBAL_ACTION_POWER_DIALOG
            else -> AccessibilityService.GLOBAL_ACTION_BACK
        }

        Log.d(TAG, "GlobalAction: Performing $globalActionStr")
        return service.performGlobalAction(action)
    }

    /**
     * BONUS: Long Click
     */
    private fun handleLongClick(actionJson: JSONObject): Boolean {
        val elementId = actionJson.optInt("element_id", -1)
        val duration = actionJson.optInt("duration", 1000)

        if (elementId < 0) {
            Log.e(TAG, "LongClick: Invalid element ID")
            return false
        }

        val rootNode = service.rootInActiveWindow ?: return false
        val element = findElementById(rootNode, elementId)
        if (element == null) {
            Log.e(TAG, "LongClick: Element $elementId not found")
            return false
        }

        Log.d(TAG, "LongClick: Performing long click on element $elementId for ${duration}ms")
        element.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        Thread.sleep(duration.toLong())
        return true
    }

    /**
     * BONUS: Double Click
     */
    private fun handleDoubleClick(actionJson: JSONObject): Boolean {
        val elementId = actionJson.optInt("element_id", -1)

        if (elementId < 0) {
            Log.e(TAG, "DoubleClick: Invalid element ID")
            return false
        }

        val rootNode = service.rootInActiveWindow ?: return false
        val element = findElementById(rootNode, elementId)
        if (element == null) {
            Log.e(TAG, "DoubleClick: Element $elementId not found")
            return false
        }

        Log.d(TAG, "DoubleClick: Performing double click on element $elementId")
        element.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        Thread.sleep(100)
        element.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        Thread.sleep(100)
        return true
    }

    // ========================================================================
    // HELPER METHODS
    // ========================================================================

    /**
     * Find element by ID in the tree
     * This assumes the element_id from the semantic tree is the traversal order
     */
    private fun findElementById(rootNode: AccessibilityNodeInfo, targetId: Int): AccessibilityNodeInfo? {
        var currentId = 0
        return findElementByIdRecursive(rootNode, targetId, { currentId++ })
    }

    private fun findElementByIdRecursive(
        node: AccessibilityNodeInfo?,
        targetId: Int,
        idCounter: () -> Int
    ): AccessibilityNodeInfo? {
        if (node == null) return null

        val currentId = idCounter()
        if (currentId == targetId) {
            return node
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                val result = findElementByIdRecursive(child, targetId, idCounter)
                if (result != null) {
                    return result
                }
                child.recycle()
            }
        }

        return null
    }

    private fun verifyElementTextContains(elementId: Int, expected: String): Boolean {
        if (expected.isEmpty()) return true
        val rootNode = service.rootInActiveWindow ?: return false
        val element = findElementById(rootNode, elementId) ?: return false
        val live = (element.text?.toString() ?: "").trim()
        return live == expected || live.contains(expected)
    }

    /**
     * Find first scrollable element in tree
     */
    private fun findFirstScrollableNode(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isScrollable) {
            return node
        }

        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                val result = findFirstScrollableNode(child)
                if (result != null) {
                    return result
                }
                child.recycle()
            }
        }

        return null
    }
}
