"""Diagnostic-only tests for Dendy's Love Actually screening dates."""
import json
import requests
from playwright.sync_api import sync_playwright

URL = "https://canberra.dendy.com.au/movie/love-actually---20th-anniversary/"
API = "https://canberra.dendy.com.au/graphql"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://canberra.dendy.com.au",
    "Referer": URL,
}
SLUG = "love-actually---20th-anniversary"
EXT = {"clientLibrary": {"name": "@apollo/client", "version": "4.0.9"}}

def report(label, response):
    try:
        data = response.json()
    except Exception:
        data = response.text[:1000]
    print(label, "HTTP", response.status_code, json.dumps(data, ensure_ascii=False)[:2500], flush=True)

def main():
    print("=== TEST 1: HTML/page-data inspection ===", flush=True)
    try:
        r = requests.get(URL, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=25)
        print("HTML HTTP", r.status_code, "bytes", len(r.text), flush=True)
        for token in ["datesWithPublicShowing", "2026-12-10", "2026-12-12", "2026-12-14", "dateOfFirstShowing"]:
            print("HTML TOKEN", token, token in r.text, flush=True)
        print("HTML CONTEXT", r.text[max(0, r.text.find("Love Actually")-250):r.text.find("Love Actually")+700].replace("\\n", " ")[:1000], flush=True)
    except Exception as e:
        print("HTML ERROR", repr(e), flush=True)

    print("=== TEST 2: GraphQL request variations ===", flush=True)
    queries = [
        ("siteIds-empty", "query ($urlSlug: String!, $siteIds: [ID]) { findMovieBySlug(urlSlug: $urlSlug, siteIds: $siteIds) { id name releaseDate } }", {"urlSlug": SLUG, "siteIds": []}),
        ("siteIds-34", "query ($urlSlug: String!, $siteIds: [ID]) { findMovieBySlug(urlSlug: $urlSlug, siteIds: $siteIds) { id name releaseDate } }", {"urlSlug": SLUG, "siteIds": ["34"]}),
        ("no-siteIds", "query ($urlSlug: String!) { findMovieBySlug(urlSlug: $urlSlug) { id name releaseDate } }", {"urlSlug": SLUG}),
    ]
    for label, query, variables in queries:
        payload = {"query": query, "variables": variables, "extensions": EXT}
        try:
            report("GQL " + label, requests.post(API, headers=HEADERS, json=payload, timeout=25))
        except Exception as e:
            print("GQL ERROR", label, repr(e), flush=True)

    print("=== TEST 3: Real browser network capture ===", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = context.new_page()
        captured = []
        def on_response(response):
            if "graphql" not in response.url.lower():
                return
            try:
                request = response.request
                body = request.post_data or ""
                text = response.text()
                item = {"status": response.status, "url": response.url,
                        "method": request.method, "post_data": body[:5000],
                        "response": text[:5000]}
                captured.append(item)
                print("BROWSER GRAPHQL", json.dumps(item, ensure_ascii=False)[:10000], flush=True)
            except Exception as e:
                print("BROWSER CAPTURE ERROR", repr(e), flush=True)
        page.on("response", on_response)
        try:
            response = page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            print("BROWSER PAGE", response.status if response else None, page.url, flush=True)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(5000)
            print("BROWSER TITLE", page.title(), flush=True)
            print("BROWSER GRAPHQL COUNT", len(captured), flush=True)
            print("BROWSER BODY HAS DATES", {d: d in page.content() for d in ["2026-12-10", "2026-12-12", "2026-12-14"]}, flush=True)
        except Exception as e:
            print("BROWSER ERROR", repr(e), flush=True)
        finally:
            browser.close()

if __name__ == "__main__":
    main()
