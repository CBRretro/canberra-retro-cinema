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
    """Collect future Dendy Canberra screenings from linked film pages."""
    soup = get_soup(url)
    movies = {}

    def walk_nodes(obj):
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from walk_nodes(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from walk_nodes(value)

    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in walk_nodes(data):
            typ = node.get("@type", [])
            typ = [typ] if isinstance(typ, str) else typ
            if "Movie" in typ and node.get("@id"):
                movies[node["@id"]] = node

    found = []
    now = datetime.now(timezone.utc)
    for movie in movies.values():
        raw_description = str(movie.get("description", ""))
        description = BeautifulSoup(raw_description, "html.parser").get_text(" ", strip=True)
        year_match = re.search(r"\((19\d{2}|20\d{2})\)", description)
        if not year_match:
            published = movie.get("datePublished") or movie.get("releaseDate") or ""
            year_match = re.search(r"(19\d{2}|20\d{2})", str(published))
        if not year_match:
            continue

        year = int(year_match.group(1))
        if today_year - year < 10:
            continue

        movie_url = movie.get("sameAs") or str(movie.get("@id", "")).split("#", 1)[0]
        if not movie_url:
            continue
        try:
            detail_soup = get_soup(movie_url)
        except requests.RequestException as exc:
            print("WARN Dendy film page", movie_url, str(exc)[:150])
            continue

        for tag in detail_soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(tag.string or tag.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            for event in walk_nodes(data):
                typ = event.get("@type", [])
                typ = [typ] if isinstance(typ, str) else typ
                if "ScreeningEvent" not in typ:
                    continue

                title = str(event.get("name", movie.get("name", ""))).strip()
                start = event.get("startDate")
                if not title or not start:
                    continue
                try:
                    if datetime.fromisoformat(str(start).replace("Z", "+00:00")) <= now:
                        continue
                except ValueError:
                    continue

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
    for slug in ["rocky", "the-maltese-falcon", "eyes-wide-shut", "8", "citizen-kane"]:
        url = "https://canberra.dendy.com.au/movie/" + slug + "/"
        soup = get_soup(url)
        html = str(soup)
        print("YEAR_PROBE",slug,"bytes",len(html))
        for term in ["1976","1941","1999","1963","year","releaseYear","productionYear","datePublished","releaseDate","movieYear","filmYear","tmdb"]:
            hits=[m.start() for m in re.finditer(re.escape(term),html,re.I)][:3]
            if hits:
                p=hits[0]
                print("YEAR_HIT",slug,term,hits,html[max(0,p-180):p+450].replace("\\n"," "))
        for tag in soup.find_all("meta"):
            k=tag.get("property") or tag.get("name") or tag.get("itemprop")
            v=tag.get("content")
            if k and v and any(t in (k+" "+v).lower() for t in ["year","release","1976","1941","1999","1963"]):
                print("YEAR_META",slug,k,v[:500])
        for tag in soup.find_all("script"):
            src=tag.get("src")
            raw=tag.string or tag.get_text()
            if src: print("YEAR_SCRIPT_SRC",slug,src)
            if raw and len(raw)>500 and any(t in raw.lower() for t in ["releaseyear","productionyear","film_year","movieyear","1976","1941","1999","1963"]):
                print("YEAR_SCRIPT_INLINE",slug,len(raw),raw[:1800].replace("\\n"," "))

if __name__ == "__main__":
    main()
