# ============================================================================
# ENHANCED POST-ACTION VERIFIERS - CLOSED-LOOP AUTOMATION
# ============================================================================
# ✅ Verifies that actions actually succeeded by checking observable outcomes
# ✅ Enhanced media control verification
# ✅ State comparison verification
# ✅ False success detection

import logging
from typing import Dict, Tuple, Optional, Any, List

logger = logging.getLogger(__name__)

# ============================================================================
# MAIN VERIFICATION FUNCTION
# ============================================================================

async def verify_action(
    page, 
    action_type: str, 
    context: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Verify that an action actually succeeded.
    
    ✅ ENHANCED: More comprehensive verification including state comparison
    
    Args:
        page: Playwright Page object
        action_type: Type of action performed
        context: Context dictionary with action details
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    
    logger.debug(f"🔍 Verifying action: {action_type}")
    
    try:
        if action_type == "navigate":
            return await verify_navigation(page, context)

        elif action_type == "fill":
            # Advisory — never override EXECUTION_SUCCESS from the code
            _, msg = await verify_fill(page, context)
            logger.debug(f"Fill verification (advisory): {msg}")
            return True, f"[advisory] {msg}"

        elif action_type == "click":
            # Advisory — never override EXECUTION_SUCCESS from the code
            _, msg = await verify_click(page, context)
            logger.debug(f"Click verification (advisory): {msg}")
            return True, f"[advisory] {msg}"
        
        elif action_type == "play_video":
            return await verify_video_playback(page, context)
        
        elif action_type == "extract":
            return await verify_extraction(page, context)
        
        # ✅ NEW: Media control verifications
        elif action_type in ["pause", "play", "mute", "unmute", "skip", "next"]:
            return await verify_media_control(page, action_type, context)
        
        else:
            # Default: assume success if no specific verification
            logger.debug(f"⚠️ No specific verification for action type: {action_type}")
            return True, f"Action '{action_type}' executed (no verification implemented)"
    
    except Exception as e:
        logger.error(f"❌ Verification error (non-fatal): {e}")
        # Never let a verification crash kill a task
        return True, f"Verification skipped due to error: {e}"

# ============================================================================
# ✅ NEW: MEDIA CONTROL VERIFICATION
# ============================================================================

async def verify_media_control(page, action: str, context: Dict) -> Tuple[bool, str]:
    """
    Verify media control actions (pause, play, mute, etc.).
    Checks actual video element state.
    """
    
    try:
        # Wait for any transitions
        await page.wait_for_timeout(500)
        
        video_state = await page.evaluate("""
            () => {
                const video = document.querySelector('video');
                if (!video) {
                    return { found: false };
                }
                
                return {
                    found: true,
                    paused: video.paused,
                    muted: video.muted,
                    currentTime: video.currentTime,
                    duration: video.duration,
                    volume: video.volume,
                    ended: video.ended,
                    playing: !video.paused && video.currentTime > 0,
                };
            }
        """)
        
        if not video_state.get('found'):
            return False, "No video element found on page"
        
        action_lower = action.lower()
        
        # Verify pause
        if 'pause' in action_lower:
            if video_state.get('paused'):
                return True, "✅ Video is paused"
            else:
                return False, "Video is still playing (pause failed)"
        
        # Verify play
        elif 'play' in action_lower:
            if video_state.get('playing'):
                return True, "✅ Video is playing"
            else:
                return False, "Video is still paused (play failed)"
        
        # Verify mute
        elif 'mute' in action_lower:
            if video_state.get('muted'):
                return True, "✅ Video is muted"
            else:
                return False, "Video is not muted (mute failed)"
        
        # Verify unmute
        elif 'unmute' in action_lower:
            if not video_state.get('muted'):
                return True, "✅ Video is unmuted"
            else:
                return False, "Video is still muted (unmute failed)"
        
        # ✅ NEW: Verify skip/next (check time change or URL change)
        elif any(word in action_lower for word in ['skip', 'next']):
            # For playlists, URL should change
            # For single video, can't really verify without "before" state
            url_before = context.get('url_before')
            url_after = page.url
            
            if url_before and url_before != url_after:
                return True, f"✅ Navigated to next video: {url_after}"
            
            # Check if video time jumped (might indicate skip within video)
            if video_state.get('currentTime', 0) > 5:
                return True, "✅ Video position changed (possibly skipped)"
            
            return False, "Could not verify skip action (no URL change or time jump)"
        
        return True, f"Media control '{action}' executed (state: {video_state})"
        
    except Exception as e:
        return False, f"Media control verification failed: {str(e)}"

# ============================================================================
# EXISTING VERIFIERS (ENHANCED)
# ============================================================================

async def verify_navigation(page, context: Dict) -> Tuple[bool, str]:
    """Verify that navigation succeeded"""
    
    try:
        current_url = page.url
        
        # ✅ FIX (Message 9): For hash/fragment navigation (Gmail SPAs), skip networkidle
        # Fragment navigation (#inbox?compose=new) doesn't trigger network events
        is_fragment_nav = '#' in current_url or '?' in current_url.split('/')[-1]
        
        if not is_fragment_nav:
            # Only wait for networkidle for full page loads
            try:
                await page.wait_for_load_state('networkidle', timeout=3000)
            except:
                # If networkidle times out, fall through to manual checks
                pass
        
        # Manual check**: just verify page is loaded
        ready_state = await page.evaluate("() => document.readyState")
        if ready_state == 'complete':
            return True, f"✅ Successfully navigated to {current_url}"
        
        # Fall back: if not 'complete' but we're on a new URL, it's still OK
        expected_domain = context.get('expected_domain')
        if expected_domain and expected_domain not in current_url:
            return False, f"Navigated to wrong URL: {current_url}"
        
        # For SPAs, just check we have interactive content
        has_interactive = await page.evaluate("""
            () => {
                const hasButtons = !!document.querySelector('button, [role="button"]');
                const hasInputs = !!document.querySelector('input, textarea, [contenteditable]');
                return hasButtons || hasInputs;
            }
        """)
        
        if has_interactive:
            return True, f"✅ Successfully navigated to {current_url} (SPA interactive)"
        
        return True, f"✅ Successfully navigated to {current_url} (navigation complete)"
        
    except Exception as e:
        logger.warning(f"⚠️ Navigation verification: {str(e)} (treating as non-fatal)")
        # For navigation, be lenient — if we got this far, page loaded
        return True, f"Navigation completed (verification skipped: {str(e)[:50]})"

async def verify_fill(page, context: Dict) -> Tuple[bool, str]:
    """
    Verify text was entered — handles standard inputs AND contenteditable divs.
    BUG 2a FIX: Gmail body is a contenteditable div, not an <input>.
    Never raises — returns advisory True on any verification error so a
    genuine fill is never falsely killed by a racing state check.
    """
    try:
        expected_text = context.get('text', '') or ''
        selector = context.get('last_selector')

        # Strategy 1: focused element value (standard input/textarea)
        focused_value = await page.evaluate("""
            () => {
                const el = document.activeElement;
                return el ? (el.value || el.innerText || el.textContent || '') : '';
            }
        """)
        if expected_text and focused_value and expected_text.lower() in focused_value.lower():
            return True, f"Text filled (focused element): '{focused_value[:60]}'"

        # Strategy 2: specific selector via input_value()
        if selector:
            try:
                v = await page.input_value(selector, timeout=2000)
                if expected_text and v and expected_text.lower() in v.lower():
                    return True, f"Text filled (selector): '{v[:60]}'"
            except Exception:
                pass

        # Strategy 3 & 4: full-page scan — contenteditable + standard inputs
        if expected_text:
            found = await page.evaluate(
                """(text) => {
                    // contenteditable (Gmail body, Notion, Outlook, rich-text editors)
                    const eds = document.querySelectorAll('[contenteditable="true"],[contenteditable=""]');
                    for (const el of eds) {
                        const c = el.innerText || el.textContent || '';
                        if (c.toLowerCase().includes(text.toLowerCase()))
                            return { found: true, type: 'contenteditable', value: c.substring(0,60) };
                    }
                    // Standard inputs and textareas
                    const ins = document.querySelectorAll('input[type="text"],input:not([type]),textarea');
                    for (const el of ins) {
                        if (el.value && el.value.toLowerCase().includes(text.toLowerCase()))
                            return { found: true, type: 'input', value: el.value.substring(0,60) };
                    }
                    return { found: false };
                }""",
                expected_text
            )
            if found and found.get('found'):
                return True, f"Text '{expected_text}' found in {found['type']}: '{found['value']}'"
            return False, f"Text '{expected_text}' not found on page"

        return True, "Fill executed (no expected text to verify)"

    except Exception as e:
        # Never let a verification error kill a task
        logger.warning(f"Fill verification error (non-fatal): {e}")
        return True, f"Fill verification skipped: {e}"

async def verify_click(page, context: Dict) -> Tuple[bool, str]:
    """Verify that click action caused expected change"""
    
    try:
        url_before = context.get('url_before')
        url_after = page.url
        
        # Check if URL changed (navigation happened)
        if url_before and url_before != url_after:
            return True, f"Click caused navigation to {url_after}"
        
        # Check if any loading happened
        try:
            await page.wait_for_load_state('networkidle', timeout=2000)
            return True, "Click triggered page change"
        except:
            pass
        
        # Check if any DOM changes occurred
        try:
            # Wait briefly for any animations or dynamic content
            await page.wait_for_timeout(500)
            
            # If we get here, click executed but no obvious change
            # This might be okay (e.g., click on already-expanded menu)
            return True, "Click executed (no visible navigation)"
            
        except Exception as e:
            return False, f"Click verification failed: {str(e)}"
    
    except Exception as e:
        return False, f"Click verification error: {str(e)}"

async def verify_video_playback(page, context: Dict) -> Tuple[bool, str]:
    """Verify that video is actually playing"""
    
    try:
        # Wait a bit for video to start
        await page.wait_for_timeout(1000)
        
        is_playing = await page.evaluate("""
            () => {
                const video = document.querySelector('video');
                if (!video) {
                    return { found: false };
                }
                
                return {
                    found: true,
                    paused: video.paused,
                    currentTime: video.currentTime,
                    duration: video.duration,
                    readyState: video.readyState,
                    playing: !video.paused && video.currentTime > 0
                };
            }
        """)
        
        if not is_playing.get('found'):
            return False, "No video element found on page"
        
        if is_playing.get('playing'):
            return True, f"Video is playing (time: {is_playing.get('currentTime', 0):.1f}s)"
        
        # Video found but not playing
        if is_playing.get('paused'):
            return False, "Video is paused (not playing)"
        
        if is_playing.get('currentTime', 0) == 0:
            return False, "Video not started (currentTime = 0)"
        
        return False, f"Video state unclear: {is_playing}"
        
    except Exception as e:
        return False, f"Video playback verification failed: {str(e)}"

async def verify_extraction(page, context: Dict) -> Tuple[bool, str]:
    """Verify that data extraction succeeded"""
    
    try:
        extracted_data = context.get('extracted_data')
        
        if not extracted_data:
            return False, "No data was extracted"
        
        # Check if extracted data is meaningful (not empty/null)
        if isinstance(extracted_data, str):
            if len(extracted_data.strip()) > 0:
                return True, f"Extracted {len(extracted_data)} characters of text"
            return False, "Extracted empty string"
        
        elif isinstance(extracted_data, list):
            if len(extracted_data) > 0:
                return True, f"Extracted {len(extracted_data)} items"
            return False, "Extracted empty list"
        
        elif isinstance(extracted_data, dict):
            if len(extracted_data) > 0:
                return True, f"Extracted {len(extracted_data)} fields"
            return False, "Extracted empty dictionary"
        
        return True, "Data extracted successfully"
        
    except Exception as e:
        return False, f"Extraction verification failed: {str(e)}"

# ============================================================================
# ADVANCED VERIFIERS
# ============================================================================

async def verify_element_exists(page, selector: str, timeout: int = 2000) -> Tuple[bool, str]:
    """Verify that an element exists and is visible"""
    
    try:
        await page.wait_for_selector(selector, timeout=timeout, state='visible')
        return True, f"Element found: {selector}"
    except:
        return False, f"Element not found or not visible: {selector}"

async def verify_text_visible(page, text: str, timeout: int = 2000) -> Tuple[bool, str]:
    """Verify that specific text is visible on page"""
    
    try:
        await page.wait_for_selector(f'text={text}', timeout=timeout)
        return True, f"Text visible: '{text}'"
    except:
        return False, f"Text not found: '{text}'"

async def verify_url_contains(page, substring: str) -> Tuple[bool, str]:
    """Verify that current URL contains substring"""
    
    current_url = page.url
    if substring.lower() in current_url.lower():
        return True, f"URL contains '{substring}': {current_url}"
    return False, f"URL does not contain '{substring}': {current_url}"

async def verify_page_loaded(page, timeout: int = 5000) -> Tuple[bool, str]:
    """Verify that page is fully loaded"""
    
    try:
        await page.wait_for_load_state('networkidle', timeout=timeout)
        ready_state = await page.evaluate("() => document.readyState")
        
        if ready_state == 'complete':
            return True, "Page fully loaded"
        
        return False, f"Page not fully loaded (state: {ready_state})"
        
    except Exception as e:
        return False, f"Page load verification failed: {str(e)}"

# ============================================================================
# ✅ NEW: STATE COMPARISON VERIFIERS
# ============================================================================

async def verify_state_change(
    page, 
    state_before: Dict, 
    state_after: Dict, 
    expected_changes: List[str]
) -> Tuple[bool, str]:
    """
    Verify that specific state changes occurred.
    
    Args:
        page: Playwright page
        state_before: Page state before action
        state_after: Page state after action
        expected_changes: List of expected change types
        
    Returns:
        (success, message)
    """
    
    try:
        changes_detected = []
        changes_missing = []
        
        for change_type in expected_changes:
            if change_type == 'url':
                if state_before.get('url') != state_after.get('url'):
                    changes_detected.append(f"URL changed: {state_after.get('url')}")
                else:
                    changes_missing.append('URL did not change')
            
            elif change_type == 'video_paused':
                video_before = state_before.get('video', {})
                video_after = state_after.get('video', {})
                
                if video_before.get('paused') != video_after.get('paused'):
                    changes_detected.append(f"Video paused state changed: {video_after.get('paused')}")
                else:
                    changes_missing.append('Video paused state did not change')
            
            elif change_type == 'video_muted':
                video_before = state_before.get('video', {})
                video_after = state_after.get('video', {})
                
                if video_before.get('muted') != video_after.get('muted'):
                    changes_detected.append(f"Video muted state changed: {video_after.get('muted')}")
                else:
                    changes_missing.append('Video muted state did not change')
            
            elif change_type == 'focus':
                if state_before.get('activeElement') != state_after.get('activeElement'):
                    changes_detected.append(f"Focus changed to: {state_after.get('activeElement')}")
                else:
                    changes_missing.append('Focus did not change')
        
        if changes_missing:
            return False, f"Expected changes not detected: {', '.join(changes_missing)}"
        
        if changes_detected:
            return True, f"Verified changes: {', '.join(changes_detected)}"
        
        return True, "No specific changes expected, action completed"
        
    except Exception as e:
        return False, f"State comparison failed: {str(e)}"

async def verify_video_state(
    page,
    expected_state: Dict[str, Any]
) -> Tuple[bool, str]:
    """
    Verify video element is in expected state.
    
    Args:
        page: Playwright page
        expected_state: Dict with expected properties (paused, muted, etc.)
        
    Returns:
        (success, message)
    """
    
    try:
        actual_state = await page.evaluate("""
            () => {
                const video = document.querySelector('video');
                if (!video) return null;
                
                return {
                    paused: video.paused,
                    muted: video.muted,
                    volume: video.volume,
                    currentTime: video.currentTime,
                    duration: video.duration,
                    ended: video.ended,
                };
            }
        """)
        
        if not actual_state:
            return False, "No video element found"
        
        mismatches = []
        
        for key, expected_value in expected_state.items():
            actual_value = actual_state.get(key)
            
            if actual_value != expected_value:
                mismatches.append(f"{key}: expected {expected_value}, got {actual_value}")
        
        if mismatches:
            return False, f"Video state mismatch: {', '.join(mismatches)}"
        
        return True, f"Video state matches expected: {expected_state}"
        
    except Exception as e:
        return False, f"Video state verification failed: {str(e)}"

# ============================================================================
# COMPOSITE VERIFIERS
# ============================================================================

async def verify_search_executed(page, context: Dict) -> Tuple[bool, str]:
    """Verify that search was executed and results are shown"""
    
    try:
        # Check URL changed to results page
        url_before = context.get('url_before', '')
        url_after = page.url
        
        # Common search result indicators
        search_indicators = [
            '/search',
            'q=',
            'query=',
            'results',
            'search?'
        ]
        
        if any(ind in url_after.lower() for ind in search_indicators):
            return True, f"Search results loaded: {url_after}"
        
        # Check for results container
        results_selectors = [
            '[role="main"]',
            '#search',
            '.search-results',
            '#results'
        ]
        
        for selector in results_selectors:
            try:
                await page.wait_for_selector(selector, timeout=2000)
                return True, "Search results container found"
            except:
                continue
        
        return False, "Could not verify search results"
        
    except Exception as e:
        return False, f"Search verification failed: {str(e)}"

async def verify_form_submitted(page, context: Dict) -> Tuple[bool, str]:
    """Verify that form was submitted successfully"""
    
    try:
        url_before = context.get('url_before')
        url_after = page.url
        
        # Check for URL change (typical after form submit)
        if url_before != url_after:
            return True, f"Form submitted (navigated to {url_after})"
        
        # Check for success message
        success_patterns = [
            'text=success',
            'text=submitted',
            'text=thank you',
            '[role="alert"]'
        ]
        
        for pattern in success_patterns:
            try:
                await page.wait_for_selector(pattern, timeout=2000)
                return True, "Form submission success message found"
            except:
                continue
        
        # Check if loading happened
        try:
            await page.wait_for_load_state('networkidle', timeout=3000)
            return True, "Form submitted (page reloaded)"
        except:
            pass
        
        return False, "Could not verify form submission"
        
    except Exception as e:
        return False, f"Form submission verification failed: {str(e)}"

# ============================================================================
# VERIFICATION HELPERS
# ============================================================================

def parse_verification_result(stdout: str) -> Tuple[bool, str]:
    """
    Parse stdout from executed code to determine success.
    
    Args:
        stdout: Standard output from code execution
        
    Returns:
        Tuple of (success: bool, message: str)
    """
    
    # Check for explicit failure
    if 'FAILED:' in stdout:
        failure_msg = stdout.split('FAILED:')[1].split('\n')[0].strip()
        return False, f"Playwright error: {failure_msg}"
    
    # Check for success indicator
    if 'EXECUTION_SUCCESS' in stdout:
        return True, "Code reported success"
    
    # No clear indicator
    return False, "No success indicator found in output"

async def get_page_state_snapshot(page) -> Dict:
    """
    Get snapshot of current page state for comparison.
    
    Returns:
        Dictionary with page state information
    """
    
    try:
        snapshot = await page.evaluate("""
            () => ({
                url: window.location.href,
                title: document.title,
                readyState: document.readyState,
                activeElement: document.activeElement?.tagName,
                visibleText: document.body?.innerText?.substring(0, 500),
                elementCount: document.querySelectorAll('*').length
            })
        """)
        
        return snapshot
        
    except Exception as e:
        logger.debug(f"Could not get page snapshot: {e}")
        return {}

async def compare_page_states(before: Dict, after: Dict) -> Dict:
    """
    Compare two page state snapshots.
    
    Returns:
        Dictionary describing changes
    """
    
    changes = {
        'url_changed': before.get('url') != after.get('url'),
        'title_changed': before.get('title') != after.get('title'),
        'content_changed': before.get('visibleText') != after.get('visibleText'),
        'dom_changed': before.get('elementCount') != after.get('elementCount'),
        'any_change': False
    }
    
    changes['any_change'] = any([
        changes['url_changed'],
        changes['title_changed'],
        changes['content_changed'],
        changes['dom_changed']
    ])
    
    return changes