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
    # TEMP: inspect Dendy's JSON-LD and client-side showtime source.
    base = "https://canberra.dendy.com.au/"
    probe_url = base + "now-showing/"
    try:
        response = requests.get(probe_url, headers=HEADERS, timeout=TIMEOUT)
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        print("DENDY_PROBE", response.status_code, len(html))
        for n, tag in enumerate(soup.select('script[type="application/ld+json"]')):
            raw = tag.string or tag.get_text()
            try:
                data = json.loads(raw)
                def types(obj):
                    if isinstance(obj, dict):
                        t = obj.get("@type")
                        if t: yield t
                        for v in obj.values(): yield from types(v)
                    elif isinstance(obj, list):
                        for v in obj: yield from types(v)
                print("DENDY_JSONLD", n, list(types(data))[:30], raw[:1100].replace("\\n", " "))
            except Exception:
                print("DENDY_JSONLD_BAD", n, raw[:400])
        for term in ["showtimes", "showtime", "sessionTimes", "session_times", "sessions", "startDate", "screening", "movieId", "sessionDate"]:
            positions = [m.start() for m in re.finditer(term, html, re.I)][:5]
            print("DENDY_HTML_TERM", term, positions)
        scripts = [tag.get("src") for tag in soup.find_all("script") if tag.get("src")]
        for src in scripts:
            if "app." in src or "vendor." in src:
                js_url = urljoin(base, src)
                try:
                    js = requests.get(js_url, headers=HEADERS, timeout=TIMEOUT).text
                    print("DENDY_JS", js_url, len(js))
                    for term in ["showtimes", "showtime", "session", "api/", "graphql", "cinemaId", "movieId"]:
                        hits = [m.start() for m in re.finditer(term, js, re.I)][:8]
                        print("DENDY_JS_TERM", term, hits)
                        if hits:
                            p = hits[0]
                            print("DENDY_JS_CONTEXT", term, js[max(0,p-200):p+500])
                except requests.RequestException as exc:
                    print("DENDY_JS_ERROR", js_url, str(exc)[:150])
    except requests.RequestException as exc:
        print("DENDY_PROBE_ERROR", str(exc)[:250])
    today = datetime.now(timezone.utc).date()
    rows = []
    status = []

    for cinema, url in SOURCES:
        try:
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


if __name__ == "__main__":
    main()
