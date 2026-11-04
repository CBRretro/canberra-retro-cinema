# Canberra Retro Cinema

A mobile-first guide to films at least 10 years old screening at Dendy Canberra, Palace Electric, NFSA Arc Cinema and ANU Film Group.

## First development milestone
- Responsive web app shell with Upcoming, Calendar and Watchlist navigation.
- Local watchlist storage in the visitor's browser.
- Installable web-app manifest and offline shell cache.
- GitHub Actions workflow scheduled every six hours, with manual runs available.
- Conservative first-pass collector that looks for structured Event data.

## Important data limitation
The Dendy, Palace and NFSA collectors are discovery scaffolds, **not yet verified or complete feeds**. ANU Film Group now has a dedicated programme/detail-page collector. Ticketing providers and dynamically loaded session data often require site-specific collection. The programme intentionally starts empty rather than inventing screenings. Next we need to inspect each cinema's actual programme pages, implement and test site-specific extraction, and match film titles to reliable release-year/poster metadata before treating results as complete.

## Publish with GitHub Pages
In repository Settings, open Pages and set Build and deployment Source to **GitHub Actions**. Then run the workflow from the Actions tab. GitHub will show the published URL in the deployment details.

The scheduled workflow runs at minute 17 every six hours. Collection status is written to data/sessions.json; a zero-result run means no supported structured events were found, not that there are no eligible screenings.
\n\n## ANU Film Group\nThe collector reads the official [ANU Film Group programme](https://www.anufg.org.au/programme), follows individual screening-information pages, and extracts the listed screening date/time, release year, poster and synopsis where available. It only includes future screenings at least ten years old. A source status is recorded for the venue so collection failures are visible.\n