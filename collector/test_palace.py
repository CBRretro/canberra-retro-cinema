from playwright.sync_api import sync_playwright
import json

URL = "https://www.palacecinemas.com.au/cinemas/palace-electric-cinema"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        responses = []
        def capture(r):
            ct = r.headers.get("content-type", "")
            if "json" in ct or "javascript" in ct:
                responses.append({"status": r.status, "type": ct, "url": r.url})
        page.on("response", capture)
        response = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)
        print("PAGE", response.status if response else None, page.title(), page.url, flush=True)
        print("SELECTS", json.dumps(page.locator("select").evaluate_all(
            "(els) => els.map(e => ({name:e.name,id:e.id,html:e.outerHTML.slice(0,5000)}))"
        )), flush=True)
        print("INPUTS", json.dumps(page.locator("input").evaluate_all(
            "(els) => els.map(e => ({name:e.name,id:e.id,type:e.type,value:e.value,html:e.outerHTML.slice(0,1000)}))"
        )), flush=True)
        print("SCRIPTS", json.dumps(page.locator("script[src]").evaluate_all(
            "(els) => els.map(e => e.src)"
        )), flush=True)
        print("JSON_RESPONSES", json.dumps(responses, ensure_ascii=False)[:18000], flush=True)
        print("MOVIE_LINKS", json.dumps(page.locator("a").evaluate_all(
            "(els) => els.filter(e => /movie|film/i.test(e.href+' '+e.innerText)).slice(0,50).map(e => ({text:e.innerText.trim().slice(0,100),href:e.href,html:e.parentElement?.outerHTML.slice(0,1200)}))"
        ), ensure_ascii=False)[:16000], flush=True)
        print("BODY_START", page.locator("body").inner_text()[:2500].replace("\\n"," | "), flush=True)
        browser.close()

if __name__ == "__main__":
    main()
