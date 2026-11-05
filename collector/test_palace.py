from playwright.sync_api import sync_playwright
import json
import re

BASE = "https://www.palacecinemas.com.au"
PAGES = [
    ("NOW_SHOWING", BASE + "/movies"),
    ("COMING_SOON", BASE + "/movies/coming-soon"),
]

def inspect_page(page, label, url):
    responses = []
    def capture(response):
        content_type = response.headers.get("content-type", "")
        if "json" in content_type or "javascript" in content_type:
            responses.append({"status": response.status, "type": content_type, "url": response.url})

    page.on("response", capture)
    response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(2500)

    print("\\n===== " + label + " BEFORE FILTER =====", flush=True)
    print("PAGE", response.status if response else None, page.title(), page.url, flush=True)
    selects = page.locator("select")
    print("SELECTS", json.dumps(selects.evaluate_all(
        "(els) => els.map(e => ({aria:e.getAttribute('aria-label'),value:e.value,options:Array.from(e.options).map(o=>({text:o.text,value:o.value}))}))"
    )), flush=True)

    # Explicitly select Canberra in every cinema/location selector.
    for i in range(selects.count()):
        selector = selects.nth(i)
        options = selector.locator("option").evaluate_all(
            "(els) => els.map(o => ({text:o.text.trim(),value:o.value}))"
        )
        canberra = next((o for o in options if o["value"].lower() == "canberra" or o["text"].lower() == "canberra"), None)
        if canberra:
            selector.select_option(canberra["value"])
            page.wait_for_timeout(1800)

    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(2000)

    print("PAGE_AFTER_CANBERRA", page.url, flush=True)
    print("SELECTS_AFTER", json.dumps(page.locator("select").evaluate_all(
        "(els) => els.map(e => ({aria:e.getAttribute('aria-label'),value:e.value}))"
    )), flush=True)

    movies = page.locator('a[href^="/movies/"]').evaluate_all("""(els) => {
      const seen = new Set();
      return els.map(a => {
        const href = a.getAttribute('href') || '';
        if (!href || href === '/movies' || href === '/movies/coming-soon' || seen.has(href)) return null;
        seen.add(href);
        let node = a;
        for (let i=0; i<5 && node.parentElement; i++) {
          node = node.parentElement;
          const txt = (node.innerText || '').trim();
          if (txt.length > 40 && (txt.includes('Book Tickets') || txt.includes('Notify Me') || txt.includes('Today') || txt.includes('Release Date'))) break;
        }
        return {title:a.innerText.trim(),href:location.origin+href,cardText:(node.innerText||'').trim().slice(0,900),cardHtml:node.outerHTML.slice(0,2500)};
      }).filter(Boolean);
    }""")
    print("MOVIE_CARDS", json.dumps(movies, ensure_ascii=False)[:30000], flush=True)
    print("NETWORK_DATA", json.dumps(responses, ensure_ascii=False)[:16000], flush=True)
    print("BODY_TEXT", page.locator("body").inner_text()[:9000], flush=True)
    page.remove_listener("response", capture)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for label, url in PAGES:
            inspect_page(page, label, url)
        browser.close()

if __name__ == "__main__":
    main()
