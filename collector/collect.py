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

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sessions.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CanberraRetroCinema/0.1)"}
TIMEOUT = 25
SOURCES = [
    ("Dendy Canberra", "https://canberra.dendy.com.au/now-showing/"),
    ("Palace Electric", "https://www.palacecinemas.com.au/cinemas/palace-electric-cinema"),
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
    """Collect Dendy Canberra screenings from Now Showing and Coming Soon."""
    coming_soon_url = "https://canberra.dendy.com.au/coming-soon/"
    now = datetime.now(timezone.utc)
    year_cache = {}

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

    def wikidata_year(title):
        key = title.casefold().strip()
        if key in year_cache:
            return year_cache[key]
        year_cache[key] = None
        try:
            response = requests.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities",
                    "search": title,
                    "language": "en",
                    "format": "json",
                    "type": "item",
                },
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            results = response.json().get("search", [])
            candidates = []
            for item in results[:10]:
                if item.get("label", "").casefold().strip() != key:
                    continue
                description = str(item.get("description", "")).casefold()
                if "film" not in description and "movie" not in description:
                    continue
                entity_response = requests.get(
                    f"https://www.wikidata.org/wiki/Special:EntityData/{item['id']}.json",
                    headers=HEADERS,
                    timeout=TIMEOUT,
                )
                entity_response.raise_for_status()
                entity = entity_response.json()["entities"][item["id"]]
                claims = entity.get("claims", {})
                instance_ids = {
                    claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                    for claim in claims.get("P31", [])
                }
                dates = []
                for claim in claims.get("P577", []):
                    value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                    date_text = value.get("time", "") if isinstance(value, dict) else ""
                    match = re.search(r"[+-](19\d{2}|20\d{2})-", date_text)
                    if match:
                        dates.append(int(match.group(1)))
                if dates:
                    candidates.append((0 if "Q11424" in instance_ids else 1, min(dates)))
            if candidates:
                candidates.sort()
                year_cache[key] = candidates[0][1]
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            print("WARN Dendy release-year lookup", title, str(exc)[:140])
        return year_cache[key]

    now_soup = get_soup(url)
    movie_pages = {}
    for movie in jsonld_nodes(now_soup):
        if "Movie" not in node_types(movie):
            continue
        movie_url = movie.get("sameAs") or str(movie.get("@id", "")).split("#", 1)[0]
        if movie_url:
            movie_pages[movie_url] = {"label": movie.get("name", ""), "movie": movie, "coming_soon": False}

    try:
        coming_soup = get_soup(coming_soon_url)
        for anchor in coming_soup.find_all("a", href=True):
            movie_url = urljoin(coming_soon_url, anchor["href"]).split("#", 1)[0]
            if "/movie/" not in movie_url or "canberra.dendy.com.au" not in movie_url:
                continue
            if movie_url not in movie_pages:
                movie_pages[movie_url] = {"label": anchor.get_text(" ", strip=True), "movie": {}, "coming_soon": True}
            else:
                movie_pages[movie_url]["coming_soon"] = True
    except requests.RequestException as exc:
        print("WARN Dendy Coming Soon page", str(exc)[:180])

    found = []
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
            continue
        year, description = year_from_text(movie)
        if year is None and page_info.get("coming_soon"):
            year = wikidata_year(title)
        if year is None or today_year - year < 10:
            continue

        # Dendy's individual film pages display showtimes as checkout links.
        # These are not consistently exposed as JSON-LD ScreeningEvent nodes,
        # so read the visible date/time text and retain each link as its ticket URL.
        events = [node for node in nodes if "ScreeningEvent" in node_types(node)]
        if not events:
            page_text = detail_soup.get_text(" ", strip=True)
            page_dates = re.findall(r"\\b(20\\d{2})-(\\d{2})-(\\d{2})\\b", page_text)
            page_years = [int(match[0]) for match in page_dates]
            default_year = page_years[0] if page_years else today_year
            local_tz = ZoneInfo("Australia/Sydney")
            for anchor in detail_soup.find_all("a", href=True):
                href = anchor["href"]
                if "/checkout/showing/" not in href:
                    continue
                label = anchor.get_text(" ", strip=True)
                match = re.search(
                    r"([A-Za-z]+\\s+\\d{1,2}),?\\s+(\\d{1,2}:\\d{2}\\s*[AP]M)",
                    label,
                    re.IGNORECASE,
                )
                if not match:
                    continue
                date_label, time_label = match.groups()
                parsed = None
                candidate_years = sorted(set(page_years + [default_year, today_year, today_year + 1]))
                for candidate_year in candidate_years:
                    try:
                        date_part = datetime.strptime(
                            f"{date_label} {candidate_year}", "%B %d %Y"
                        ).date()
                        time_part = datetime.strptime(time_label.upper().replace(" ", ""), "%I:%M%p").time()
                        candidate = datetime.combine(date_part, time_part, tzinfo=local_tz)
                    except ValueError:
                        continue
                    if candidate > now:
                        parsed = candidate
                        break
                if parsed is None:
                    continue
                events.append({
                    "name": title,
                    "startDate": parsed.isoformat(),
                    "url": urljoin(movie_url, href),
                })

        for event in events:
            start = event.get("startDate")
            if not start:
                continue
            try:
                if datetime.fromisoformat(str(start).replace("Z", "+00:00")) <= now:
                    continue
            except ValueError:
                continue

            event_title = str(event.get("name") or title).strip()
            found.append({
                "film_id": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
                "title": title,
                "year": year,
                "cinema": "Dendy Canberra",
                "start": start,
                "description": description,
                "ticket_url": event.get("url", ""),
                "poster_url": movie.get("image") or movie.get("thumbnailUrl") or "",
            })

    return found

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
                found = collect_jsonld(cinema, url, today.year)
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
