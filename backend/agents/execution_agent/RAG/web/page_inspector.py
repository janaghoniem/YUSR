# ============================================================================
# PAGE INSPECTOR - ENHANCED DOM-AWARE CONTEXT FOR RAG
# ============================================================================
# ✅ Fixed fallback when accessibility API fails
# ✅ Better error handling for page semantics extraction
# ✅ Enhanced element detection with multiple strategies

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# ENHANCED FALLBACK PAGE SEMANTICS EXTRACTOR
# ============================================================================

async def get_page_semantics_fallback(page) -> str:
    """
    Enhanced fallback method to extract page elements when accessibility API unavailable.
    Uses multiple strategies to find interactive elements.
    ✅ ENHANCED: Now detects Gmail compose modal elements (recipient field, send button)
    """
    try:
        logger.info("🔄 Using enhanced fallback method for page semantics")
        
        # Extract interactive elements using comprehensive evaluate
        elements_info = await page.evaluate("""
            () => {
        // Strategy 0: Gmail-specific elements (compose modal, recipient field, send button)
                const gmailSendButtons = Array.from(document.querySelectorAll(
                    'div[role="button"][data-tooltip*="Send"], ' +  // Gmail send button with tooltip
                    'div[aria-label*="Send"], ' +  // Send button by aria-label
                    'button[data-tooltip*="Send"], ' +  // Button version
                    '[jsname] button' +  // Gmail uses data-jsname attributes
                    'div[data-tooltip*="send" i]'  // Case-insensitive tooltip
                ));
                
                const gmailRecipientFields = Array.from(document.querySelectorAll(
                    'div[contenteditable="true"][aria-label*="To"], ' +  // Recipient field
                    'div[contenteditable="true"][role="textbox"][aria-label*="recipient" i], ' +  // Recipient as textbox
                    'input[aria-label*="To"], ' +  // Input field for recipients
                    'input[placeholder*="To"], ' +  // Input with To placeholder
                    'div[class*="recipient"], ' +  // div with recipient class
                    'input[type="text"][aria-label*="recipient" i]'  // Text input for recipient
                ));
                
        // Strategy 1: Standard interactive elements
                const buttons = Array.from(document.querySelectorAll('button, [role="button"], [type="button"], [type="submit"]')).concat(gmailSendButtons);
                const links = Array.from(document.querySelectorAll('a[href]'));
                const inputs = Array.from(document.querySelectorAll('input, textarea, select')).concat(gmailRecipientFields);
                
                // Strategy 2: Media controls (video, audio)
                const videoElements = Array.from(document.querySelectorAll('video'));
                const audioElements = Array.from(document.querySelectorAll('audio'));
                
                // Strategy 3: Generic media items (works across YouTube, Netflix, e-commerce, education platforms, etc.)
                const mediaItems = Array.from(document.querySelectorAll(
                    'a[href*="watch"], a[href*="video"], a[href*="play"], ' +  // Generic video links
                    'a[href*="product"], a[href*="item"], a[href*="course"], ' +  // E-commerce, education
                    '[data-video-id], [data-media-id], [data-item-id], ' +  // Media/item identifiers
                    '[class*="thumbnail"], [class*="video-tile"], [class*="product-card"], [class*="item-card"], ' +  // Common container classes
                    '[class*="media-item"], [class*="content-item"], [class*="video-item"], ' +
                    'article[data-content-type], article[data-media], ' +  // Article-based media
                    '.video, .media, .product, .thumbnail, .item, .card' +  // Generic classes
                    '[role="link"][data-content], [role="button"][data-video], [role="button"][data-content]'  // ARIA roles with data
                )).slice(0, 50);
                
                // Strategy 4: Platform-specific UI (YouTube ads, etc.)
                const youtubeAds = Array.from(document.querySelectorAll(
                    'button[aria-label*="Skip"], ' +  // YouTube skip ad buttons
                    '.ytp-ad-skip-button, ' +  // YouTube skip button class
                    'button[title*="Skip"]'  // Skip button with title attribute
                ));
                
                // Strategy 5: Contenteditable elements (Gmail recipient chips, rich text editors, etc.)
                const editables = Array.from(document.querySelectorAll('div[contenteditable="true"]'));
                
                // Strategy 6: Common UI patterns
                const clickableElements = Array.from(document.querySelectorAll('[onclick], [data-action]'));
                
                return {
                    buttons: buttons.slice(0, 15).map(el => {
                        const s = getComputedStyle(el);
                        return {
                            text: el.textContent?.trim() || el.ariaLabel || el.title || el.getAttribute('data-tooltip') || 'Unnamed button',
                            disabled: el.disabled || el.hasAttribute('disabled'),
                            id: el.id || '',
                            classes: el.className || '',
                            // ✅ FIX 2: Visual attributes for button finding
                            title: el.getAttribute('title') || '',
                            dataTestId: el.getAttribute('data-test-id') || el.getAttribute('data-qa') || '',
                            dataAttributes: Array.from(el.attributes).filter(a => a.name.startsWith('data-')).slice(0, 3).map(a => `${a.name}=${a.value}`),
                            color: s.color || '',
                            backgroundColor: s.backgroundColor || '',
                            role: el.getAttribute('role') || '',
                        };
                    }),
                    links: links.slice(0, 20).map(el => {
                        const s = getComputedStyle(el);
                        return {
                            text: el.textContent?.trim() || el.ariaLabel || el.title || 'Unnamed link',
                            href: el.href,
                            id: el.id || '',
                            // ✅ FIX 2: Visual attributes for links
                            title: el.getAttribute('title') || '',
                            color: s.color || '',
                            backgroundColor: s.backgroundColor || '',
                        };
                    }),
                    inputs: inputs.slice(0, 20).map(el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        const visible = r.width > 0 && r.height > 0
                            && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                        
                        // ✅ FIX 2: Add visual attributes for all inputs
                        const baseAttrs = {
                            title: el.getAttribute('title') || '',
                            dataTestId: el.getAttribute('data-test-id') || el.getAttribute('data-qa') || '',
                            color: s.color || '',
                            backgroundColor: s.backgroundColor || '',
                            borderColor: s.borderColor || '',
                        };
                        
                        // Special handling for contenteditable elements (Gmail recipients)
                        if (el.contentEditable === 'true' || el.getAttribute('contenteditable') === 'true') {
                            return {
                                type: 'contenteditable',
                                placeholder: el.getAttribute('placeholder') || el.getAttribute('aria-label') || '',
                                value: el.innerText || el.textContent || '',
                                name: el.getAttribute('aria-label') || el.className || 'contenteditable',
                                disabled: false,
                                ariaLabel: el.getAttribute('aria-label') || '',
                                visible: visible,
                                isContentEditable: true,
                                ...baseAttrs,
                            };
                        }
                        
                        return {
                            type: el.type || el.tagName.toLowerCase(),
                            placeholder: el.placeholder || '',
                            value: el.value || '',
                            name: el.name || el.id || 'unnamed',
                            disabled: el.disabled || el.hasAttribute('disabled'),
                            ariaLabel: el.getAttribute('aria-label') || '',
                            visible: visible,
                            ...baseAttrs,
                        };
                    }),
                    videos: videoElements.map(el => ({
                        src: el.src || el.currentSrc || '',
                        paused: el.paused,
                        muted: el.muted,
                        duration: el.duration,
                        currentTime: el.currentTime,
                    })),
                    mediaItems: mediaItems.map(el => ({
                        title: el.textContent?.trim() || el.getAttribute('title') || el.getAttribute('aria-label') || el.getAttribute('alt') || 'Item',
                        href: el.href || el.getAttribute('href') || '',
                        itemId: el.getAttribute('data-video-id') || el.getAttribute('data-media-id') || el.getAttribute('data-item-id') || el.getAttribute('data-id') || '',
                        dataAttributes: Array.from(el.attributes).filter(a => a.name.startsWith('data-')).slice(0, 5).map(a => `${a.name}=${a.value}`),
                    })).filter((v, idx, arr) => arr.findIndex(x => x.href === v.href && x.title === v.title) === idx),  // Dedupe
                    youtubeAds: youtubeAds.map(el => ({
                        text: el.textContent?.trim() || el.getAttribute('aria-label') || el.getAttribute('title') || 'Skip Ad Button',
                        ariaLabel: el.getAttribute('aria-label') || '',
                        classes: el.className || '',
                    })),
                    audios: audioElements.map(el => ({
                        src: el.src || el.currentSrc || '',
                        paused: el.paused,
                        muted: el.muted,
                    })),
                    clickables: clickableElements.slice(0, 10).map(el => ({
                        text: el.textContent?.trim() || el.ariaLabel || '',
                        action: el.getAttribute('data-action') || el.getAttribute('onclick') || '',
                    })),
                    editables: editables.slice(0, 10).map(el => ({
                        ariaLabel: el.getAttribute('aria-label') || '',
                        role: el.getAttribute('role') || '',
                        text: (el.innerText || el.textContent || '').trim().substring(0, 100),
                        tag: el.tagName.toLowerCase(),
                        placeholder: el.getAttribute('placeholder') || '',
                        className: el.className || '',
                    })),
                };
            }
        """)
        
        descriptions = []
        
        # Format buttons
        if elements_info.get('buttons'):
            descriptions.append("BUTTONS:")
            for btn in elements_info['buttons']:
                status = " (disabled)" if btn['disabled'] else ""
                id_info = f" #{btn['id']}" if btn['id'] else ""
                descriptions.append(f"  - '{btn['text']}'{id_info}{status}")
        
        # Format inputs
        if elements_info.get('inputs'):
            visible_inputs = [i for i in elements_info['inputs'] if i.get('visible', True)]
            hidden_inputs  = [i for i in elements_info['inputs'] if not i.get('visible', True)]
            if visible_inputs:
                descriptions.append("\nINPUT FIELDS (visible — USE THESE):")
                for inp in visible_inputs:
                    status = " (disabled)" if inp['disabled'] else ""
                    value_info = f" [current: '{inp['value']}']" if inp['value'] else ""
                    placeholder_info = f" placeholder='{inp['placeholder']}'" if inp['placeholder'] else ""
                    aria_info = f" aria-label='{inp['ariaLabel']}'" if inp['ariaLabel'] else ""
                    descriptions.append(f"  - {inp['type']} ({inp['name']}){placeholder_info}{aria_info}{value_info}{status}")
            if hidden_inputs:
                descriptions.append("\nINPUT FIELDS (hidden — DO NOT TARGET — not yet visible on page):")
                for inp in hidden_inputs:
                    descriptions.append(f"  - HIDDEN: {inp['type']} ({inp['name']}) — not visible, skip this")
        
        # Format links
        if elements_info.get('links'):
            descriptions.append("\nLINKS:")
            for link in elements_info['links']:
                descriptions.append(f"  - '{link['text']}'")
        
        # ✅ NEW: Format video elements
        if elements_info.get('videos'):
            descriptions.append("\nVIDEO ELEMENTS:")
            for i, video in enumerate(elements_info['videos']):
                status = "paused" if video['paused'] else "playing"
                muted_status = "muted" if video['muted'] else "unmuted"
                descriptions.append(f"  - Video {i+1}: {status}, {muted_status}")
                if video['duration']:
                    descriptions.append(f"    Duration: {video['duration']:.1f}s, Current: {video['currentTime']:.1f}s")
        
        # ✅ NEW: Format media/content items (YouTube, Netflix, e-commerce, education platforms, etc.)
        if elements_info.get('mediaItems'):
            descriptions.append("\nCONTENT/MEDIA ITEMS (Videos, Products, Courses, etc.):")
            for item in elements_info['mediaItems'][:20]:
                item_id = item['itemId'] if item['itemId'] else "no-id"
                href_info = f" [{item['href'][:45]}...]" if item['href'] else ""
                data_info = f" ({', '.join(item['dataAttributes'][:2])})" if item['dataAttributes'] else ""
                descriptions.append(f"  - {item['title'][:60]} [ID: {item_id}]{data_info}{href_info}")
        
        # ✅ NEW: Format YouTube ad skip buttons (important for ad handling)
        if elements_info.get('youtubeAds'):
            descriptions.append("\n⚠️️ YOUTUBE AD/UI ELEMENTS:")
            for ad_btn in elements_info['youtubeAds']:
                aria_info = f" (aria: {ad_btn['ariaLabel'][:40]})" if ad_btn['ariaLabel'] else ""
                descriptions.append(f"  - {ad_btn['text']}{aria_info}")
        
        # ✅ NEW: Format clickable elements
        if elements_info.get('clickables'):
            descriptions.append("\nOTHER CLICKABLE ELEMENTS:")
            for el in elements_info['clickables']:
                descriptions.append(f"  - '{el['text']}' (action: {el['action'][:30]})")
        
        # ✅ NEW: Format contenteditable fields (Gmail recipient chips, rich text editors, etc.)
        if elements_info.get('editables'):
            descriptions.append("\nCONTENTEDITABLE FIELDS (Gmail recipients, rich text, etc.):")
            for ed in elements_info['editables']:
                if ed['ariaLabel'] or ed['placeholder'] or ed['text']:
                    label_info = f" aria-label='{ed['ariaLabel']}'" if ed['ariaLabel'] else ""
                    placeholder_info = f" placeholder='{ed['placeholder']}'" if ed['placeholder'] else ""
                    text_info = f" [content: '{ed['text'][:40]}...']" if ed['text'] else ""
                    descriptions.append(f"  - <{ed['tag']}>{label_info}{placeholder_info}{text_info}")
        
        result = "\n".join(descriptions) if descriptions else "No interactive elements found on page"
        
        logger.info(f"✅ Extracted {len(elements_info.get('buttons', []))} buttons, "
                   f"{len(elements_info.get('inputs', []))} inputs, "
                   f"{len(elements_info.get('links', []))} links, "
                   f"{len(elements_info.get('videos', []))} videos, "
                   f"{len(elements_info.get('mediaItems', []))} media/content items, "
                   f"{len(elements_info.get('youtubeAds', []))} platform-specific UI elements")
        return result
        
    except Exception as e:
        logger.error(f"❌ Enhanced fallback extraction failed: {e}")
        return "Page semantics unavailable (both methods failed)"

# ============================================================================
# PRIMARY PAGE SEMANTICS EXTRACTOR (WITH ENHANCED FALLBACK)
# ============================================================================

async def get_page_semantics(page) -> str:
    """
    Extract actionable elements from the current page.
    BUG 8 FIX: page.accessibility.snapshot() removed — always uses DOM evaluate().
    """
    return await get_page_semantics_fallback(page)



# ============================================================================
# ENHANCED PAGE CONTEXT FUNCTIONS
# ============================================================================

async def get_page_context(page) -> Dict:
    """Get comprehensive page context including URL, title, and elements."""
    
    try:
        url = page.url
        title = await page.title()
        semantics = await get_page_semantics(page)
        
        # Get viewport info
        viewport = page.viewport_size
        
        # Check if page is loaded
        ready_state = await page.evaluate("() => document.readyState")
        
        # ✅ NEW: Detect page type
        page_type = await detect_page_type(page)
        
        # Detect auth form state if this is a login page
        auth_state = {}
        try:
            from urllib.parse import urlparse
            hostname = urlparse(url).hostname or ''
            if any(d in hostname for d in ['accounts.google', 'login.microsoftonline', 'login.live', 'facebook.com']):
                auth_state = await detect_auth_form_state(page)
        except Exception:
            pass

        return {
            'url': url,
            'title': title,
            'semantics': semantics,
            'viewport': viewport,
            'ready_state': ready_state,
            'is_loaded': ready_state == 'complete',
            'page_type': page_type,
            'auth_state': auth_state,  # FIX: which fields are currently visible
        }
    
    except Exception as e:
        logger.error(f"❌ Failed to get page context: {e}")
        return {
            'url': 'unknown',
            'title': 'unknown',
            'semantics': 'unavailable',
            'is_loaded': False,
            'error': str(e)
        }

async def detect_auth_form_state(page) -> Dict:
    """
    Detect the current state of a multi-step auth form.
    Returns which fields are actually visible (not just present in DOM).
    Google auth: email page has hiddenPassword (display:none), password page shows it.
    """
    try:
        state = await page.evaluate("""
            () => {
                // A field is 'visible' if it has dimensions and is not hidden
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return r.width > 0 && r.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                };

                const emailInput = document.querySelector(
                    'input[type="email"], input[name="identifier"], #identifierId'
                );
                const passwordInput = document.querySelector(
                    'input[type="password"], input[name="password"], input[name="Passwd"]'
                );
                const nextBtn = document.querySelector(
                    'button:contains, [jsname][id="identifierNext"], [id="passwordNext"]'
                ) || Array.from(document.querySelectorAll('button')).find(
                    b => b.textContent.trim().toLowerCase() === 'next'
                );
                const signInBtn = Array.from(document.querySelectorAll('button')).find(
                    b => ['sign in', 'log in', 'next'].includes(b.textContent.trim().toLowerCase())
                );

                return {
                    emailVisible:    isVisible(emailInput),
                    passwordVisible: isVisible(passwordInput),
                    hasNextButton:   !!nextBtn,
                    hasSignInButton: !!signInBtn,
                    currentPage: isVisible(passwordInput) ? 'password_page' : 'email_page',
                };
            }
        """)
        return state or {}
    except Exception as e:
        logger.debug(f"Could not detect auth form state: {e}")
        return {}


async def detect_page_type(page) -> str:
    """
    ✅ NEW: Detect what type of page this is to help with smart intent.
    ✅ ENHANCED: Detects Gmail compose modals and multi-page auth flows
    ✅ FIXED: Auth detection now has HIGHEST priority (before media detection)
    """
    try:
        page_info = await page.evaluate("""
            () => {
                const url = window.location.href;
                const hostname = window.location.hostname;
                
                // Gmail-specific detection
                const isGmailCompose = hostname.includes('mail.google.com') && url.includes('compose');
                const isGmail = hostname.includes('mail.google.com');
                
                // Multi-page auth detection (MUST CHECK FIRST)
                const isGoogleAuth = hostname.includes('accounts.google.com');
                const isMicrosoftAuth = hostname.includes('login.microsoftonline.com') || hostname.includes('login.live.com');
                const isFacebookAuth = hostname.includes('facebook.com') && (url.includes('login') || url.includes('auth'));
                
                // Form field detection
                const emailInput = document.querySelector(
                    'input[type="email"], input[name*="email" i], input[name*="identifier" i], input[aria-label*="email" i]'
                );
                const passwordInput = document.querySelector(
                    'input[type="password"], input[name*="password" i], input[aria-label*="password" i]'
                );
                
                // Captcha detection (blocking auth progress)
                const hasCaptcha = !!document.querySelector(
                    'audio[src*="Captcha"], ' +
                    'div[data-captcha], ' +
                    'div[aria-label*="captcha" i], ' +
                    'iframe[src*="captcha" i], ' +
                    '[role="dialog"] audio'
                );
                
                // Gmail compose detection
                const composeArea = document.querySelector('div[role="dialog"], div[data-tooltip*="compose" i], div[aria-label*="compose" i]');
                const recipientField = document.querySelector('div[contenteditable="true"][aria-label*="To"]');
                const sendButton = document.querySelector('div[role="button"][data-tooltip*="Send" i], button[aria-label*="Send"]');
                
                return {
                    isYouTube: hostname.includes('youtube.com'),
                    isVideo: !!document.querySelector('video'),
                    isAudio: !!document.querySelector('audio'),
                    isForm: !!document.querySelector('form'),
                    isSearch: !!document.querySelector('input[type="search"], input[placeholder*="search" i]'),
                    // Gmail detection
                    isGmailCompose: isGmailCompose,
                    isGmail: isGmail,
                    hasComposeArea: !!composeArea,
                    hasRecipientField: !!recipientField,
                    hasSendButton: !!sendButton,
                    // ⚠️ PRIORITY: Multi-page auth detection (HIGHEST PRIORITY - before media)
                    isGoogleAuth: isGoogleAuth,
                    isMicrosoftAuth: isMicrosoftAuth,
                    isFacebookAuth: isFacebookAuth,
                    hasEmailField: !!emailInput,
                    hasPasswordField: !!passwordInput,
                    hasCaptcha: hasCaptcha,
                };
            }
        """)
        
        # ⚠️ PRIORITY 0: Gmail compose detection (highest)
        if page_info.get('isGmailCompose') or (page_info.get('isGmail') and page_info.get('hasComposeArea')):
            return 'gmail_compose'
        
        # ⚠️ PRIORITY 1: Multi-page auth detection (BEFORE media detection)
        # This MUST come before video/audio detection because auth pages may have captcha audio
        if page_info.get('isGoogleAuth'):
            if page_info.get('hasCaptcha'):
                return 'google_auth_captcha_page'
            elif page_info.get('hasPasswordField') and not page_info.get('hasEmailField'):
                return 'google_auth_password_page'
            elif page_info.get('hasEmailField'):
                return 'google_auth_email_page'
            else:
                return 'google_auth'
        elif page_info.get('isMicrosoftAuth'):
            if page_info.get('hasPasswordField'):
                return 'microsoft_auth_password_page'
            else:
                return 'microsoft_auth'
        elif page_info.get('isFacebookAuth'):
            return 'facebook_auth'
        
        # ⚠️ PRIORITY 2+: Media/content detection (lower priority)
        elif page_info.get('isYouTube'):
            return 'youtube'
        elif page_info.get('isVideo'):
            return 'video'
        elif page_info.get('isAudio'):
            return 'audio'
        elif page_info.get('isForm'):
            return 'form'
        elif page_info.get('isSearch'):
            return 'search'
        else:
            return 'general'
            
    except Exception as e:
        logger.debug(f"Could not detect page type: {e}")
        return 'unknown'

async def wait_for_page_stable(page, timeout: int = 5000):
    """Wait for page to be stable (network idle + DOM mutations settled)."""
    
    try:
        await page.wait_for_load_state('networkidle', timeout=timeout)
        await page.wait_for_timeout(500)
        logger.debug("✅ Page is stable")
        
    except Exception as e:
        logger.debug(f"⚠️ Page may not be fully stable: {e}")

async def element_exists(page, selector: str, timeout: int = 2000) -> bool:
    """Check if an element exists on the page."""
    try:
        await page.wait_for_selector(selector, timeout=timeout, state='visible')
        return True
    except:
        return False

async def get_element_info(page, selector: str) -> Optional[Dict]:
    """Get detailed information about an element."""
    
    try:
        element = await page.query_selector(selector)
        if not element:
            return None
        
        info = await element.evaluate("""
            (el) => ({
                tagName: el.tagName,
                text: el.textContent?.trim(),
                value: el.value,
                enabled: !el.disabled,
                visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                attributes: {
                    id: el.id,
                    class: el.className,
                    type: el.type,
                    placeholder: el.placeholder,
                    ariaLabel: el.getAttribute('aria-label'),
                    dataAction: el.getAttribute('data-action'),
                }
            })
        """)
        
        return info
        
    except Exception as e:
        logger.debug(f"Could not get element info: {e}")
        return None

async def suggest_selectors(page, description: str) -> List[str]:
    """Suggest possible selectors based on natural language description."""
    
    keywords = description.lower().split()
    selectors = []
    
    if 'search' in keywords:
        selectors.extend([
            'input[type="search"]',
            'input[placeholder*="search" i]',
            'input[aria-label*="search" i]',
            'button[aria-label*="search" i]',
            '#search',
            '.search-box',
            '[data-action*="search"]',
        ])
    
    if 'button' in keywords or 'click' in keywords:
        label_words = [w for w in keywords if w not in ['button', 'click', 'the', 'a']]
        if label_words:
            label = ' '.join(label_words)
            selectors.extend([
                f'button:has-text("{label}")',
                f'button[aria-label*="{label}" i]',
                f'[role="button"]:has-text("{label}")',
                f'[data-action*="{label}"]',
            ])
    
    if 'link' in keywords:
        label_words = [w for w in keywords if w not in ['link', 'click', 'the', 'a']]
        if label_words:
            label = ' '.join(label_words)
            selectors.extend([
                f'a:has-text("{label}")',
                f'[role="link"]:has-text("{label}")',
            ])
    
    # ✅ NEW: Media control selectors
    if any(word in keywords for word in ['play', 'pause', 'video', 'media']):
        selectors.extend([
            'video',
            '[data-action="play"]',
            '[data-action="pause"]',
            '.ytp-play-button',  # YouTube
            'button[aria-label*="play" i]',
            'button[aria-label*="pause" i]',
        ])
    
    if any(word in keywords for word in ['mute', 'volume', 'sound']):
        selectors.extend([
            '[data-action="mute"]',
            'button[aria-label*="mute" i]',
            'button[aria-label*="volume" i]',
            '.ytp-mute-button',  # YouTube
        ])
    
    return selectors

async def build_rag_context(page, task_description: str) -> str:
    """
    Build complete context string for RAG prompt.
    ✅ ENHANCED: Includes page type and smart intent guidance.
    """
    
    context = await get_page_context(page)
    
    # ✅ NEW: Add page type specific guidance
    page_type_guidance = ""
    if context.get('page_type') == 'gmail_compose':
        page_type_guidance = """
✉️ GMAIL COMPOSE MODAL DETECTED - Special Guidelines:
- This is a Gmail compose window (creating a new email)
- Recipient field is likely a contenteditable DIV with aria-label="To"
- Subject field should be at index 'subjectbox' 
- Content/body is usually a rich text editor (contenteditable div)
- Send button is typically a div with data-tooltip="Send" or aria-label="Send"
- Try contenteditable elements first if regular inputs aren't found
- The modal might hide elements until you interact with them
"""
    elif context.get('page_type') == 'google_auth_captcha_page':
        page_type_guidance = """
⚠️ GOOGLE AUTH - CAPTCHA CHALLENGE DETECTED:
- Account has triggered captcha verification (security check)
- Password field is HIDDEN until captcha is solved/skipped
- Options:
  1. Try clicking "Skip" or "Skip for now" button if available
  2. Try clicking "Can't access your account?" or "Try another way"
  3. Look for alternative verification method buttons
  4. Solve the audio/image captcha if needed (not automated)
- After dismissing captcha, password field will become visible
🚨 Important: Do NOT try to fill password while on captcha page - it will fail
"""
    elif context.get('page_type') == 'google_auth_email_page':
        page_type_guidance = """
🔐 GOOGLE AUTH - EMAIL PAGE DETECTED:
- Only email/identifier field is visible
- Password field will appear after clicking "Next"
- Look for "Next", "Continue`, or similar button to proceed
"""
    elif context.get('page_type') == 'google_auth_password_page':
        page_type_guidance = """
🔐 GOOGLE AUTH - PASSWORD PAGE DETECTED:
- Only password field is visible (email was on previous page)
- Look for "Sign in" or "Login" button
"""
    elif context.get('page_type') == 'youtube':
        page_type_guidance = """
📺 YOUTUBE DETECTED - Special Guidelines:
- For media controls, prefer keyboard shortcuts over clicking UI elements
- Keyboard shortcuts: k=play/pause, m=mute, Shift+N=next video
- UI elements may be hidden or localized - use shortcuts when possible
"""
    elif context.get('page_type') == 'video':
        page_type_guidance = """
🎬 VIDEO PAGE DETECTED:
- Video element is present - direct video manipulation available
- Can use page.evaluate() to control video: video.paused, video.muted, etc.
"""
    
    context_parts = [
        "="*80,
        "CURRENT PAGE STATE",
        "="*80,
        f"URL: {context['url']}",
        f"Title: {context['title']}",
        f"Page Type: {context.get('page_type', 'unknown')}",
        f"Page Loaded: {context['is_loaded']}",
        "",
        page_type_guidance,
        "",
        "AVAILABLE INTERACTIVE ELEMENTS:",
        context['semantics'],
        "",
        "="*80,
        "USER TASK",
        "="*80,
        task_description,
        "",
        "ENHANCED RULES:",
        "1. PRIMARY: Use ONLY elements that exist in the list above",
        "2. SMART INTENT: If element not listed:",
        "   - For YouTube/Video: Use keyboard shortcuts (k, m, Shift+N, etc.)",
        "   - For other missing elements: Use page.evaluate() to manipulate DOM directly",
        "   - Try alternative selectors (aria-label, data-action, etc.)",
        "3. SUCCESS: Print 'EXECUTION_SUCCESS' only when intended outcome achieved",
        "4. FAILURE: Print 'FAILED: [reason]' if element truly doesn't exist",
        ""
    ]
    
    return "\n".join(context_parts)

async def detect_video_player(page) -> Optional[Dict]:
    """Detect video player on page and its state."""
    
    try:
        video_info = await page.evaluate("""
            () => {
                const video = document.querySelector('video');
                if (!video) return null;
                
                return {
                    exists: true,
                    paused: video.paused,
                    currentTime: video.currentTime,
                    duration: video.duration,
                    playing: !video.paused && video.currentTime > 0,
                    muted: video.muted,
                    volume: video.volume,
                    ended: video.ended,
                    readyState: video.readyState,
                    src: video.src || video.currentSrc,
                };
            }
        """)
        
        return video_info
        
    except Exception as e:
        logger.debug(f"No video player found: {e}")
        return None

# ============================================================================
# ✅ NEW: YOUTUBE-SPECIFIC HELPERS
# ============================================================================

async def get_youtube_player_state(page) -> Optional[Dict]:
    """
    Get YouTube player state specifically.
    Useful for detecting playlists, player mode, etc.
    """
    try:
        yt_state = await page.evaluate("""
            () => {
                // Check if YouTube
                if (!window.location.hostname.includes('youtube.com')) {
                    return null;
                }
                
                const player = document.querySelector('#movie_player');
                const video = document.querySelector('video');
                
                if (!player) return null;
                
                return {
                    isYouTube: true,
                    hasPlayer: true,
                    isPlaylist: !!document.querySelector('[aria-label*="playlist" i], #playlist'),
                    playerMode: player.className.includes('ytp-fullscreen') ? 'fullscreen' : 'normal',
                    controlsVisible: !!document.querySelector('.ytp-chrome-bottom:not(.ytp-autohide)'),
                    video: video ? {
                        paused: video.paused,
                        muted: video.muted,
                        currentTime: video.currentTime,
                        duration: video.duration,
                    } : null,
                };
            }
        """)
        
        return yt_state
        
    except Exception as e:
        logger.debug(f"Not a YouTube page or error: {e}")
        return None