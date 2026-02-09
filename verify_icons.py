from playwright.sync_api import sync_playwright
import time

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto("http://localhost:8000/trivial_endings.html")
            page.wait_for_selector("header")

            # Wait a bit for everything to settle
            time.sleep(1)

            # Take a screenshot of the header
            header = page.locator("header")
            header.screenshot(path="verification_header.png")
            print("Screenshot taken: verification_header.png")

        except Exception as e:
            print(f"Error: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    run()
