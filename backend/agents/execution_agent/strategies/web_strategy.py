
import os
import time
import platform
import subprocess
from datetime import datetime
from typing import List

# from backend.agents.execution_agent.core.exec_agent_config import Config, ActionStatus
# from backend.agents.execution_agent.core.exec_agent_models import ExecutionTask, ExecutionResult
# from backend.agents.execution_agent.core.exec_agent_deps import (
#     PYWINAUTO_AVAILABLE, SELENIUM_AVAILABLE,
#     Application, webdriver, By, WebDriverWait, EC
# )
# ✅ CORRECT:
from ..core.exec_agent_config import Config, ActionStatus, StatusCode
from ..core.exec_agent_models import ExecutionTask, ExecutionResult
from ..layers.exec_agent_safety import SafetyLayer
from ..core.exec_agent_deps import SELENIUM_AVAILABLE, webdriver, By, WebDriverWait, EC



class WebStrategy:
    """Execute web automation tasks"""
    
    def __init__(self, logger, safety_layer):
        self.logger = logger
        self.safety = safety_layer
        self.driver = None
    
    def execute(self, task: ExecutionTask) -> ExecutionResult:
        """Execute web automation task"""
        start_time = time.time()
        logs = []
        
        try:
            if not SELENIUM_AVAILABLE:
                return self._create_error_result(task, logs, start_time, "Selenium not available")
            
            action_type = task.params.get("action_type", "")
            
            # Actions that manage their own drivers
            if action_type == "extract_data":
                return self._extract_web_content(task, logs, start_time)
            
            # Browser management
            if action_type == "open_browser":
                return self._open_browser(task, logs, start_time)
            
            if action_type == "close_browser":
                return self._close_browser(task, logs, start_time)
            
            # Initialize browser for other actions if not already open
            if not self.driver:
                try:
                    logs.append("Initializing browser...")
                    self._init_driver(task.params.get("browser", "edge"))
                    logs.append("Browser initialized successfully")
                except Exception as e:
                    return self._create_error_result(task, logs, start_time, f"Failed to open browser: {e}")
            
            # Navigate to URL if provided
            url = task.params.get("url")
            if url:
                self.driver.get(url)
                logs.append(f"Navigated to {url}")
            
            # Handle different action types
            if action_type == "login":
                return self._login(task, logs, start_time)
            elif action_type == "download_file":
                return self._download_file(task, logs, start_time)
            elif action_type == "fill_form":
                return self._fill_form(task, logs, start_time)
            elif action_type == "web_search":
                return self._web_search(task, logs, start_time)
            elif action_type == "click_search_result":
                return self._click_search_result(task, logs, start_time)
            elif action_type == "wait_page_load":
                return self._wait_page_load(task, logs, start_time)
            elif action_type == "find_element":
                return self._find_element(task, logs, start_time)
            elif action_type == "click_element":
                return self._click_web_element(task, logs, start_time)
            elif action_type == "wait_download":
                return self._wait_download(task, logs, start_time)
            elif action_type == "get_page_info":
                return self._get_page_info(task, logs, start_time)
            else:
                return self._create_error_result(task, logs, start_time, f"Unknown web action: {action_type}")
        
        except Exception as e:
            self.logger.error(f"Web execution error: {e}")
            return self._create_error_result(task, logs, start_time, f"Web automation failed: {e}")
        
        finally:
            # Only close if action is close_browser
            if action_type == "close_browser" and self.driver:
                try:
                    self.driver.quit()
                    self.driver = None
                    logs.append("Browser closed in finally block")
                except:
                    pass
    
    # ==================== BROWSER MANAGEMENT ====================
    
    def _init_driver(self, browser="edge"):
        """Initialize WebDriver if not already initialized"""
        if not self.driver:
            try:
                if browser.lower() == "edge":
                    self.driver = webdriver.Edge()
                    self.logger.info("Edge browser initialized")
                elif browser.lower() == "chrome":
                    self.driver = webdriver.Chrome()
                    self.logger.info("Chrome browser initialized")
                else:
                    # Default to Edge
                    self.driver = webdriver.Edge()
                    self.logger.info("Edge browser initialized (default)")
            except Exception as e:
                self.logger.warning(f"Primary browser unavailable: {e}, trying Chrome")
                try:
                    self.driver = webdriver.Chrome()
                    self.logger.info("Chrome browser initialized (fallback)")
                except Exception as e2:
                    raise Exception(f"Failed to initialize any browser: {e2}")
    
    def _open_browser(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Open web browser"""
        browser = task.params.get("browser", "edge")
        url = task.params.get("url", "")
        
        try:
            self._init_driver(browser)
            logs.append(f"{browser.capitalize()} browser opened")
            
            if url:
                self.driver.get(url)
                logs.append(f"Navigated to {url}")
            
            return self._create_success_result(task, logs, start_time, f"{browser.capitalize()} browser opened")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Failed to open browser: {e}")
    
    def _close_browser(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Close web browser"""
        try:
            if self.driver:
                self.driver.quit()
                self.driver = None
                logs.append("Browser closed")
                return self._create_success_result(task, logs, start_time, "Browser closed successfully")
            else:
                return self._create_error_result(task, logs, start_time, "No browser to close")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Failed to close browser: {e}")
    
    # ==================== SEARCH ACTIONS ====================
    
    def _web_search(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Perform a web search via the specified search engine"""
        search_query = task.params.get("search_query")
        search_engine = task.params.get("search_engine", "google").lower()
        
        if not search_query:
            return self._create_error_result(task, logs, start_time, "Missing 'search_query' parameter")
        
        try:
            # Ensure driver is initialized
            if not self.driver:
                logs.append("Initializing WebDriver...")
                self._init_driver()
                time.sleep(2)
            
            # Log the search process
            logs.append(f"Performing web search using {search_engine}: '{search_query}'")
            
            # Clean and encode query safely
            query_encoded = str(search_query).strip().replace(" ", "+")
            
            # Build search URL based on engine
            if search_engine == "google":
                search_url = f"https://www.google.com/search?q={query_encoded}"
            elif search_engine == "bing":
                search_url = f"https://www.bing.com/search?q={query_encoded}"
            elif search_engine == "duckduckgo":
                search_url = f"https://duckduckgo.com/?q={query_encoded}"
            else:
                logs.append(f"Unknown search engine '{search_engine}', defaulting to Google")
                search_url = f"https://www.google.com/search?q={query_encoded}"
            
            # Open browser and navigate
            self.driver.get(search_url)
            logs.append(f"Navigated to {search_url}")
            
            # Wait for results page to load
            time.sleep(3)
            
            # Optionally capture screenshot using Selenium
            screenshot_path = None
            try:
                # Use Selenium's built-in screenshot
                screenshot_dir = "screenshots"
                os.makedirs(screenshot_dir, exist_ok=True)
                screenshot_path = f"{screenshot_dir}/search_{task.task_id}_{int(time.time())}.png"
                self.driver.save_screenshot(screenshot_path)
                logs.append(f"Captured screenshot: {screenshot_path}")
            except Exception as e:
                logs.append(f"Screenshot capture skipped: {e}")
            
            return ExecutionResult(
                status=ActionStatus.SUCCESS.value,
                task_id=task.task_id,
                context=task.context,
                action="web_search",
                details=f"Web search for '{search_query}' completed successfully",
                logs=logs,
                timestamp=datetime.now().isoformat(),
                duration=time.time() - start_time,
                screenshot_path=screenshot_path
            )
        
        except Exception as e:
            logs.append(f"Web search error: {e}")
            return self._create_error_result(task, logs, start_time, f"Web search failed: {e}")
    
    def _click_search_result(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Click on a search result with improved reliability"""
        result_index = task.params.get("result_index", 0)
        
        try:
            # Wait for page to stabilize
            time.sleep(3)
            logs.append(f"Looking for search result #{result_index}")
            
            # Scroll down a bit to ensure results are visible
            self.driver.execute_script("window.scrollBy(0, 300);")
            time.sleep(1)
            
            # Try multiple selectors for different search engines
            result_selectors = [
                'li.b_algo h2 a',  # Bing
                'div.g a h3',  # Google
                'article.result a',  # DuckDuckGo
                'h3 a',  # Generic
                'a[href^="http"]'  # Any external link
            ]
            
            results = []
            for selector in result_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    # Filter out non-visible or ad elements
                    visible_results = [elem for elem in elements if elem.is_displayed() and elem.get_attribute('href')]
                    if visible_results:
                        results = visible_results
                        logs.append(f"Found {len(results)} results using selector: {selector}")
                        break
                except:
                    continue
            
            if not results:
                return self._create_error_result(task, logs, start_time, "No search results found")
            
            if len(results) <= result_index:
                return self._create_error_result(task, logs, start_time, 
                    f"Result #{result_index} not found (only {len(results)} results available)")
            
            # Click using JavaScript as fallback for intercepted clicks
            target_result = results[result_index]
            try:
                # Try normal click first
                target_result.click()
                logs.append(f"Clicked search result #{result_index}")
            except Exception as e:
                # Fallback to JavaScript click
                logs.append(f"Normal click failed, using JavaScript click: {e}")
                self.driver.execute_script("arguments[0].click();", target_result)
                logs.append(f"JavaScript clicked search result #{result_index}")
            
            time.sleep(2)
            return self._create_success_result(task, logs, start_time, f"Clicked result #{result_index}")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Failed to click result: {e}")
    
    # ==================== PAGE INTERACTION ACTIONS ====================
    
    def _wait_page_load(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Wait for page to load"""
        timeout = task.params.get("timeout", 10)
        
        try:
            logs.append(f"Waiting for page to load (timeout: {timeout}s)")
            time.sleep(timeout)
            logs.append("Page load wait completed")
            
            return self._create_success_result(task, logs, start_time, "Page loaded")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Wait failed: {e}")
    
    def _find_element(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Find element on page"""
        element_type = task.params.get("element_type", "button")
        search_text = task.params.get("search_text", [])
        search_method = task.params.get("search_method", "text")
        
        try:
            logs.append(f"Searching for {element_type} with text: {search_text}")
            
            # Ensure search_text is a list
            if isinstance(search_text, str):
                search_text = [search_text]
            
            for text in search_text:
                try:
                    if search_method == "text":
                        element = self.driver.find_element(By.PARTIAL_LINK_TEXT, text)
                    elif search_method == "xpath":
                        element = self.driver.find_element(By.XPATH, f"//*[contains(text(), '{text}')]")
                    else:
                        element = self.driver.find_element(By.PARTIAL_LINK_TEXT, text)
                    
                    if element:
                        logs.append(f"Found element: {text}")
                        return self._create_success_result(task, logs, start_time, f"Element found: {text}")
                except:
                    continue
            
            return self._create_error_result(task, logs, start_time, "Element not found")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Find element failed: {e}")
    
    def _click_web_element(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Click element on webpage with improved reliability and download link detection"""
        element_selector = task.params.get("element_selector")
        selector_type = task.params.get("selector_type", "css")
        wait_time = task.params.get("wait_time", 10)
        
        if not element_selector:
            return self._create_error_result(task, logs, start_time, "Missing element_selector parameter")
        
        try:
            logs.append(f"Current URL: {self.driver.current_url}")
            logs.append(f"Searching for download link on page")
            
            # Strategy 1: Find download links by href attribute
            download_links = []
            try:
                # Look for links with download in href or text
                all_links = self.driver.find_elements(By.TAG_NAME, "a")
                logs.append(f"Found {len(all_links)} total links on page")
                
                for link in all_links:
                    try:
                        href = link.get_attribute("href") or ""
                        text = (link.text or "").strip().lower()
                        
                        # Check if it's a download link
                        if any(keyword in href.lower() for keyword in ["download", ".pdf", ".zip", ".doc", ".xls", ".ppt", "/file/", "/get/"]) or \
                           any(keyword in text for keyword in ["download", "get file", "save", "pdf", "get it", "grab"]):
                            if link.is_displayed() and href:
                                download_links.append({
                                    'element': link,
                                    'text': text[:50],
                                    'href': href[:100]
                                })
                                logs.append(f"  → Found: '{text[:30]}' - {href[:60]}")
                    except:
                        continue
                
                if download_links:
                    logs.append(f"Total {len(download_links)} download link(s) found")
                    
                    # Try each download link
                    for idx, link_info in enumerate(download_links[:3]):  # Try first 3
                        try:
                            target_link = link_info['element']
                            logs.append(f"Attempting link #{idx + 1}: {link_info['text']}")
                            
                            # Scroll into view
                            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_link)
                            time.sleep(0.5)
                            
                            # Try clicking
                            try:
                                target_link.click()
                                logs.append("Clicked download link successfully")
                                time.sleep(2)
                                return self._create_success_result(task, logs, start_time, "Download link clicked")
                            except Exception as e:
                                # JavaScript fallback
                                logs.append(f"Normal click failed: {e}, trying JS click")
                                self.driver.execute_script("arguments[0].click();", target_link)
                                logs.append("Clicked download link via JavaScript")
                                time.sleep(2)
                                return self._create_success_result(task, logs, start_time, "Download initiated via JS")
                        except Exception as e:
                            logs.append(f"Link #{idx + 1} failed: {e}")
                            continue
                else:
                    logs.append("No download links found using keyword matching")
            except Exception as e:
                logs.append(f"Download link search error: {e}")
            
            # Strategy 2: Try buttons
            try:
                buttons = self.driver.find_elements(By.TAG_NAME, "button")
                logs.append(f"Found {len(buttons)} buttons on page")
                
                for button in buttons:
                    try:
                        text = (button.text or "").strip().lower()
                        if any(keyword in text for keyword in ["download", "get", "save", "grab"]):
                            if button.is_displayed():
                                logs.append(f"Found download button: {text}")
                                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                                time.sleep(0.5)
                                try:
                                    button.click()
                                except:
                                    self.driver.execute_script("arguments[0].click();", button)
                                logs.append("Clicked download button")
                                time.sleep(2)
                                return self._create_success_result(task, logs, start_time, "Download button clicked")
                    except:
                        continue
            except Exception as e:
                logs.append(f"Button search error: {e}")
            
            # Strategy 3: Check if current page IS the file (direct link)
            try:
                current_url = self.driver.current_url
                content_type = self.driver.execute_script("return document.contentType;")
                logs.append(f"Page content type: {content_type}")
                
                # Check if we're already on a downloadable file
                if any(ext in current_url.lower() for ext in ['.pdf', '.zip', '.doc', '.xls']) or \
                   'application/' in str(content_type):
                    logs.append("Current page appears to be a direct file link")
                    # File should auto-download or we can use requests to download
                    return self._create_success_result(task, logs, start_time, "Direct file page detected")
            except Exception as e:
                logs.append(f"Direct file check error: {e}")
            
            # Strategy 4: Look for iframe with download content
            try:
                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                if iframes:
                    logs.append(f"Found {len(iframes)} iframes, checking for download content")
                    for idx, iframe in enumerate(iframes[:2]):  # Check first 2
                        try:
                            self.driver.switch_to.frame(iframe)
                            iframe_links = self.driver.find_elements(By.TAG_NAME, "a")
                            for link in iframe_links:
                                text = (link.text or "").lower()
                                href = link.get_attribute("href") or ""
                                if "download" in text or "download" in href:
                                    link.click()
                                    logs.append(f"Clicked download link in iframe #{idx}")
                                    time.sleep(2)
                                    self.driver.switch_to.default_content()
                                    return self._create_success_result(task, logs, start_time, "Download from iframe")
                            self.driver.switch_to.default_content()
                        except:
                            self.driver.switch_to.default_content()
                            continue
            except Exception as e:
                logs.append(f"Iframe check error: {e}")
            
            # If all strategies failed, save page source for debugging
            logs.append("All download strategies failed")
            logs.append(f"Page title: {self.driver.title}")
            
            # Save a small snippet of page source
            try:
                page_source = self.driver.page_source[:500]
                logs.append(f"Page source preview: {page_source}...")
            except:
                pass
            
            return self._create_error_result(task, logs, start_time, 
                "No download link, button, or direct file found. Page may require different search query or manual download.")
        
        except Exception as e:
            import traceback
            logs.append(f"Exception: {traceback.format_exc()}")
            return self._create_error_result(task, logs, start_time, f"Click failed: {e}")
    
    def _get_page_info(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Get page information"""
        info_type = task.params.get("info_type", "title")
        
        try:
            if info_type == "title":
                info = self.driver.title
                logs.append(f"Page title: {info}")
            elif info_type == "url":
                info = self.driver.current_url
                logs.append(f"Current URL: {info}")
            else:
                return self._create_error_result(task, logs, start_time, f"Unknown info type: {info_type}")
            
            return ExecutionResult(
                status=ActionStatus.SUCCESS.value,
                task_id=task.task_id,
                context=task.context,
                action=task.action_type,
                details=f"Retrieved {info_type}: {info}",
                logs=logs,
                timestamp=datetime.now().isoformat(),
                duration=time.time() - start_time,
                metadata={info_type: info}
            )
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Failed to get page info: {e}")
    
    # ==================== FORM ACTIONS ====================
    
    def _login(self, task, logs, start_time):
        """Login to web application"""
        username = task.params.get("username")
        password = task.params.get("password")
        username_field_name = task.params.get("username_field", "username")
        password_field_name = task.params.get("password_field", "password")
        
        try:
            username_field = WebDriverWait(self.driver, Config.WEB_WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.NAME, username_field_name))
            )
            username_field.send_keys(username)
            logs.append("Username entered")
            
            password_field = self.driver.find_element(By.NAME, password_field_name)
            password_field.send_keys(password)
            logs.append("Password entered")
            
            login_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            login_button.click()
            logs.append("Login submitted")
            
            time.sleep(3)
            
            return self._create_success_result(task, logs, start_time, "Login successful")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Login failed: {e}")
    
    def _fill_form(self, task, logs, start_time):
        """Fill web form"""
        form_data = task.params.get("form_data", {})
        
        try:
            for field_name, value in form_data.items():
                field = self.driver.find_element(By.NAME, field_name)
                field.send_keys(value)
                logs.append(f"Filled field: {field_name}")
            
            return self._create_success_result(task, logs, start_time, "Form filled successfully")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Form filling failed: {e}")
    
    # ==================== DOWNLOAD ACTIONS ====================
    
    def _download_file(self, task, logs, start_time):
        """Download file from web"""
        file_link_text = task.params.get("file_link_text")
        
        try:
            download_link = WebDriverWait(self.driver, Config.WEB_WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.LINK_TEXT, file_link_text))
            )
            
            download_link.click()
            logs.append(f"Clicked download link: {file_link_text}")
            time.sleep(5)
            
            return self._create_success_result(task, logs, start_time, "File download initiated")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Download failed: {e}")
    
    def _wait_download(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Wait for download to complete"""
        max_wait = task.params.get("max_wait_time", 60)
        
        try:
            logs.append(f"Waiting for download (max: {max_wait}s)")
            time.sleep(max_wait)
            logs.append("Download wait completed")
            
            return self._create_success_result(task, logs, start_time, "Download completed")
        
        except Exception as e:
            return self._create_error_result(task, logs, start_time, f"Download wait failed: {e}")
    
    # ==================== CONTENT EXTRACTION ====================
    
    def _extract_web_content(self, task: ExecutionTask, logs: List, start_time: float) -> ExecutionResult:
        """Extract text from the first website after a search result"""
        if not SELENIUM_AVAILABLE:
            return self._create_error_result(task, logs, start_time, "Selenium not available for extraction")
        
        search_engine = task.params.get("search_engine", "bing")
        search_query = task.params.get("search_query", "")
        driver = None
        
        try:
            # Create driver for extraction
            try:
                driver = webdriver.Edge()
                logs.append("Edge browser opened for content extraction")
            except Exception as e:
                try:
                    driver = webdriver.Chrome()
                    logs.append("Chrome browser opened for content extraction (Edge unavailable)")
                except Exception as e2:
                    return self._create_error_result(task, logs, start_time, f"Failed to open browser: {e2}")
            
            driver.implicitly_wait(10)
            logs.append(f"Searching for: {search_query}")
            
            # Build search URL
            search_url = f"https://www.bing.com/search?q={search_query.replace(' ', '+')}"
            driver.get(search_url)
            logs.append(f"Opened search page: {search_url}")
            time.sleep(2)
            
            # Find first organic result link
            first_url = None
            try:
                first_result = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'li.b_algo a'))
                )
                first_url = first_result.get_attribute("href")
                logs.append(f"Navigating to first result: {first_url}")
                driver.get(first_url)
                time.sleep(2)
            except Exception as e:
                logs.append(f"Warning: Could not find first result link: {e}")
                try:
                    first_result = driver.find_element(By.CSS_SELECTOR, 'h2 a')
                    first_url = first_result.get_attribute("href")
                    logs.append(f"Found alternative result: {first_url}")
                    driver.get(first_url)
                    time.sleep(2)
                except Exception as e2:
                    return self._create_error_result(task, logs, start_time, f"Failed to find search result: {e2}")
            
            # Extract main text
            paragraphs = driver.find_elements(By.TAG_NAME, "p")
            text_content = "\n".join([p.text for p in paragraphs if p.text.strip()])
            
            if not text_content or len(text_content) < 50:
                try:
                    body = driver.find_element(By.TAG_NAME, "body")
                    text_content = body.text
                    logs.append("Extracted content from body element")
                except:
                    pass
            
            if not text_content or len(text_content) < 10:
                text_content = f"Content extraction from {first_url} - Limited content available."
                logs.append("Warning: Minimal content extracted")
            
            logs.append(f"Extracted {len(text_content)} characters from first result.")
            
            return ExecutionResult(
                status=ActionStatus.SUCCESS.value,
                task_id=task.task_id,
                context=task.context,
                action=task.action_type,
                details=f"Extracted {len(text_content)} characters of web content.",
                logs=logs,
                timestamp=datetime.now().isoformat(),
                duration=time.time() - start_time,
                metadata={"web_content": text_content, "source_url": first_url}
            )
            
        except Exception as e:
            error_msg = f"Web content extraction failed: {e}"
            logs.append(f"Extraction error: {e}")
            import traceback
            logs.append(f"Traceback: {traceback.format_exc()}")
            return self._create_error_result(task, logs, start_time, error_msg)
        finally:
            if driver:
                try:
                    driver.quit()
                    logs.append("Browser closed")
                except:
                    pass
    
    # ==================== HELPER METHODS ====================
    
    def _create_success_result(self, task, logs, start_time, details):
        """Helper to create success result"""
        return ExecutionResult(
            status=ActionStatus.SUCCESS.value,
            task_id=task.task_id,
            context=task.context,
            action=task.action_type,
            details=details,
            logs=logs,
            timestamp=datetime.now().isoformat(),
            duration=time.time() - start_time
        )
    
    def _create_error_result(self, task, logs, start_time, error):
        """Helper to create error result"""
        logs.append(error)
        return ExecutionResult(
            status=ActionStatus.FAILED.value,
            task_id=task.task_id,
            context=task.context,
            action=task.action_type,
            details=error,
            logs=logs,
            timestamp=datetime.now().isoformat(),
            duration=time.time() - start_time,
            error=error
        )