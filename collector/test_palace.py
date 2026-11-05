from playwright.sync_api import sync_playwright
import json
import re
import requests

BASE = "https://www.palacecinemas.com.au"
PAGES = [
    ("NOW_SHOWING", BASE + "/movies"),
    ("COMING_SOON", BASE + "/movies/coming-soon"),
]

def inspect_page(page, label, url):
    responses = []
    def capture(response):
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                body = response.text()[:5000]
            except Exception as exc:
                body = "BODY_ERROR " + str(exc)
            responses.append({"status": response.status, "type": content_type, "url": response.url, "body": body})

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

    # Palace renders duplicate responsive location selectors, including hidden copies.
    # Set every matching native select through the DOM so visibility does not block the test.
    visible_location = page.locator('select[aria-label="Choose your location"]:visible')
    print("VISIBLE_LOCATION_SELECTS", visible_location.count(), flush=True)
    if visible_location.count():
        visible_location.first.select_option("canberra")
    page.wait_for_timeout(3000)

    # Use Playwright's real select interaction so the page framework receives the change.
    cinema_select = page.locator("select:visible").filter(has=page.locator('option[value="171"]'))
    print("VISIBLE_CINEMA_SELECTS", cinema_select.count(), flush=True)
    if cinema_select.count():
        cinema_select.first.select_option("171")
        page.wait_for_timeout(4000)

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


def inspect_la_la_land_detail(page):
    url = BASE + "/movies/la-la-land-10th-anniversary"
    responses = []
    def capture(response):
        if "json" in response.headers.get("content-type", ""):
            try:
                body = response.text()[:10000]
            except Exception as exc:
                body = "BODY_ERROR " + str(exc)
            responses.append({"status": response.status, "url": response.url, "body": body})
    page.on("response", capture)
    response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    print("\\n===== LA LA LAND DETAIL PAGE =====", flush=True)
    print("DETAIL_PAGE", response.status if response else None, page.url, flush=True)
    print("DETAIL_TEXT", page.locator("body").inner_text()[:12000], flush=True)
    print("DETAIL_JSON_NETWORK", json.dumps(responses, ensure_ascii=False)[:30000], flush=True)
    page.remove_listener("response", capture)

def direct_api_probe():
    api = "https://prod-api.palace-cinemas.workers.dev"
    print("\\n===== DIRECT PALACE API PROBE =====", flush=True)
    for path in ["/cinemas", "/movies?locality=canberra", "/movies/now/trending?locality=canberra"]:
        try:
            response = requests.get(api + path, headers={"User-Agent": "Mozilla/5.0", "Origin": BASE}, timeout=30)
            print("API", path, "STATUS", response.status_code, flush=True)
            data = response.json()
            if path == "/cinemas":
                print("CANBERRA_CINEMAS", json.dumps(
                    [x for x in data if x.get("locality") == "canberra" or str(x.get("cinemaId")) == "171"],
                    ensure_ascii=False
                ), flush=True)
            elif isinstance(data, list):
                matches = [x for x in data if "la la land" in json.dumps(x, ensure_ascii=False).lower()]
                print("API_ITEMS", path, len(data), json.dumps(data[:40], ensure_ascii=False)[:12000], flush=True)
                print("LA_LA_LAND_MATCHES", path, json.dumps(matches, ensure_ascii=False)[:12000], flush=True)
            else:
                encoded = json.dumps(data, ensure_ascii=False)
                print("API_DATA", path, encoded[:12000], flush=True)
                if "la la land" in encoded.lower():
                    print("LA_LA_LAND_MATCHES", path, encoded[:12000], flush=True)
        except Exception as exc:
            print("API_ERROR", path, repr(exc), flush=True)



def inspect_palace_candidate_pages(page):
    api = "https://prod-api.palace-cinemas.workers.dev"
    response = requests.get(api + "/movies?locality=canberra",
                            headers={"User-Agent":"Mozilla/5.0","Origin":BASE}, timeout=30)
    response.raise_for_status()
    movies = response.json()
    candidates = [m for m in movies
                  if "171" in [str(x) for x in (m.get("showingAtCinemaIds") or [])]
                  and (m.get("isRepertory") or "retrospective" in str(m.get("genreDisplayNames","")).lower())]
    print("\\n===== PALACE CANBERRA RETRO CANDIDATES =====", flush=True)
    print("CANDIDATE_COUNT", len(candidates), flush=True)
    for movie in candidates:
        print("CANDIDATE", json.dumps({k:movie.get(k) for k in
              ["movieId","title","slug","releaseDateUtc","publishedScreeningDate","isRepertory","genreDisplayNames"]},
              ensure_ascii=False), flush=True)

    for movie in candidates[:6]:
        slug = movie.get("slug")
        if not slug:
            continue
        url = BASE + "/movies/" + slug
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(1200)
        body = page.locator("body").inner_text()
        print("\\nCANDIDATE_DETAIL", movie.get("title"), response.status if response else None, flush=True)
        print("CANDIDATE_DETAIL_TEXT", body[:4500].replace("\\n"," | "), flush=True)
        matches = page.locator("body").evaluate("""el => {
          const out=[];
          const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
          while(walker.nextNode()){
            const t=walker.currentNode.textContent.trim();
            if(/\\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\\b|\\b\\d{1,2}:\\d{2}\\s*(?:am|pm)\\b/i.test(t)){
              let n=walker.currentNode.parentElement;
              for(let i=0;i<4 && n && n.parentElement;i++) n=n.parentElement;
              out.push({text:t,html:n?n.outerHTML.slice(0,3500):""});
            }
          }
          return out.slice(0,18);
        }""")
        print("CANDIDATE_SESSION_NODES", json.dumps(matches, ensure_ascii=False)[:14000], flush=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for label, url in PAGES:
            inspect_page(page, label, url)
        inspect_la_la_land_detail(page)
        inspect_palace_candidate_pages(page)
        browser.close()
    direct_api_probe()

if __name__ == "__main__":
    main()
