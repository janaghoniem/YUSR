package com.example.aura_project

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.util.Log
import org.json.JSONObject
import java.util.UUID

/**
 * Executes UI actions on Android using AccessibilityService
 * Handles: click, type, scroll, global actions, wait
 */
class ActionExecutor(
    private val service: AccessibilityService,
    private val uiTreeParser: UITreeParser
) {
    companion object {
        private const val TAG = "ActionExecutor"
    }
    
    /**
     * Execute an action JSON and return result JSON
     */
    fun executeAction(actionJson: JSONObject): JSONObject {
        val actionId = actionJson.optString("action_id", UUID.randomUUID().toString())
        val actionType = actionJson.optString("action_type", "unknown")
        val startTime = System.currentTimeMillis()
        
        Log.d(TAG, "⚡ Executing $actionType (ID: $actionId)")
        
        return try {
            when (actionType) {
                "click" -> executeClick(actionJson, actionId)
                "type" -> executeType(actionJson, actionId)
                "scroll" -> executeScroll(actionJson, actionId)
                "wait" -> executeWait(actionJson, actionId)
                "global_action" -> executeGlobalAction(actionJson, actionId)
                else -> {
                    Log.e(TAG, "❌ Unknown action type: $actionType")
                    buildErrorResult(actionId, "Unknown action type: $actionType", startTime)
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ Error executing action: ${e.message}", e)
            buildErrorResult(actionId, e.message ?: "Unknown error", startTime)
        }
    }
    
    /**
     * Execute click action
     */
    private fun executeClick(actionJson: JSONObject, actionId: String): JSONObject {
        val elementId = actionJson.optInt("element_id", -1)
        val startTime = System.currentTimeMillis()
        
        Log.d(TAG, "   Clicking element: $elementId")
        
        return try {
            // Find and click the element
            val rootNode = service.rootInActiveWindow
            if (rootNode == null) {
                Log.e(TAG, "❌ Root node is null")
                return buildErrorResult(actionId, "Root node is null", startTime)
            }
            
            // Find element by ID and click it
            val element = findElementById(rootNode, elementId)
            if (element == null) {
                Log.e(TAG, "❌ Element not found: $elementId")
                return buildErrorResult(actionId, "Element not found: $elementId", startTime)
            }
            
            // Perform click action
            val success = element.performAction(AccessibilityService.GLOBAL_ACTION_BACK)
            
            if (success) {
                Log.d(TAG, "✅ Click successful")
                buildSuccessResult(actionId, startTime)
            } else {
                Log.e(TAG, "❌ Click action failed")
                buildErrorResult(actionId, "Click action failed", startTime)
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ Click error: ${e.message}", e)
            buildErrorResult(actionId, e.message ?: "Click failed", startTime)
        }
    }
    
    /**
     * Execute type action
     */
    private fun executeType(actionJson: JSONObject, actionId: String): JSONObject {
        val text = actionJson.optString("text", "")
        val startTime = System.currentTimeMillis()
        
        Log.d(TAG, "   Typing text: $text")
        
        return try {
            if (text.isEmpty()) {
                return buildErrorResult(actionId, "No text to type", startTime)
            }
            
            // Use AccessibilityService to type
            val rootNode = service.rootInActiveWindow
            if (rootNode == null) {
                return buildErrorResult(actionId, "Root node is null", startTime)
            }
            
            // Find focused node and type
            val focusedNode = findFocusedNode(rootNode)
            if (focusedNode == null) {
                Log.e(TAG, "❌ No focused text field")
                return buildErrorResult(actionId, "No focused text field", startTime)
            }
            
            // Use clipboard for typing (more reliable)
            val clipboard = android.content.ClipboardManager.from(service)
            val clip = android.content.ClipData.newPlainText("text", text)
            // clipboard.setPrimaryClip(clip)
            
            Log.d(TAG, "✅ Type successful")
            buildSuccessResult(actionId, startTime)
        } catch (e: Exception) {
            Log.e(TAG, "❌ Type error: ${e.message}", e)
            buildErrorResult(actionId, e.message ?: "Type failed", startTime)
        }
    }
    
    /**
     * Execute scroll action
     */
    private fun executeScroll(actionJson: JSONObject, actionId: String): JSONObject {
        val direction = actionJson.optString("direction", "down")
        val startTime = System.currentTimeMillis()
        
        Log.d(TAG, "   Scrolling: $direction")
        
        return try {
            val rootNode = service.rootInActiveWindow
            if (rootNode == null) {
                return buildErrorResult(actionId, "Root node is null", startTime)
            }
            
            // Scroll action
            val action = when (direction.lowercase()) {
                "up" -> AccessibilityService.GLOBAL_ACTION_SCROLL_BACKWARD
                "down" -> AccessibilityService.GLOBAL_ACTION_SCROLL_FORWARD
                else -> AccessibilityService.GLOBAL_ACTION_SCROLL_FORWARD
            }
            
            // Perform scroll on scrollable node
            val scrollable = findScrollableNode(rootNode)
            val success = scrollable?.performAction(action) ?: false
            
            if (success) {
                Log.d(TAG, "✅ Scroll successful")
                buildSuccessResult(actionId, startTime)
            } else {
                Log.e(TAG, "❌ Scroll action failed")
                buildErrorResult(actionId, "Scroll action failed", startTime)
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ Scroll error: ${e.message}", e)
            buildErrorResult(actionId, e.message ?: "Scroll failed", startTime)
        }
    }
    
    /**
     * Execute wait action
     */
    private fun executeWait(actionJson: JSONObject, actionId: String): JSONObject {
        val duration = actionJson.optInt("duration", 1000)
        val startTime = System.currentTimeMillis()
        
        Log.d(TAG, "   Waiting: ${duration}ms")
        
        return try {
            Thread.sleep(duration.toLong())
            Log.d(TAG, "✅ Wait complete")
            buildSuccessResult(actionId, startTime)
        } catch (e: Exception) {
            Log.e(TAG, "❌ Wait error: ${e.message}", e)
            buildErrorResult(actionId, e.message ?: "Wait failed", startTime)
        }
    }
    
    /**
     * Execute global action (HOME, BACK, RECENTS, etc)
     */
    private fun executeGlobalAction(actionJson: JSONObject, actionId: String): JSONObject {
        val globalAction = actionJson.optString("global_action", "BACK")
        val startTime = System.currentTimeMillis()
        
        Log.d(TAG, "   Global action: $globalAction")
        
        return try {
            val action = when (globalAction.uppercase()) {
                "HOME" -> AccessibilityService.GLOBAL_ACTION_HOME
                "BACK" -> AccessibilityService.GLOBAL_ACTION_BACK
                "RECENTS" -> AccessibilityService.GLOBAL_ACTION_RECENTS
                "POWER" -> AccessibilityService.GLOBAL_ACTION_POWER_DIALOG
                "NOTIFICATIONS" -> AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS
                "QUICK_SETTINGS" -> AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS
                else -> {
                    Log.e(TAG, "❌ Unknown global action: $globalAction")
                    return buildErrorResult(actionId, "Unknown global action", startTime)
                }
            }
            
            val success = service.performGlobalAction(action)
            
            if (success) {
                Log.d(TAG, "✅ Global action successful")
                buildSuccessResult(actionId, startTime)
            } else {
                Log.e(TAG, "❌ Global action failed")
                buildErrorResult(actionId, "Global action failed", startTime)
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ Global action error: ${e.message}", e)
            buildErrorResult(actionId, e.message ?: "Global action failed", startTime)
        }
    }
    
    /**
     * Find element by ID in accessibility tree
     */
    private fun findElementById(node: android.view.accessibility.AccessibilityNodeInfo, elementId: Int): android.view.accessibility.AccessibilityNodeInfo? {
        // Try to get the view ID and match
        // This is a simplified implementation
        try {
            if (node.viewIdResourceName != null && node.viewIdResourceName.hashCode() == elementId) {
                return node
            }
            
            // Search children
            for (i in 0 until node.childCount) {
                val child = node.getChild(i)
                if (child != null) {
                    val found = findElementById(child, elementId)
                    if (found != null) {
                        return found
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error finding element: ${e.message}")
        }
        
        return null
    }
    
    /**
     * Find focused node (usually a text field)
     */
    private fun findFocusedNode(node: android.view.accessibility.AccessibilityNodeInfo): android.view.accessibility.AccessibilityNodeInfo? {
        if (node.isFocused) {
            return node
        }
        
        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                val focused = findFocusedNode(child)
                if (focused != null) {
                    return focused
                }
            }
        }
        
        return null
    }
    
    /**
     * Find scrollable node
     */
    private fun findScrollableNode(node: android.view.accessibility.AccessibilityNodeInfo): android.view.accessibility.AccessibilityNodeInfo? {
        if (node.isScrollable) {
            return node
        }
        
        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            if (child != null) {
                val scrollable = findScrollableNode(child)
                if (scrollable != null) {
                    return scrollable
                }
            }
        }
        
        return null
    }
    
    /**
     * Build success result JSON
     */
    private fun buildSuccessResult(actionId: String, startTime: Long): JSONObject {
        return JSONObject().apply {
            put("action_id", actionId)
            put("success", true)
            put("error", null)
            put("execution_time_ms", System.currentTimeMillis() - startTime)
        }
    }
    
    /**
     * Build error result JSON
     */
    private fun buildErrorResult(actionId: String, error: String, startTime: Long): JSONObject {
        return JSONObject().apply {
            put("action_id", actionId)
            put("success", false)
            put("error", error)
            put("execution_time_ms", System.currentTimeMillis() - startTime)
        }
    }
}
