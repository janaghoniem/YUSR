package com.example.aura_project

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.util.Log
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Service that monitors UI changes and sends UI tree to backend
 * Runs in background, sends updates on screen changes
 */
class UITreeBroadcastService(
    private val deviceId: String,
    private val backendUrl: String,
    private val service: AccessibilityService,
    private val uiTreeParser: UITreeParser
) {
    companion object {
        private const val TAG = "UITreeBroadcast"
        private const val SEND_INTERVAL_MS = 3000L  // Send every 3 seconds
    }
    
    private var broadcastJob: Job? = null
    private var isRunning = false
    private var lastScreenId: String? = null
    
    /**
     * Start broadcasting UI tree updates
     */
    fun startBroadcasting() {
        if (isRunning) {
            Log.w(TAG, "⚠️ Already broadcasting")
            return
        }
        
        Log.d(TAG, "📡 Starting UI tree broadcasting...")
        isRunning = true
        
        broadcastJob = CoroutineScope(Dispatchers.IO).launch {
            while (isRunning) {
                try {
                    sendUITreeUpdate()
                    delay(SEND_INTERVAL_MS)
                } catch (e: Exception) {
                    Log.e(TAG, "❌ Error in broadcast loop: ${e.message}", e)
                    delay(5000)
                }
            }
        }
        
        Log.d(TAG, "✅ UI tree broadcasting started")
    }
    
    /**
     * Stop broadcasting
     */
    fun stopBroadcasting() {
        Log.d(TAG, "🛑 Stopping UI tree broadcasting...")
        isRunning = false
        broadcastJob?.cancel()
        broadcastJob = null
        Log.d(TAG, "✅ UI tree broadcasting stopped")
    }
    
    /**
     * Send current UI tree to backend
     */
    private suspend fun sendUITreeUpdate() {
        try {
            val rootNode = service.rootInActiveWindow
            
            if (rootNode == null) {
                Log.d(TAG, "⚠️ Root node is null, skipping update")
                return
            }
            
            // Parse UI tree
            val uiTreeJson = uiTreeParser.parseAccessibilityTree(rootNode)
            val screenId = uiTreeJson.optString("screen_id", "unknown")
            
            // Only send if screen changed (to reduce spam)
            if (screenId == lastScreenId) {
                return
            }
            
            lastScreenId = screenId
            
            Log.d(TAG, "📤 Sending UI tree update...")
            Log.d(TAG, "   Screen: ${uiTreeJson.optString("screen_name")}")
            Log.d(TAG, "   App: ${uiTreeJson.optString("app_name")}")
            Log.d(TAG, "   Elements: ${uiTreeJson.optJSONArray("elements")?.length() ?: 0}")
            
            // POST to backend
            val url = URL("$backendUrl/device/$deviceId/ui-tree")
            val connection = url.openConnection() as HttpURLConnection
            
            try {
                connection.requestMethod = "POST"
                connection.setRequestProperty("Content-Type", "application/json")
                connection.connectTimeout = 10000
                connection.readTimeout = 10000
                connection.doOutput = true
                
                // Write UI tree
                connection.outputStream.use { os ->
                    os.write(uiTreeJson.toString().toByteArray(Charsets.UTF_8))
                }
                
                val responseCode = connection.responseCode
                
                if (responseCode == HttpURLConnection.HTTP_OK) {
                    Log.d(TAG, "✅ UI tree sent successfully")
                } else {
                    Log.e(TAG, "❌ Failed to send UI tree: HTTP $responseCode")
                }
            } finally {
                connection.disconnect()
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ Error sending UI tree: ${e.message}")
        }
    }
    
    /**
     * Trigger immediate UI tree update (called after actions)
     */
    fun sendImmediateUpdate() {
        CoroutineScope(Dispatchers.IO).launch {
            sendUITreeUpdate()
        }
    }
}
