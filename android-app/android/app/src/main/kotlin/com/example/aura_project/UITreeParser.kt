package com.example.aura_project

import android.view.accessibility.AccessibilityNodeInfo
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * UITreeParser
 *
 * Converts the raw Android AccessibilityNodeInfo tree to two representations:
 *
 *  1. Full JSON (parseAccessibilityTree)  — used by the backend for element
 *     resolution, screen-signature building, and ChromaDB storage.
 *
 *  2. Pruned semantic string (treeToSemanticString) — a compact ~2 KB string
 *     sent directly to the LLM inside the Tier 3 ReAct prompt.
 *     Contains ONLY interactive elements plus their normalised centre
 *     coordinates (@x%,y%), so the LLM can reason about spatial layout
 *     (top bar vs bottom FAB vs mid-screen list item) without raw pixels.
 *
 * Pruning rules (applied in treeToSemanticString):
 *   - Skip invisible/gone elements
 *   - Skip non-interactive containers with no text or content_description
 *   - Skip pure layout containers (FrameLayout, LinearLayout, etc.) that
 *     are neither clickable nor focusable
 *   - Keep disabled elements that have a content_description (e.g. Send
 *     button that becomes disabled but is still identifiable)
 *   - Normalise centre coordinate to percentage of screen (0-100%)
 *
 * Output line format:
 *   [id:N] TYPE "text"|[hint: ...]|"content_desc" @(x%,y%) [FLAGS]
 *
 * Example:
 *   [id:42] BUTTON "Send" @(91%,4%) [CLICKABLE]
 *   [id:17] TEXTFIELD [hint: To] @(50%,12%) [FOCUSABLE]
 *   [id:53] IMAGE "Navigate up" @(4%,4%) [CLICKABLE]
 */
class UITreeParser(
    private val deviceId: String = "default_device",
    private val screenWidth:  Int = 1080,
    private val screenHeight: Int = 2340,
) {
    companion object {
        private const val TAG = "UITreeParser"

        // Layout containers that are almost never useful to the LLM
        private val LAYOUT_CLASS_PREFIXES = setOf(
            "android.widget.FrameLayout",
            "android.widget.LinearLayout",
            "android.widget.RelativeLayout",
            "android.view.ViewGroup",
            "androidx.constraintlayout",
            "android.view.View",
        )
    }

    // ──────────────────────────────────────────────────────────────────────
    //  PUBLIC: full JSON tree (used by backend element resolver)
    // ──────────────────────────────────────────────────────────────────────

    fun parseAccessibilityTree(rootNode: AccessibilityNodeInfo?): JSONObject {
        if (rootNode == null) {
            Log.e(TAG, "Root node is null")
            return JSONObject()
        }

        val result = JSONObject()
        result.put("device_id",    deviceId)
        result.put("screen_width",  screenWidth)
        result.put("screen_height", screenHeight)
        result.put("timestamp",     System.currentTimeMillis() / 1000.0)

        val appPackage = rootNode.packageName?.toString() ?: "unknown"
        val appName    = getAppNameFromPackage(appPackage)
        result.put("app_package",  appPackage)
        result.put("app_name",     appName)
        result.put("screen_name",  getActivityName(rootNode) ?: appName)

        val elementsArray = JSONArray()
        val elementMap    = mutableMapOf<Int, JSONObject>()
        traverseTree(rootNode, elementsArray, elementMap, 0)
        buildRelationships(elementsArray, elementMap)

        result.put("elements",  elementsArray)
        result.put("screen_id", "screen_${System.currentTimeMillis()}")

        Log.d(TAG, "✅ Parsed ${elementsArray.length()} elements")
        return result
    }

    // ──────────────────────────────────────────────────────────────────────
    //  PUBLIC: pruned semantic string (sent to LLM in Tier 3 prompt)
    // ──────────────────────────────────────────────────────────────────────

    /**
     * Produces a compact string suitable for the LLM (~2 KB target).
     *
     * Each line:
     *   [id:N] TYPE "label" @(x%,y%) [FLAGS]
     *
     * The @(x%,y%) encodes the CENTRE of the element as a percentage of
     * screen width and height. This allows the LLM to infer spatial context:
     *
     *   @(4%,4%)   → top-left  (back/nav button)
     *   @(50%,5%)  → top-centre (title / search bar)
     *   @(90%,95%) → bottom-right FAB (compose, send)
     *   @(50%,50%) → centre (main content)
     *
     * Coordinates use integer percentages so they add minimal tokens.
     */
    fun treeToSemanticString(treeJson: JSONObject): String {
        val sb    = StringBuilder()
        val sw    = treeJson.optInt("screen_width",  screenWidth).coerceAtLeast(1)
        val sh    = treeJson.optInt("screen_height", screenHeight).coerceAtLeast(1)
        val appPkg = treeJson.optString("app_package", "")

        sb.appendLine("Screen: ${treeJson.optString("screen_name", treeJson.optString("app_name"))}")
        if (appPkg.isNotEmpty()) sb.appendLine("App: $appPkg")
        sb.appendLine()

        val elements = treeJson.optJSONArray("elements") ?: JSONArray()

        for (i in 0 until elements.length()) {
            val elem = elements.getJSONObject(i)

            // ── Pruning rules ──────────────────────────────────────────
            if (elem.optString("visibility") != "visible") continue

            val clickable  = elem.optBoolean("clickable")
            val focusable  = elem.optBoolean("focusable")
            val scrollable = elem.optBoolean("scrollable")
            val enabled    = elem.optBoolean("enabled", true)
            val text       = elem.optString("text",  "").trim()
            val desc       = elem.optString("content_description", "").trim()
            val hint       = elem.optString("hint_text", "").trim()
            val type       = elem.optString("type", "container")
            val className  = elem.optString("class_name", "")

            // Skip pure layout containers with no interactive properties
            // and no useful label (they're structural noise for the LLM)
            val isPureLayout = LAYOUT_CLASS_PREFIXES.any { className.startsWith(it) }
            if (isPureLayout && !clickable && !focusable && !scrollable
                && text.isEmpty() && desc.isEmpty()) {
                continue
            }

            // Skip non-interactive text/image that carries no label
            if (!clickable && !focusable && !scrollable
                && type in setOf("text", "image", "container")
                && text.isEmpty() && desc.isEmpty()) {
                continue
            }

            // Keep disabled elements only if they have a content_description
            // (e.g. Send button disabled until recipient is typed)
            if (!enabled && desc.isEmpty()) continue

            // ── Build label ────────────────────────────────────────────
            val label = when {
                text.isNotEmpty() -> "\"$text\""
                hint.isNotEmpty() -> "[hint: $hint]"
                desc.isNotEmpty() -> "\"$desc\""
                else              -> "(no text)"
            }

            // ── Normalised centre coordinate ───────────────────────────
            val coordStr = buildCoordString(elem, sw, sh)

            // ── Flags ──────────────────────────────────────────────────
            val flags = buildList {
                if (clickable)  add("CLICKABLE")
                if (focusable)  add("FOCUSABLE")
                if (scrollable) add("SCROLLABLE")
                if (!enabled)   add("DISABLED")
            }
            val flagStr = if (flags.isNotEmpty()) " [${flags.joinToString("|")}]" else ""

            val id = elem.getInt("element_id")
            sb.appendLine("[id:$id] ${type.uppercase()} $label$coordStr$flagStr")
        }

        val result = sb.toString().trimEnd()
        Log.d(TAG, "🪄 Pruned tree: ${result.length} chars")
        return result
    }

    // ──────────────────────────────────────────────────────────────────────
    //  PRIVATE helpers
    // ──────────────────────────────────────────────────────────────────────

    /**
     * Build " @(x%,y%)" coordinate string from element bounds.
     * Returns empty string if bounds are absent or zero-size.
     */
    private fun buildCoordString(elem: JSONObject, sw: Int, sh: Int): String {
        val bounds = elem.optJSONObject("bounds") ?: return ""
        val left   = bounds.optInt("left",   -1)
        val top    = bounds.optInt("top",    -1)
        val right  = bounds.optInt("right",  -1)
        val bottom = bounds.optInt("bottom", -1)

        if (left < 0 || top < 0 || right <= left || bottom <= top) return ""

        val cx = (left + right)  / 2
        val cy = (top  + bottom) / 2
        val xPct = (cx * 100 / sw).coerceIn(0, 100)
        val yPct = (cy * 100 / sh).coerceIn(0, 100)
        return " @($xPct%,$yPct%)"
    }

    private fun traverseTree(
        node:         AccessibilityNodeInfo,
        elementsArray: JSONArray,
        elementMap:   MutableMap<Int, JSONObject>,
        counter:      Int,
    ): Int {
        if (!node.isVisibleToUser) return counter

        val bounds = android.graphics.Rect()
        node.getBoundsInScreen(bounds)

        val elem = JSONObject()
        val id   = counter
        elem.put("element_id", id)
        elem.put("type",        inferType(node.className?.toString() ?: "", node))
        elem.put("class_name",  node.className?.toString() ?: "")
        elem.put("resource_id", node.viewIdResourceName ?: "")

        node.text?.toString().takeIf { !it.isNullOrBlank() }
            ?.let { elem.put("text", it) }
        node.contentDescription?.toString().takeIf { !it.isNullOrBlank() }
            ?.let { elem.put("content_description", it) }
        node.hintText?.toString().takeIf { !it.isNullOrBlank() }
            ?.let { elem.put("hint_text", it) }

        elem.put("clickable",  node.isClickable)
        elem.put("focusable",  node.isFocusable)
        elem.put("scrollable", node.isScrollable)
        elem.put("enabled",    node.isEnabled)
        elem.put("visibility", if (node.isVisibleToUser) "visible" else "gone")

        if (bounds.width() > 0 && bounds.height() > 0) {
            elem.put("bounds", JSONObject().apply {
                put("left",   bounds.left)
                put("top",    bounds.top)
                put("right",  bounds.right)
                put("bottom", bounds.bottom)
            })
        }

        elem.put("parent_id", -1)
        elem.put("child_ids", JSONArray())

        elementsArray.put(elem)
        elementMap[id] = elem

        var c = counter + 1
        val childStart = c
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            c = traverseTree(child, elementsArray, elementMap, c)
            child.recycle()
        }

        // Wire parent IDs for direct children
        for (j in childStart until c) {
            if (j < elementsArray.length()) {
                elementsArray.getJSONObject(j).put("parent_id", id)
            }
        }

        return c
    }

    private fun buildRelationships(
        elementsArray: JSONArray,
        elementMap:    MutableMap<Int, JSONObject>,
    ) {
        for (i in 0 until elementsArray.length()) {
            val elem     = elementsArray.getJSONObject(i)
            val parentId = elem.getInt("parent_id")
            if (parentId >= 0) {
                elementMap[parentId]
                    ?.getJSONArray("child_ids")
                    ?.put(elem.getInt("element_id"))
            }
        }
    }

    private fun inferType(className: String, node: AccessibilityNodeInfo): String = when {
        className.contains("Button")       -> "button"
        className.contains("EditText")     -> "textfield"
        className.contains("CheckBox")     -> "checkbox"
        className.contains("RadioButton")  -> "radio"
        className.contains("Switch")       -> "switch"
        className.contains("ImageView")    -> "image"
        className.contains("ProgressBar")  -> "progressbar"
        className.contains("SeekBar")      -> "seekbar"
        className.contains("Spinner")      -> "spinner"
        className.contains("RecyclerView") -> "list"
        className.contains("ListView")     -> "list"
        className.contains("ScrollView")   -> "scrollview"
        className.contains("WebView")      -> "webview"
        className.contains("TextView")     -> "text"
        node.isClickable                   -> "clickable_element"
        node.isScrollable                  -> "scrollable_container"
        else                               -> "container"
    }

    private fun getAppNameFromPackage(pkg: String): String =
        pkg.split(".").lastOrNull()?.replaceFirstChar { it.uppercase() } ?: "Unknown"

    private fun getActivityName(node: AccessibilityNodeInfo): String? = null
}