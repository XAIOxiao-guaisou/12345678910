import asyncio
import os
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def test_volcengine(headless=False):
    print(f"Testing Volcengine Settings Automation with headless={headless}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--window-size=1440,900'
            ]
        )
        
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.2277.112 Safari/537.36"
        session_file = Path("./browser_session/default/volcengine_session.json")
        
        if session_file.exists():
            with open(session_file, "r") as f:
                state = json.load(f)
            context = await browser.new_context(storage_state=state, user_agent=user_agent)
            print("Loaded session file successfully.")
        else:
            print("No session file found! Please login via the Web UI first.")
            context = await browser.new_context(user_agent=user_agent)

        page = await context.new_page()
        
        url = "https://console.volcengine.com/ark/region:ark+cn-beijing/experience/vision?modelId=doubao-seedance-2-0-260128&tab=GenVideo"
        print("Navigating to Volcengine...")
        await page.goto(url)
        
        # Wait until the rich text editor (ProseMirror) is visible, meaning we are logged in and on the console page
        print("Waiting for page to load (please log in manually if you haven't)...")
        try:
            input_box = page.locator('div.ProseMirror').last
            await input_box.wait_for(state="visible", timeout=120000) # 2 mins to login
            print("✅ Logged in and page loaded.")
        except Exception as e:
            print("❌ Timeout waiting for page to load or login.")
            return

        await asyncio.sleep(5) # Extra wait for all React elements to render
        
        try:
            print("Step 1: Looking for the parameter settings toolbar...")
            # According to the screenshot, it's a toolbar at the bottom containing "智能比例", "720p", "5秒"
            # It's an arco-btn or similar.
            
            # Since the UI is React-based, let's try to find the button containing "5 秒" or "智能比例"
            settings_toolbar = page.locator('text="5 秒"').last
            if await settings_toolbar.is_visible():
                print("Found toolbar via '5 秒' text. Clicking...")
                await settings_toolbar.click(force=True)
            else:
                settings_toolbar = page.locator('div, span, button', has_text="智能比例").last
                if await settings_toolbar.is_visible():
                    print("Found toolbar via '智能比例' text. Clicking...")
                    await settings_toolbar.click(force=True)
                else:
                    print("Could not find the toolbar! Aborting.")
                    return
                
            await asyncio.sleep(2) # Wait for popup animation
            
            print("Step 2: Looking for '16:9' aspect ratio button...")
            # The 16:9 button is inside the popup
            ratio_16_9 = page.locator('text="16:9"').last
            if await ratio_16_9.is_visible():
                await ratio_16_9.click(force=True)
                print("Clicked 16:9 aspect ratio.")
            else:
                print("Could not find exactly '16:9'. Trying alternative selectors...")
                
            await asyncio.sleep(2)
            
            print("Step 3: Looking for duration input (5 seconds -> 15 seconds)...")
            # There is an input box with value 5
            # It might be an input of type 'number' or just a text input next to '秒'
            inputs = await page.get_by_role("spinbutton").all()
            if len(inputs) > 0:
                print(f"Found {len(inputs)} spinbuttons (number inputs).")
                for inp in inputs:
                    val = await inp.input_value()
                    if val == "5":
                        print("Found duration input with value 5. Changing to 15...")
                        await inp.fill("15")
                        break
            else:
                # If not a spinbutton, look for an input placeholder or standard input
                # Try finding input by nearby text
                print("No spinbutton found, trying generic input search...")
                duration_input = page.locator('input[value="5"]').last
                if await duration_input.count() > 0:
                    await duration_input.fill("15")
                    print("Filled duration to 15.")
                    
            await asyncio.sleep(2)
            
            # Click outside to close the popup
            print("Clicking body to close popup...")
            await page.mouse.click(10, 10)
            await asyncio.sleep(2)
            
            print("Automation script executed. Leaving browser open for 10 seconds for visual inspection.")
            await asyncio.sleep(10)
            
        except Exception as e:
            print(f"Error during interaction: {e}")
            await page.screenshot(path="test_error.png")
            print("Saved error screenshot to test_error.png")
        finally:
            await context.close()
            await browser.close()

if __name__ == "__main__":
    import sys
    headless_arg = False # Default to false for visual debugging
    if len(sys.argv) > 1 and sys.argv[1].lower() == "true":
        headless_arg = True
    asyncio.run(test_volcengine(headless=headless_arg))
