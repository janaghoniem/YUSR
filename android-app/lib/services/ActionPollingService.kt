package com.example.aura_project

import kotlinx.coroutines.*
import org.json.JSONObject
import org.json.JSONArray
import android.util.Log
import java.net.HttpURLConnection
import java.net.URL

class ActionPollingService(
    private val deviceId: String,
    private val backendUrl: String,
    private val actionExecutor: ActionExecutor
) {
    companion object {
        private const val TAG = "ActionPollingService"
        private const val POLL_INTERVAL_MS = 1000L  // Poll every 1 second
    }
    
    private var pollingJob: Job? = null
    private var isRunning = false
    
    /**
     * Start polling for pending actions from backend
     */
    fun startPolling() {
        if (isRunning) {
            Log.w(TAG, "⚠️ Polling already running")
            return
        }
        
        Log.d(TAG, "🔄 Starting action polling service...")
        isRunning = true
        
        pollingJob = CoroutineScope(Dispatchers.IO).launch {
            while (isRunning) {
                try {
                    pollForActions()
                    delay(POLL_INTERVAL_MS)
                } catch (e: Exception) {
                    Log.e(TAG, "❌ Error in polling loop: ${e.message}", e)
                    delay(5000)  // Wait longer on error
                }
            }
        }
        
        Log.d(TAG, "✅ Action polling started")
    }
    
    /**
     * Stop polling
     */
    fun stopPolling() {
        Log.d(TAG, "🛑 Stopping action polling...")
        isRunning = false
        pollingJob?.cancel()
        pollingJob = null
        Log.d(TAG, "✅ Action polling stopped")
    }
    
    /**
     * Poll backend for pending actions and execute them
     */
    private suspend fun pollForActions() {
        try {
            // GET /device/{device_id}/pending-actions
            val url = URL("$backendUrl/device/$deviceId/pending-actions")
            val connection = url.openConnection() as HttpURLConnection
            
            try {
                connection.requestMethod = "GET"
                connection.connectTimeout = 5000
                connection.readTimeout = 5000
                
                val responseCode = connection.responseCode
                
                if (responseCode == HttpURLConnection.HTTP_OK) {
                    val response = connection.inputStream.bufferedReader().use { it.readText() }
                    val responseJson = JSONObject(response)
                    val actionsArray = responseJson.optJSONArray("actions")
                    
                    if (actionsArray != null && actionsArray.length() > 0) {
                        Log.d(TAG, "📥 Received ${actionsArray.length()} pending actions")
                        
                        // Execute each action
                        for (i in 0 until actionsArray.length()) {
                            val actionJson = actionsArray.getJSONObject(i)
                            executeAction(actionJson)
                        }
                    }
                }
            } finally {
                connection.disconnect()
            }
        } catch (e: Exception) {
            // Silent fail - don't spam logs during normal operation
            // Only log if it's not a connection timeout
            if (e !is java.net.SocketTimeoutException) {
                Log.e(TAG, "Error polling: ${e.message}")
            }
        }
    }
    
    /**
     * Execute a single action and send result back
     */
    private suspend fun executeAction(actionJson: JSONObject) {
        val actionId = actionJson.optString("action_id", "unknown")
        val actionType = actionJson.optString("action_type", "unknown")
        
        Log.d(TAG, "⚡ Executing action: $actionType (ID: $actionId)")
        
        try {
            // Execute action using ActionExecutor
            val resultJson = actionExecutor.executeAction(actionJson)
            
            Log.d(TAG, "✅ Action executed, sending result back...")
            
            // Send result back to backend
            sendActionResult(resultJson)
            
        } catch (e: Exception) {
            Log.e(TAG, "❌ Error executing action: ${e.message}", e)
            
            // Send error result
            val errorResult = JSONObject().apply {
                put("action_id", actionId)
                put("success", false)
                put("error", e.message)
                put("execution_time_ms", 0)
            }
            
            sendActionResult(errorResult)
        }
    }
    
    /**
     * Send action execution result back to backend
     */
    private suspend fun sendActionResult(resultJson: JSONObject) {
        try {
            val url = URL("$backendUrl/device/$deviceId/action-result")
            val connection = url.openConnection() as HttpURLConnection
            
            try {
                connection.requestMethod = "POST"
                connection.setRequestProperty("Content-Type", "application/json")
                connection.connectTimeout = 10000
                connection.readTimeout = 10000
                connection.doOutput = true
                
                // Write result
                connection.outputStream.use { os ->
                    os.write(resultJson.toString().toByteArray(Charsets.UTF_8))
                }
                
                val responseCode = connection.responseCode
                
                if (responseCode == HttpURLConnection.HTTP_OK) {
                    Log.d(TAG, "✅ Action result sent successfully")
                } else {
                    Log.e(TAG, "❌ Failed to send result: HTTP $responseCode")
                }
            } finally {
                connection.disconnect()
            }
        } catch (e: Exception) {
            Log.e(TAG, "❌ Error sending action result: ${e.message}", e)
        }
    }
}
