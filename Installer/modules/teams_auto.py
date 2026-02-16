"""Teams automation via pywinauto."""

import os
import re
import time
import threading
import config
from PyQt6.QtCore import QObject, pyqtSignal

# Pywinauto imports
from pywinauto import Desktop


class TeamsController(QObject):
    """Teams call automation."""
    
    call_ended_signal = pyqtSignal()
    call_started_signal = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.teams_process_name = config.TEAMS_PROCESS_NAME
        self.monitor_thread = None
        self.monitoring = False
        self.baseline_windows = None
    
    def start_call(self, phone_number: str) -> bool:
        try:
            print(f"[TeamsController] Initiating call to {phone_number}...")
            
            clean_number = re.sub(r'[\s\-\(\)]', '', phone_number)
            callto_url = f"callto:{clean_number}"
            os.system(f'start {callto_url}')

            print("[TeamsController] Scanning for confirmation dialog...")

            desktop = Desktop(backend="uia")
            max_retries = 20  # ~10 seconds
            found_button = None
            self.baseline_windows = set()  # Initialize baseline windows set

            for i in range(max_retries):
                # 1. Check if call is already active (Auto-dial scenario)
                if self.is_call_active(desktop):
                    print("[TeamsController] Active call detected immediately.")
                    return True

                # 2. Find Main Teams Window
                try:
                    teams_window = desktop.window(title_re=".*Microsoft Teams.*", visible_only=True)
                except Exception:
                    teams_window = None

                if teams_window:
                    try:
                        # Check if window exists (with timeout)
                        if teams_window.exists(timeout=0.5):
                            # Find ALL "Call" buttons
                            buttons = teams_window.descendants(control_type="Button", title="Call")
                            for btn in buttons:
                                # Walk up the tree (depth 20) to find the specific dialog text
                                is_popup_button = False
                                current = btn
                                for _ in range(20):
                                    try:
                                        parent = current.parent()
                                        if not parent:
                                            break
                                        p_title = parent.window_text()
                                        
                                        # This is the specific text from the old dialog strategy
                                        if "Would you like to call this number" in p_title:
                                            is_popup_button = True
                                            break
                                        
                                        # Stop if we hit the main window shell
                                        if "Microsoft Teams" in p_title and "|" in p_title:
                                            break
                                        current = parent
                                    except:
                                        break
                                
                                if is_popup_button:
                                    found_button = btn
                                    # Set baseline window for the current monitoring logic
                                    try:
                                        handle = teams_window.handle if hasattr(teams_window, 'handle') else None
                                        title = teams_window.window_text() or ""
                                        self.baseline_windows.add((handle, title))
                                        print(f"[TeamsController] Found 'Call' button in confirmation dialog. Captured baseline window: '{title}' (handle: {handle})")
                                    except Exception as e:
                                        print(f"[TeamsController] Error capturing baseline window: {e}")
                                    break
                    except Exception as e:
                        print(f"[TeamsController] Search error in window: {e}")
                        continue
                
                if found_button:
                    break
                time.sleep(0.3)

            # Execute Click with retry logic
            if found_button:
                print("[TeamsController] Clicking button...")
                
                # Get the parent window for focusing
                parent_window = None
                try:
                    parent_window = found_button.top_level_parent()
                except Exception:
                    pass
                
                # Attempt 1: Try clicking immediately
                click_success = False
                try:
                    # Method 1: UIA Invoke (Preferred for Automation)
                    found_button.invoke()
                    print("[TeamsController] Button clicked via invoke (first attempt)")
                    click_success = True
                except Exception as e1:
                    print(f"[TeamsController] Invoke failed (first attempt): {e1}")
                    try:
                        # Method 2: Mouse Click (Fallback)
                        found_button.set_focus()
                        found_button.click_input()
                        print("[TeamsController] Button clicked via mouse (first attempt)")
                        click_success = True
                    except Exception as e2:
                        print(f"[TeamsController] Mouse click failed (first attempt): {e2}")
                
                # Attempt 2: If first attempt failed, focus Teams window and retry
                if not click_success:
                    print("[TeamsController] First click attempt failed, waiting 1s then retrying with focus...")
                    time.sleep(1.0)
                    
                    try:
                        # Focus the Teams window first
                        if parent_window:
                            try:
                                parent_window.set_focus()
                                print("[TeamsController] Focused Teams window for retry")
                                time.sleep(0.2)  # Small delay for focus to take effect
                            except Exception as e_focus:
                                print(f"[TeamsController] Warning: Could not focus window: {e_focus}")
                        
                        # Retry clicking
                        try:
                            found_button.invoke()
                            print("[TeamsController] Button clicked via invoke (retry)")
                            click_success = True
                        except Exception as e3:
                            print(f"[TeamsController] Invoke failed (retry): {e3}")
                            try:
                                found_button.set_focus()
                                found_button.click_input()
                                print("[TeamsController] Button clicked via mouse (retry)")
                                click_success = True
                            except Exception as e4:
                                print(f"[TeamsController] Mouse click failed (retry): {e4}")
                    except Exception as e_retry:
                        print(f"[TeamsController] Retry attempt error: {e_retry}")
                
                if click_success:
                    return True
                else:
                    print("[TeamsController] All click attempts failed")
                    return False
            else:
                print("[TeamsController] Timeout: 'Call' button not found in confirmation dialog.")
                return False

        except Exception as e:
            print(f"[TeamsController] Critical Error: {e}")
            return False

    def is_call_active(self, desktop_obj=None) -> bool:
        """
        Check if a Teams call is currently active.
        """
        if not desktop_obj:
            desktop_obj = Desktop(backend="uia")

        try:
            windows = desktop_obj.windows(title_re=".*Microsoft Teams.*", visible_only=True)
            for w in windows:
                # 1. Check Window Title for timer
                try:
                    if re.search(r"\d{2}:\d{2}", w.window_text() or ""):
                        return True
                except Exception:
                    pass

                # 2. Check for Timer Text element inside window (New Teams)
                try:
                    timer = w.descendants(control_type="Text", title_re=r"^\d{2}:\d{2}$")
                    if timer:
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        return False
    
    def monitor_call_window(self, timer_callback=None, on_ended_callback=None):
        """Monitor Teams call window for connection and end states.
        
        New approach: Window diff detection
        1. Get list of Teams windows BEFORE call starts
        2. After call button is pressed, wait 0.5s
        3. Get new list of Teams windows
        4. Find the NEW window (not in original list) - that's the call window
        5. Monitor that window until it closes
        6. When it closes, call ended
        """
        def monitor_loop():
            self.monitoring = True
            desktop = Desktop(backend="uia")
            
            # Step 1: Use baseline windows captured in start_call (before button was pressed)
            baseline_window_handles = self.baseline_windows if self.baseline_windows is not None else set()
            print(f"[TeamsController] [MONITOR] Using baseline: {len(baseline_window_handles)} Teams window(s) (captured before call)")
            if baseline_window_handles:
                for handle, title in baseline_window_handles:
                    print(f"[TeamsController] [MONITOR]   Baseline: '{title}' (handle: {handle})")
            
            # Step 2: Wait for call window to appear (after button is pressed)
            print("[TeamsController] [MONITOR] Waiting 0.5s for call window to appear...")
            time.sleep(0.5)
            
            # Step 3: Find the NEW window (call window)
            print("[TeamsController] [MONITOR] Looking for new Teams window (call window)...")
            call_window = None
            discovery_attempts = 0
            max_discovery_attempts = 10  # Give it up to 10 seconds to appear
            
            while self.monitoring and not call_window and discovery_attempts < max_discovery_attempts:
                try:
                    current_windows = desktop.windows(title_re=".*Microsoft Teams.*", visible_only=True)
                    print(f"[TeamsController] [MONITOR] Discovery attempt {discovery_attempts + 1}/{max_discovery_attempts}: Found {len(current_windows)} Teams window(s)")
                    
                    for w in current_windows:
                        try:
                            handle = w.handle if hasattr(w, 'handle') else None
                            title = w.window_text() or ""
                            window_id = (handle, title)
                            
                            # Check if this window is NEW (not in baseline)
                            if window_id not in baseline_window_handles:
                                # This is a new Teams window - it's the call window!
                                # Accept it immediately (timer/call buttons may appear later)
                                # Only reject if it's clearly a chat window
                                is_chat_window = "Chat |" in title or title.startswith("Chat |")
                                
                                if not is_chat_window:
                                    call_window = w
                                    print(f"[TeamsController] [MONITOR] ✓ Found NEW Teams window (call window): '{title}' (handle: {handle})")
                                    break
                                else:
                                    print(f"[TeamsController] [MONITOR]   New window found but it's a chat window (skipping): '{title}'")
                        except Exception as e:
                            print(f"[TeamsController] [MONITOR]   Error checking window: {e}")
                            continue
                    
                    if not call_window:
                        print(f"[TeamsController] [MONITOR] No new window found yet, waiting 1s... (attempt {discovery_attempts + 1}/{max_discovery_attempts})")
                        time.sleep(1.0)
                        discovery_attempts += 1
                    else:
                        break
                except Exception as e:
                    print(f"[TeamsController] [MONITOR] Error in discovery: {e}")
                    time.sleep(1.0)
                    discovery_attempts += 1
            
            if call_window:
                window_title_at_start = call_window.window_text() or "Unknown"
                print(f"[TeamsController] [MONITOR] ✓ Starting to monitor call window: '{window_title_at_start}'")
                self.call_started_signal.emit()
                
                # Step 4: Monitor the call window until it closes
                check_count = 0
                while self.monitoring:
                    check_count += 1
                    try:
                        # Check if the call window still exists using multiple methods
                        exists_result = False
                        
                        # Method 1: Try to read window text
                        try:
                            current_title = call_window.window_text()
                            exists_result = True
                        except Exception as e1:
                            print(f"[TeamsController] [MONITOR] Check #{check_count}: Cannot read window text: {e1}")
                            exists_result = False
                        
                        # Method 2: Verify window handle is still in the current Teams windows list
                        if exists_result:
                            try:
                                handle = call_window.handle if hasattr(call_window, 'handle') else None
                                if handle:
                                    # Get current Teams windows and check if our handle is still there
                                    desktop = Desktop(backend="uia")
                                    current_windows = desktop.windows(title_re=".*Microsoft Teams.*", visible_only=True)
                                    found_in_list = False
                                    for w in current_windows:
                                        try:
                                            w_handle = w.handle if hasattr(w, 'handle') else None
                                            if w_handle == handle:
                                                found_in_list = True
                                                if check_count % 10 == 0:  # Log every 10th check
                                                    print(f"[TeamsController] [MONITOR] Check #{check_count}: Window handle {handle} verified in current Teams windows")
                                                break
                                        except Exception:
                                            pass
                                    
                                    if not found_in_list:
                                        print(f"[TeamsController] [MONITOR] Check #{check_count}: ✗ Window handle {handle} NOT found in current Teams windows - window closed!")
                                        exists_result = False
                                else:
                                    if check_count % 10 == 0:
                                        print(f"[TeamsController] [MONITOR] Check #{check_count}: Warning - window has no handle attribute")
                            except Exception as e2:
                                print(f"[TeamsController] [MONITOR] Check #{check_count}: Error verifying window handle: {e2}")
                                # If we can't verify, assume it still exists (conservative)
                                exists_result = True
                        
                        if not exists_result:
                            print(f"[TeamsController] [MONITOR] ✗ Call window no longer exists (check #{check_count}) - call ended")
                            break
                        
                        # Extract timer from the window for callback
                        time_str = "00:00"
                        try:
                            # Try to get timer from window title first
                            current_title = call_window.window_text() or ""
                            match = re.search(r'(\d{2}:\d{2})', current_title)
                            if match:
                                time_str = match.group(1)
                                if check_count % 10 == 0:  # Log every 10th check to reduce noise
                                    print(f"[TeamsController] [MONITOR] Check #{check_count}: Window exists, timer='{time_str}', title='{current_title}'")
                            else:
                                # Fallback: check Text controls inside the window
                                # Fix: Use title= with exact match instead of title_re= (pywinauto UIA doesn't support title_re in descendants)
                                try:
                                    # Get all Text elements and check their content manually
                                    text_elems = call_window.descendants(control_type="Text")
                                    for text_elem in text_elems:
                                        try:
                                            text_content = text_elem.window_text()
                                            if re.match(r'^\d{2}:\d{2}$', text_content):
                                                time_str = text_content
                                                if check_count % 10 == 0:
                                                    print(f"[TeamsController] [MONITOR] Check #{check_count}: Window exists, timer='{time_str}' (from Text element), title='{current_title}'")
                                                break
                                        except Exception:
                                            continue
                                except Exception as e:
                                    if check_count % 10 == 0:
                                        print(f"[TeamsController] [MONITOR] Check #{check_count}: Window exists but error extracting timer: {e}, title='{current_title}'")
                        except Exception as e:
                            if check_count % 10 == 0:
                                print(f"[TeamsController] [MONITOR] Check #{check_count}: Error reading window text: {e}")
                        
                        if timer_callback:
                            timer_callback(time_str)
                        
                        time.sleep(1.0)
                    except Exception as e:
                        # If window reference becomes stale, assume call ended
                        print(f"[TeamsController] [MONITOR] ✗ Exception checking window (check #{check_count}): {e}")
                        import traceback
                        traceback.print_exc()
                        # Assume call ended if we can't check the window
                        break
            else:
                print(f"[TeamsController] [MONITOR] ✗ Could not find new call window after {max_discovery_attempts} attempts")
            
            # Cleanup
            print("[TeamsController] [MONITOR] Monitoring loop ending, cleaning up...")
            self.monitoring = False
            if on_ended_callback:
                print("[TeamsController] [MONITOR] Calling on_ended_callback...")
                on_ended_callback()
            print("[TeamsController] [MONITOR] Emitting call_ended_signal...")
            self.call_ended_signal.emit()

        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitoring = False
            self.monitor_thread.join(timeout=1)
        
        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        self.monitoring = False
    
    def hang_up(self) -> bool:
        """
        Hang up the current call using UIA Pattern Invoke.
        Targets 'ToolBar': 'Calling controls' -> 'Button': 'Leave'
        Works even if window is in background or minimized.
        """
        print("[TeamsController] Attempting UIA Hangup via 'Calling controls' > 'Leave'...")
        try:
            desktop = Desktop(backend="uia")
            hung_up = False
            
            # Find all visible Teams windows
            try:
                windows = desktop.windows(title_re=".*Microsoft Teams.*", visible_only=True)
            except Exception as e:
                print(f"[TeamsController] Error listing windows: {e}")
                windows = []

            for w in windows:
                try:
                    # Search specifically for the 'Calling controls' ToolBar.
                    # This exists in both Main Window (as a nested child) and Compact View.
                    # Using descendants ensures we find it regardless of tree depth.
                    toolbars = w.descendants(control_type="ToolBar", title="Calling controls")
                    
                    if not toolbars:
                        continue
                        
                    for tb in toolbars:
                        # Inside the toolbar, look for the 'Leave' button
                        try:
                            # 'Leave' should be a direct child of the toolbar
                            leave_btn = tb.child_window(control_type="Button", title="Leave")
                            
                            if leave_btn.exists():
                                print(f"[TeamsController] Found 'Leave' button in window: '{w.window_text()}'")
                                
                                # METHOD 1: Invoke Pattern (Best for background/unfocused windows)
                                try:
                                    leave_btn.invoke()
                                    print("[TeamsController] Successfully invoked 'Leave' button.")
                                    hung_up = True
                                except Exception as e_invoke:
                                    print(f"[TeamsController] Invoke failed: {e_invoke}. Trying click_input...")
                                    
                                    # METHOD 2: Physical Click (Fallback)
                                    try:
                                        # Only works if visible, so we ensure focus first
                                        w.set_focus()
                                        leave_btn.click_input()
                                        print("[TeamsController] Clicked 'Leave' button.")
                                        hung_up = True
                                    except Exception as e_click:
                                        print(f"[TeamsController] Click failed: {e_click}")

                                if hung_up:
                                    break
                        except Exception:
                            continue
                    
                    if hung_up:
                        break

                except Exception as e:
                    print(f"[TeamsController] Error processing window for hangup: {e}")
                    continue

            # FALLBACK: Keyboard Shortcut (Ctrl+Shift+H)
            # Only used if UIA search failed to find/click the button
            if not hung_up:
                print("[TeamsController] UIA Hangup failed. Attempting Ctrl+Shift+H fallback...")
                import pyautogui
                
                # Try to focus the most likely call window
                active_call_found = False
                for w in windows:
                    title = w.window_text()
                    # Look for timer in title (e.g., "00:45 | ...") or "Meeting compact view"
                    if re.search(r'\d{2}:\d{2}', title) or "Meeting compact view" in title:
                        try:
                            w.set_focus()
                            active_call_found = True
                            time.sleep(0.1)
                            pyautogui.hotkey('ctrl', 'shift', 'h')
                            print(f"[TeamsController] Sent Ctrl+Shift+H to: {title}")
                            hung_up = True
                            break
                        except Exception:
                            continue
                
                if not active_call_found and windows:
                    # If we couldn't identify a specific call window, try the first Teams window
                    try:
                        windows[0].set_focus()
                        time.sleep(0.1)
                        pyautogui.hotkey('ctrl', 'shift', 'h')
                        print("[TeamsController] Sent Ctrl+Shift+H to primary Teams window.")
                        hung_up = True
                    except Exception:
                        pass

            if hung_up:
                # Wait for call window to close (up to 4 seconds) so next call can start cleanly
                for _ in range(20):
                    time.sleep(0.2)
                    try:
                        still_calling = False
                        # Quick check if any window still has a timer
                        current_wins = desktop.windows(title_re=".*Microsoft Teams.*")
                        for w in current_wins:
                            if re.search(r'\d{2}:\d{2}', w.window_text() or ""):
                                still_calling = True
                                break
                        if not still_calling:
                            break
                    except Exception:
                        break

            return True
        except Exception as e:
            print(f"[TeamsController] Critical Hangup Error: {e}")
            return False


# Alias for backward compatibility
TeamsAutomation = TeamsController
