"""Collect candidate older-film sessions from Canberra cinema programme pages.

The three cinema JSON-LD collectors are discovery-only. ANU Film Group has a
site-specific collector because its programme links to individual film pages
that publish the screening date, time, release year, poster and synopsis.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sessions.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CanberraRetroCinema/0.1)"}
TIMEOUT = 25
SOURCES = [
    ("Dendy Canberra", "https://canberra.dendy.com.au/now-showing/"),
    ("NFSA Arc Cinema", "https://www.nfsa.gov.au/whats-on/arc-cinema"),
]
ANU_PROGRAMME = "https://www.anufg.org.au/programme"


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def walk_events(obj):
    if isinstance(obj, dict):
        typ = obj.get("@type", [])
        typ = [typ] if isinstance(typ, str) else typ
        if any("Event" in item for item in typ):
            yield obj
        for value in obj.values():
            yield from walk_events(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk_events(value)


def collect_jsonld(cinema, url, today_year):
    soup = get_soup(url)
    found = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for event in walk_events(data):
            title = str(event.get("name", "")).strip()
            start = event.get("startDate")
            if not title or not start:
                continue
            about = event.get("about", {})
            year = about.get("datePublished", "") if isinstance(about, dict) else ""
            if not year and isinstance(event.get("workPerformed"), dict):
                year = event["workPerformed"].get("datePublished", "")
            try:
                year = int(str(year)[:4])
            except (ValueError, TypeError):
                continue
            if today_year - year < 10:
                continue
            found.append({
                "film_id": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
                "title": title,
                "year": year,
                "cinema": cinema,
                "start": start,
                "description": "" if cinema == "NFSA Arc Cinema" else event.get("description", ""),
                "ticket_url": event.get("url", ""),
                "poster_url": "",
            })
    return found



def collect_dendy(url, today_year):
    """Collect Dendy Canberra screenings from both programme pages via its GraphQL API."""
    coming_soon_url = "https://canberra.dendy.com.au/coming-soon/"
    now = datetime.now(timezone.utc)

    def walk_nodes(obj):
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from walk_nodes(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from walk_nodes(value)

    def jsonld_nodes(soup):
        nodes = []
        for tag in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or tag.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            nodes.extend(walk_nodes(data))
        return nodes

    def node_types(node):
        value = node.get("@type", [])
        return [value] if isinstance(value, str) else value

    def year_from_text(movie):
        description = BeautifulSoup(str(movie.get("description", "")), "html.parser").get_text(" ", strip=True)
        match = re.search(r"\((19\d{2}|20\d{2})\)", description)
        if not match:
            match = re.search(
                r"(?:first released|originally released|released|premiered)\s+(?:in\s+)?(19\d{2}|20\d{2})",
                description,
                re.IGNORECASE,
            )
        if match:
            return int(match.group(1)), description
        published = movie.get("datePublished") or movie.get("releaseDate") or ""
        match = re.search(r"(19\d{2}|20\d{2})", str(published))
        return (int(match.group(1)) if match else None), description

    now_soup = get_soup(url)
    movie_pages = {}

    def add_movie_links(soup, coming_soon):
        for anchor in soup.find_all("a", href=True):
            movie_url = urljoin(url, anchor["href"]).split("#", 1)[0]
            if "/movie/" not in movie_url or "canberra.dendy.com.au" not in movie_url:
                continue
            movie_url = movie_url.rstrip("/") + "/"
            if movie_url not in movie_pages:
                movie_pages[movie_url] = {
                    "label": anchor.get_text(" ", strip=True),
                    "movie": {},
                    "coming_soon": coming_soon,
                }
            elif coming_soon:
                movie_pages[movie_url]["coming_soon"] = True

    for movie in jsonld_nodes(now_soup):
        if "Movie" not in node_types(movie):
            continue
        movie_url = movie.get("sameAs") or str(movie.get("@id", "")).split("#", 1)[0]
        if movie_url:
            movie_pages[movie_url.rstrip("/") + "/"] = {
                "label": movie.get("name", ""),
                "movie": movie,
                "coming_soon": False,
            }
    add_movie_links(now_soup, False)

    try:
        coming_soup = get_soup(coming_soon_url)
        add_movie_links(coming_soup, True)
    except requests.RequestException as exc:
        print("WARN Dendy Coming Soon page", str(exc)[:180])

    found = []
    graphql_url = "https://canberra.dendy.com.au/graphql"
    gql_headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "Origin": "https://canberra.dendy.com.au",
    }
    movie_query = """
        query ($urlSlug: String!, $siteIds: [ID]) {
          findMovieBySlug(urlSlug: $urlSlug, siteIds: $siteIds) {
            id name urlSlug releaseDate synopsis posterImage
            datesWithShowing datesWithPublicShowing
          }
        }
    """
    # The film page's follow-up query resolves the Movie by ID and exposes
    # datesWithPublicShowing. This is the date-only data the simplified app needs.
    movie_dates_query = """
        query ($id: ID!) {
          movie(id: $id) {
            id
            name
            releaseDate
            datesWithShowing
            datesWithPublicShowing
          }
        }
    """

    for movie_url, page_info in movie_pages.items():
        try:
            detail_soup = get_soup(movie_url)
        except requests.RequestException as exc:
            print("WARN Dendy film page", movie_url, str(exc)[:140])
            continue

        nodes = jsonld_nodes(detail_soup)
        movie = next((node for node in nodes if "Movie" in node_types(node)), page_info["movie"])
        title = str(movie.get("name") or page_info["label"]).strip()
        if not title:
            title = movie_url.rstrip("/").split("/")[-1].replace("-", " ").title()

        description = BeautifulSoup(
            str(movie.get("description") or ""), "html.parser"
        ).get_text(" ", strip=True)
        events = [node for node in nodes if "ScreeningEvent" in node_types(node)]
        graphql_movie = None

        # Ask Dendy's public GraphQL API for the movie record and its
        # public screening dates. This is the minimal data needed by the app.
        graphql_movie = None
        public_dates = []
        try:
            slug = movie_url.rstrip("/").split("/")[-1]
            lookup_payload = {
                "query": """
                    query ($urlSlug: String!, $siteIds: [ID]) {
                      findMovieBySlug(urlSlug: $urlSlug, siteIds: $siteIds) {
                        id name releaseDate
                      }
                    }
                """,
                "variables": {"urlSlug": slug, "siteIds": ["34"]},
                "extensions": {
                    "clientLibrary": {
                        "name": "@apollo/client",
                        "version": "4.0.9",
                    }
                },
            }
            lookup_response = requests.post(
                graphql_url,
                headers={**gql_headers, "Referer": movie_url},
                json=lookup_payload,
                timeout=TIMEOUT,
            )
            lookup_response.raise_for_status()
            lookup_json = lookup_response.json()
            graphql_movie = lookup_json.get("data", {}).get("findMovieBySlug")
            if lookup_json.get("errors"):
                raise ValueError("findMovieBySlug: " + str(lookup_json["errors"])[:500])
            if not graphql_movie:
                print("Dendy movie lookup returned no movie", title, str(lookup_json)[:500])
                continue

            movie_id = str(graphql_movie.get("id") or "")
            dates_payload = {
                "query": """
                    query ($id: ID!) {
                      movie(id: $id) {
                        id
                        name
                        releaseDate
                        datesWithPublicShowing
                      }
                    }
                """,
                "variables": {"id": movie_id},
                "extensions": {
                    "clientLibrary": {
                        "name": "@apollo/client",
                        "version": "4.0.9",
                    }
                },
            }
            dates_response = requests.post(
                graphql_url,
                headers={**gql_headers, "Referer": movie_url},
                json=dates_payload,
                timeout=TIMEOUT,
            )
            dates_response.raise_for_status()
            dates_json = dates_response.json()
            if dates_json.get("errors"):
                raise ValueError("movie(id) query: " + str(dates_json["errors"])[:700])
            movie_record = dates_json.get("data", {}).get("movie") or {}
            public_dates = movie_record.get("datesWithPublicShowing") or []
            release_date = movie_record.get("releaseDate") or graphql_movie.get("releaseDate")
            if release_date:
                graphql_movie["releaseDate"] = release_date

            if "love-actually" in slug:
                print("DENDY LOVE ACTUALLY DIRECT TEST",
                      "lookup_status", lookup_response.status_code,
                      "movie_id", movie_id,
                      "dates_status", dates_response.status_code,
                      "release", release_date,
                      "public_dates", public_dates,
                      "raw", json.dumps(dates_json, ensure_ascii=False)[:1800])
            else:
                print("Dendy public dates", title, public_dates)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            print("WARN Dendy public dates", title, str(exc)[:700])
            continue

        year, year_description = year_from_text(movie)
        if year is None and graphql_movie:
            year, _ = year_from_text({
                "releaseDate": graphql_movie.get("releaseDate"),
                "description": graphql_movie.get("synopsis") or "",
            })
        if year is None or today_year - year < 10:
            continue

        for screening_date in public_dates:
            try:
                parsed_date = datetime.strptime(str(screening_date), "%Y-%m-%d").date()
            except ValueError:
                continue
            if parsed_date < now.date():
                continue
            found.append({
                "film_id": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
                "title": title,
                "year": year,
                "cinema": "Dendy Canberra",
                "start": parsed_date.isoformat() + "T00:00:00",
                "description": "",
                "ticket_url": "",
                "poster_url": "",
            })



    print("Dendy film pages discovered:", len(movie_pages))
    return found


def collect_palace(today):
    """Collect Palace Electric Canberra repertory screenings from Palace's API and film pages."""
    api = "https://prod-api.palace-cinemas.workers.dev"
    cinema_id = "171"
    cinema_name = "Palace Electric Cinema Canberra"
    api_headers = {**HEADERS, "Origin": "https://www.palacecinemas.com.au"}
    response = requests.get(api + "/movies?locality=canberra", headers=api_headers, timeout=TIMEOUT)
    response.raise_for_status()
    catalogue = response.json()
    candidates = [
        movie for movie in catalogue
        if cinema_id in [str(value) for value in (movie.get("showingAtCinemaIds") or [])]
        and movie.get("isRepertory") is True and movie.get("slug")
    ]

    # Palace's public catalogue does not consistently expose original release years.
    known_years = {
        "children of men": 2006, "boogie nights": 1997,
        "strangers on a train": 1951, "ferngully: the last rainforest": 1992,
        "black christmas": 1974, "it's a wonderful life": 1946,
        "the nightmare before christmas": 1993,
    }

    def release_year(movie):
        title = str(movie.get("title") or "").strip()
        match = re.search(r"\b(19\d{2}|20\d{2})\b", title)
        if match:
            return int(match.group(1))
        anniversary = re.search(r"\b(\d+)(?:st|nd|rd|th)\s+anniversary\b", title, re.I)
        if anniversary:
            return today.year - int(anniversary.group(1))
        for key in ("originalReleaseYear", "releaseYear", "year"):
            value = movie.get(key)
            if value:
                try:
                    return int(str(value)[:4])
                except (ValueError, TypeError):
                    pass
        return known_years.get(title.lower())

    def session_date(label):
        match = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,})", label, re.I)
        if not match:
            return None
        day, month_text = int(match.group(1)), match.group(2)
        for fmt in ("%d %b %Y", "%d %B %Y"):
            for year in (today.year, today.year + 1):
                try:
                    parsed = datetime.strptime(f"{day} {month_text} {year}", fmt).date()
                except ValueError:
                    continue
                if parsed >= today:
                    return parsed
        return None

    found, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # The successful Palace diagnostic first selected Canberra and Palace
        # Electric on the movie listing page, then opened film detail pages in
        # that same browser session. This selection is stored by the site and
        # controls which cinema's sessions appear on each detail page.
        page.goto("https://www.palacecinemas.com.au/movies",
                  wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        location_select = page.locator(
            'select[aria-label="Choose your location"]:visible'
        )
        if not location_select.count():
            raise RuntimeError("Palace location selector was not available")
        location_select.first.select_option("canberra")
        page.wait_for_timeout(2500)
        cinema_select = page.locator("select:visible").filter(
            has=page.locator('option[value="171"]')
        )
        if not cinema_select.count():
            raise RuntimeError("Palace Electric cinema selector was not available")
        cinema_select.first.select_option("171")
        page.wait_for_timeout(3500)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        print("Palace selected location/cinema:",
              page.locator("select").evaluate_all(
                  "els => els.map(e => ({label:e.getAttribute('aria-label'),value:e.value}))"
              ))
        for movie in candidates:
            title = str(movie.get("title") or "").strip()
            year = release_year(movie)
            if year is not None and today.year - year < 10:
                continue
            url = "https://www.palacecinemas.com.au/movies/" + str(movie["slug"]).strip("/")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(700)
                # Reproduce the proven diagnostic: read rendered page text, then
                # isolate the block immediately under the Palace Electric Canberra
                # heading. The cinema's session dates are rendered as text (not
                # reliably as paragraph elements), e.g. "Monday23rd Nov".
                body_text = page.locator("body").inner_text()
                heading = "PALACE ELECTRIC CINEMA CANBERRA"
                month_names = (
                    "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
                    "May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
                    "Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
                )
                date_pattern = re.compile(
                    rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s*({month_names})\b",
                    re.IGNORECASE
                )
                # The cinema name appears several times on the page: in the
                # cinema selector, as the actual sessions heading, and in the
                # participating-cinemas/footer lists. Do NOT use rfind().
                # Select the occurrence immediately followed by a real date.
                heading_positions = [
                    match.start()
                    for match in re.finditer(re.escape(heading), body_text, re.IGNORECASE)
                ]
                section_text = ""
                for heading_pos in heading_positions:
                    after_heading = body_text[heading_pos + len(heading):]
                    if date_pattern.search(after_heading[:180]):
                        section_text = after_heading
                        break
                sessions = []
                if section_text:
                    stop = re.search(
                        r"\n\s*(?:SYNOPSIS|ABOUT THE FILM|MOVIE INFORMATION)\b",
                        section_text, re.IGNORECASE
                    )
                    if stop:
                        section_text = section_text[:stop.start()]
                    section_text = section_text[:600]
                    sessions = [
                        match.group(0).strip()
                        for match in date_pattern.finditer(section_text)
                    ]
                film_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                for label in sessions:
                    parsed = session_date(label)
                    if parsed is None:
                        continue
                    found.append({
                        "film_id": film_id, "title": title, "year": year,
                        "cinema": cinema_name, "start": parsed.isoformat() + "T00:00:00",
                        "description": "", "ticket_url": "", "poster_url": "",
                    })
                print("Palace Electric sessions", title, "year", year, "dates", sessions)
            except Exception as exc:
                errors.append(f"{title}: {str(exc)[:180]}")
                print("WARN Palace film page", title, str(exc)[:300])
        browser.close()
    print("Palace Canberra repertory candidates:", len(candidates))
    print("Palace Electric sessions collected:", len(found))
    if errors:
        print("Palace page errors:", "; ".join(errors[:5]))
    return found




def collect_limelight(today):
    """Collect older-film sessions at Limelight Tuggeranong from both programme pages."""
    base = "https://limelightcinemas.com.au"
    cinema_id = "0000000002"
    programme_urls = [
        base + "/movies/now-showing/",
        base + "/movies/coming-soon/",
    ]
    # The Polar Express is the agreed diagnostic case. Its cinema re-release
    # date is not its original release year.
    known_years = {
        "the polar express": 2004,
        "the nightmare before christmas": 1993,
        "elf": 2003,
        "home alone": 1990,
        "star wars (1977) 50th anniversary": 1977,
    }

    def parse_year(page, fallback_title=""):
        # The Limelight film heading is rendered as body text, not an h1.
        # Check both the page heading and the listing-card title against known
        # original years before considering the current cinema release date.
        title_text = page.locator("h1").first.inner_text().strip() if page.locator("h1").count() else ""
        for candidate in (title_text, fallback_title):
            if candidate.strip().casefold() in known_years:
                return known_years[candidate.strip().casefold()]
        # Prefer explicit original-year metadata over the current re-release date.
        for script in page.locator('script[type="application/ld+json"]').all():
            try:
                data = json.loads(script.inner_text())
            except (ValueError, TypeError):
                continue
            stack = [data]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    for key in ("datePublished", "copyrightYear", "releaseYear", "year"):
                        value = node.get(key)
                        if value:
                            match = re.search(r"\b(19\d{2}|20\d{2})\b", str(value))
                            if match:
                                return int(match.group(1))
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
        body = page.locator("body").inner_text()
        for pattern in (
            r"(?:original release|originally released|film year|year)\s*[:\-]?\s*(19\d{2}|20\d{2})",
            r"\((19\d{2}|20\d{2})\)",
        ):
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    def parse_dates(section):
        month_names = (
            "Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            "Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
        )
        pattern = re.compile(
            rf"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s+(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names})(?:\s+(20\d{{2}}))?\b",
            re.IGNORECASE,
        )
        dates = []
        for match in pattern.finditer(section):
            day, month, explicit_year = match.groups()
            years = [int(explicit_year)] if explicit_year else [today.year, today.year + 1]
            parsed = None
            for year in years:
                for fmt in ("%d %b %Y", "%d %B %Y"):
                    try:
                        parsed = datetime.strptime(f"{day} {month} {year}", fmt).date()
                        break
                    except ValueError:
                        pass
                if parsed:
                    break
            if parsed and parsed >= today and parsed not in dates:
                dates.append(parsed)
        return dates

    found, errors, candidates = [], [], {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        # Set the venue through the site's own selector first, then preserve the
        # resulting cookie/session while visiting both programme pages.
        page.goto(base + "/film/the-polar-express-2/", wait_until="domcontentloaded", timeout=60000)
        try:
            page.get_by_role("link", name="Tuggeranong", exact=True).last.click(timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            page.goto(base + "/film/the-polar-express-2/?setCinema=" + cinema_id,
                      wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
        for programme_url in programme_urls:
            page.goto(programme_url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            for anchor in page.locator('a[href*="/film/"]').all():
                try:
                    href = anchor.get_attribute("href") or ""
                    title = anchor.inner_text().strip()
                    if not href or not title:
                        continue
                    from urllib.parse import urljoin
                    url = urljoin(base, href).split("#", 1)[0]
                    if "/film/" in url:
                        candidates.setdefault(url, title)
                except Exception:
                    continue

        # Revisit the agreed sample explicitly, even if it is absent from the
        # programme-page links due to the site's client-side listing behaviour.
        candidates.setdefault(base + "/film/the-polar-express-2/", "The Polar Express")

        for film_url, listing_title in candidates.items():
            try:
                detail_url = film_url + ("&" if "?" in film_url else "?") + "setCinema=" + cinema_id
                page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(350)
                body = page.locator("body").inner_text()
                heading = re.search(r"Tuggeranong\s+Session\s+Times", body, re.IGNORECASE)
                if not heading:
                    raise RuntimeError("Tuggeranong session section not present; refusing to read another venue's dates")
                section = body[heading.end():]
                stop = re.search(r"\bOther\s+upcoming\s+movies\b", section, re.IGNORECASE)
                if stop:
                    section = section[:stop.start()]
                title = page.locator("h1").first.inner_text().strip() if page.locator("h1").count() else listing_title
                year = parse_year(page, title)
                if year is None or today.year - year < 10:
                    continue
                dates = parse_dates(section)
                for session_day in dates:
                    found.append({
                        "film_id": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
                        "title": title,
                        "year": year,
                        "cinema": "Limelight Tuggeranong",
                        "start": session_day.isoformat() + "T00:00:00",
                        "description": "",
                        "ticket_url": film_url,
                        "poster_url": "",
                    })
                if title.casefold() == "the polar express":
                    print("LIMELIGHT POLAR EXPRESS TEST",
                          "year", year,
                          "dates", [d.isoformat() for d in dates],
                          "venue_verified", True,
                          "url", detail_url)
                if dates:
                    print("Limelight Tuggeranong", title, year, [d.isoformat() for d in dates])
            except Exception as exc:
                errors.append(f"{listing_title}: {str(exc)[:180]}")
                print("WARN Limelight film page", listing_title, str(exc)[:260])
        browser.close()

    print("Limelight programme film pages discovered:", len(candidates))
    print("Limelight Tuggeranong older-film sessions:", len(found))
    if errors:
        print("Limelight page errors:", "; ".join(errors[:8]))
    return found



def diagnose_hoyts():
    """Temporary browser diagnostic for HOYTS cinema selection."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.hoyts.com.au/session-times", wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(1800)
        print("HOYTS INITIAL", page.locator("body").inner_text()[:4500])
        controls = page.locator("button,a,[role=button],input,label").evaluate_all(
            "els => els.map(e => ({tag:e.tagName,text:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,100),role:e.getAttribute('role'),type:e.getAttribute('type'),value:e.value||'',href:e.getAttribute('href'),aria:e.getAttribute('aria-label'),checked:e.checked})).filter(x=>x.text||x.value||x.href).slice(0,120)"
        )
        print("HOYTS CONTROLS", json.dumps(controls, ensure_ascii=False)[:14000])
        try:
            page.get_by_text("Add cinemas", exact=True).first.click(timeout=10000)
            page.wait_for_timeout(900)
            print("HOYTS MODAL BODY", page.locator("body").inner_text()[:7000])
            modal_controls = page.locator("button,a,[role=button],input,label").evaluate_all(
                "els => els.map(e => ({tag:e.tagName,text:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,100),role:e.getAttribute('role'),type:e.getAttribute('type'),value:e.value||'',href:e.getAttribute('href'),aria:e.getAttribute('aria-label'),checked:e.checked})).filter(x=>x.text||x.value).slice(0,160)"
            )
            print("HOYTS MODAL CONTROLS", json.dumps(modal_controls, ensure_ascii=False)[:16000])
        except Exception as exc:
            print("HOYTS ADD CINEMAS ERROR", repr(exc))
        browser.close()



def collect_arc(today):
    """Probe NFSA's public event pages directly as an alternative to the blocked ticketing host."""
    urls = [
        "https://www.nfsa.gov.au/whats-on/arc-cinema",
        "https://www.nfsa.gov.au/whats-on/science-art-film",
        "https://www.nfsa.gov.au/whats-on/science-art-film-minority-report-4653",
        "https://www.nfsa.gov.au/whats-on",
    ]
    results = []
    for url in urls:
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            soup = BeautifulSoup(response.text, "html.parser")
            text_body = soup.get_text(" ", strip=True)
            results.append({
                "url": url, "status": response.status_code,
                "final_url": response.url, "title": soup.title.get_text(" ", strip=True) if soup.title else "",
                "minority_report_present": "Minority Report" in text_body,
                "body_excerpt": text_body[:1600],
                "jsonld": [tag.get_text(" ", strip=True)[:3000] for tag in soup.select('script[type="application/ld+json"]')[:3]],
                "links": [{"text": a.get_text(" ", strip=True)[:100], "href": a.get("href")}
                          for a in soup.select('a[href*="whats-on"]')[:40]],
            })
        except Exception as exc:
            results.append({"url": url, "error": str(exc)[:300]})
    print("NFSA PUBLIC PAGE DIAGNOSTIC", json.dumps(results, ensure_ascii=False)[:18000])
    return []

def collect_anu(today):
    """Read ANU programme links and parse every film on each screening page."""
    programme = get_soup(ANU_PROGRAMME)
    pages = []
    for anchor in programme.select('a[href*="/filminfo/"]'):
        href = anchor.get("href", "")
        if not re.search(r"/filminfo/\d{8}\.html", href):
            continue
        url = urljoin(ANU_PROGRAMME, href).split("#", 1)[0]
        if url not in pages:
            pages.append(url)

    found = []
    checked = 0
    errors = []
    for page_url in pages:
        try:
            soup = get_soup(page_url)
            headings = soup.find_all("h3")
            for heading in headings:
                title = heading.get_text(" ", strip=True)
                if not title:
                    continue

                # Gather only this film's section, stopping at the next film heading.
                section_parts = []
                section_paragraphs = []
                for node in heading.next_elements:
                    if getattr(node, "name", None) == "h3":
                        break
                    if isinstance(node, str):
                        value = node.strip()
                        if value:
                            section_parts.append(value)
                    elif getattr(node, "name", None) == "p":
                        value = node.get_text(" ", strip=True)
                        if len(value) > 45:
                            section_paragraphs.append(value)
                section_text = " ".join(section_parts)

                year_match = re.search(r"\bYear\s+(19\d{2}|20\d{2})\b", section_text)
                date_match = re.search(
                    r"(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]+),?\s+(\d{4})",
                    section_text,
                    re.IGNORECASE,
                )
                time_match = re.search(r"\b(\d{1,2}:\d{2}\s*[AP]M)\b", section_text, re.IGNORECASE)
                if not (year_match and date_match and time_match):
                    continue

                release_year = int(year_match.group(1))
                day, month_name, year = date_match.groups()
                try:
                    session_date = datetime.strptime(
                        f"{day} {month_name} {year}", "%d %B %Y"
                    ).date()
                except ValueError:
                    errors.append(f"Could not parse date for {title} on {page_url}")
                    continue

                checked += 1
                if session_date < today or session_date.year - release_year < 10:
                    continue

                poster = ""
                poster_node = heading.find_previous(
                    "img", alt=re.compile(r"Poster for ", re.IGNORECASE)
                )
                if poster_node and poster_node.get("src"):
                    poster = urljoin(page_url, poster_node["src"])

                start_time = time_match.group(1).upper().replace(" ", "")
                start = session_date.isoformat() + "T" + datetime.strptime(
                    start_time, "%I:%M%p"
                ).strftime("%H:%M:%S")
                film_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
                found.append({
                    "film_id": film_id,
                    "title": title,
                    "year": release_year,
                    "cinema": "ANU Film Group",
                    "start": start,
                    "description": " ".join(section_paragraphs)[:900],
                    "ticket_url": page_url,
                    "poster_url": poster,
                })
        except requests.RequestException as exc:
            errors.append(f"{page_url}: {str(exc)[:180]}")

    return found, {
        "cinema": "ANU Film Group",
        "status": "ok" if not errors else ("partial" if checked else "fetch error"),
        "count": len(found),
        "programme_url": ANU_PROGRAMME,
        "film_pages_checked": checked,
        "detail": "; ".join(errors[:3]) if errors else "",
    }


def main():
    today = datetime.now(timezone.utc).date()
    rows = []
    status = []

    for cinema, url in SOURCES:
        try:
            if cinema == "Dendy Canberra":
                found = collect_dendy(url, today.year)
            else:
                found = collect_arc(today) if cinema == "NFSA Arc Cinema" else collect_jsonld(cinema, url, today.year)
            rows.extend(found)
            status.append({
                "cinema": cinema,
                "status": "ok" if found else "no supported events found",
                "count": len(found),
            })
        except requests.RequestException as exc:
            status.append({
                "cinema": cinema,
                "status": "fetch error",
                "detail": str(exc)[:200],
            })
            print("WARN", cinema, exc)

    try:
        palace_rows = collect_palace(today)
        rows.extend(palace_rows)
        status.append({
            "cinema": "Palace Electric Cinema Canberra",
            "status": "ok" if palace_rows else "no supported sessions found",
            "count": len(palace_rows),
        })
    except Exception as exc:
        status.append({
            "cinema": "Palace Electric Cinema Canberra",
            "status": "fetch error", "count": 0, "detail": str(exc)[:200],
        })
        print("WARN Palace Electric Cinema Canberra", exc)


    try:
        limelight_rows = collect_limelight(today)
        rows.extend(limelight_rows)
        status.append({
            "cinema": "Limelight Tuggeranong",
            "status": "ok" if limelight_rows else "no qualifying sessions found",
            "count": len(limelight_rows),
            "programme_urls": [
                "https://limelightcinemas.com.au/movies/now-showing/",
                "https://limelightcinemas.com.au/movies/coming-soon/",
            ],
            "detail": "Tuggeranong venue selected explicitly; " + ("; ".join([])),
        })
    except Exception as exc:
        status.append({
            "cinema": "Limelight Tuggeranong",
            "status": "fetch error", "count": 0, "detail": str(exc)[:200],
        })
        print("WARN Limelight Tuggeranong", exc)

    try:
        diagnose_hoyts()
    except Exception as exc:
        print("WARN HOYTS diagnostic", str(exc)[:300])

    try:
        anu_rows, anu_status = collect_anu(today)
        rows.extend(anu_rows)
        status.append(anu_status)
    except requests.RequestException as exc:
        status.append({
            "cinema": "ANU Film Group",
            "status": "fetch error",
            "count": 0,
            "programme_url": ANU_PROGRAMME,
            "detail": str(exc)[:200],
        })
        print("WARN ANU Film Group", exc)

    # Temporary intermediary import: this proves that structured NFSA data
    # prepared outside GitHub can flow through the existing collector and app.
    # The file is explicitly provisional and must be replaced by a live feed.
    intermediary_path = ROOT / "data" / "nfsa-intermediary.json"
    try:
        intermediary = json.loads(intermediary_path.read_text(encoding="utf-8"))
        arc_rows = []
        for item in intermediary.get("sessions", []):
            # Exclude NFSA sessions explicitly designated as dementia-friendly.
            # Check the title and descriptive/source metadata so future listings
            # are caught even when the film title itself is ordinary.
            exclusion_text = " ".join(
                str(item.get(field) or "")
                for field in ("title", "description", "event_type", "category",
                              "ticket_url", "url", "tags")
            )
            if re.search(r"dementia[\s_-]*friendly", exclusion_text, re.IGNORECASE):
                print("NFSA session excluded (dementia-friendly):", item.get("title", "untitled"))
                continue

            year = item.get("year")
            start = str(item.get("start") or "")
            if not year or not start:
                continue
            if datetime.fromisoformat(start).date() < today:
                continue
            if today.year - int(year) < 10:
                continue
            arc_rows.append({
                "film_id": re.sub(r"[^a-z0-9]+", "-", str(item["title"]).lower()).strip("-"),
                "title": str(item["title"]),
                "year": int(year),
                "cinema": "NFSA Arc Cinema",
                "start": start,
                "description": "",
                "ticket_url": item.get("ticket_url", "https://tickets.nfsa.gov.au/events"),
                "poster_url": "",
            })
        rows.extend(arc_rows)
        status.append({
            "cinema": "NFSA Arc Cinema",
            "status": "provisional intermediary import",
            "count": len(arc_rows),
            "detail": "Static test import; not yet an automated NFSA feed",
        })
        print("NFSA intermediary rows imported:", len(arc_rows))
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, KeyError) as exc:
        print("WARN NFSA intermediary import:", str(exc)[:300])


    # Fixed one-off import for Stromlo Outdoor Cinema. This programme is
    # intentionally not scraped or refreshed; only future qualifying sessions
    # from the checked static file are carried into the generated listings.
    stromlo_path = ROOT / "data" / "stromlo-static.json"
    try:
        stromlo = json.loads(stromlo_path.read_text(encoding="utf-8"))
        stromlo_rows = []
        for item in stromlo.get("sessions", []):
            year = item.get("year")
            start = str(item.get("start") or "")
            if not year or not start:
                continue
            if datetime.fromisoformat(start).date() < today:
                continue
            if today.year - int(year) < 10:
                continue
            title = str(item["title"])
            stromlo_rows.append({
                "film_id": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
                "title": title,
                "year": int(year),
                "cinema": "Stromlo Outdoor Cinema",
                "start": start,
                "description": "",
                "ticket_url": item.get("ticket_url", ""),
                "poster_url": "",
            })
        rows.extend(stromlo_rows)
        status.append({
            "cinema": "Stromlo Outdoor Cinema",
            "status": "fixed one-off import",
            "count": len(stromlo_rows),
            "detail": "Static programme; deliberately not refreshed from the source website",
        })
        print("Stromlo static rows imported:", len(stromlo_rows))
    except FileNotFoundError:
        pass
    except (ValueError, TypeError, KeyError) as exc:
        print("WARN Stromlo static import:", str(exc)[:300])

    unique = {(row["film_id"], row["cinema"], row["start"]): row for row in rows}
    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "sessions": sorted(unique.values(), key=lambda row: row["start"]),
        "sources": status,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Candidate sessions:", len(unique))
    print("Dendy sessions:", sum(1 for row in unique.values() if row["cinema"] == "Dendy Canberra"))


if __name__ == "__main__":
    main()
