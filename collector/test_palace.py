from playwright.sync_api import sync_playwright
import json

BASE = "https://www.palacecinemas.com.au"

def inspect(page, path):
    responses = []
    def capture(response):
        ct = response.headers.get("content-type", "")
        if "json" in ct:
            try:
                body = response.text()
            except Exception:
                body = ""
            if "palace-cinemas.workers.dev" in response.url or "/_next/data/" in response.url:
                responses.append({"status": response.status, "url": response.url, "body": body[:5000]})
    page.on("response", capture)
    response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    print("BEFORE", path, response.status if response else None, page.title(), flush=True)
    print("SELECTS_BEFORE", json.dumps(page.locator("select").evaluate_all("(els)=>els.map(e=>({label:e.getAttribute('aria-label'),value:e.value,html:e.outerHTML.slice(0,1500)}))")), flush=True)
    for sel in page.locator('select[aria-label="Choose your location"]').all():
        try:
            sel.select_option("canberra")
        except Exception as e:
            print("SELECT_ERROR", str(e), flush=True)
    page.wait_for_timeout(5000)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    print("AFTER", page.url, flush=True)
    print("SELECTS_AFTER", json.dumps(page.locator("select").evaluate_all("(els)=>els.map(e=>({label:e.getAttribute('aria-label'),value:e.value,html:e.outerHTML.slice(0,1500)}))")), flush=True)
    print("JSON_API", json.dumps(responses, ensure_ascii=False)[:22000], flush=True)
    print("BODY", page.locator("body").inner_text()[:6500].replace("\\n"," | "), flush=True)
    print("MOVIE_HREFS", json.dumps(page.locator('a[href*="/movies/"]').evaluate_all("(els)=>els.map(e=>({text:e.innerText.trim(),href:e.href})).filter(x=>x.text).slice(0,80)"), ensure_ascii=False), flush=True)
    page.remove_listener("response", capture)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        inspect(page, "/movies")
        inspect(page, "/movies/coming-soon")
        browser.close()

if __name__ == "__main__":
    main()
