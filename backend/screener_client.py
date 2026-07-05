"""Client for screener.in — fetches a saved screen and company fundamentals pages.

Authentication: pass your screener.in `sessionid` cookie via the SCREENER_SESSIONID
environment variable. Only GET requests are made.
"""

import os
import re
import time
import requests
from bs4 import BeautifulSoup

BASE = "https://www.screener.in"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


class ScreenerError(Exception):
    pass


class ScreenerClient:
    def __init__(self, session_id=None, delay=1.2):
        self.session_id = session_id or os.environ.get("SCREENER_SESSIONID", "").strip()
        self.delay = delay
        self.http = requests.Session()
        self.http.headers.update(HEADERS)
        if self.session_id:
            self.http.cookies.set("sessionid", self.session_id, domain=".screener.in")

    def _get(self, url):
        time.sleep(self.delay)
        resp = self.http.get(url, timeout=30)
        if resp.status_code in (401, 403):
            raise ScreenerError(
                "screener.in rejected the request (%s). Your SCREENER_SESSIONID is "
                "probably missing or expired — grab a fresh sessionid cookie and update "
                "the GitHub secret." % resp.status_code)
        resp.raise_for_status()
        return resp.text

    # ---------------------------------------------------------------- screens

    def find_screen_url(self, screen_name):
        """Locate a saved screen by its display name on the user's screens page."""
        if not self.session_id:
            raise ScreenerError("SCREENER_SESSIONID is not set; cannot access saved screens.")
        html = self._get(BASE + "/explore/")
        target = screen_name.strip().lower()
        for page_html in (html, self._get(BASE + "/screens/")):
            soup = BeautifulSoup(page_html, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"^/screens/\d+/")):
                if a.get_text(strip=True).lower() == target:
                    return BASE + a["href"]
        raise ScreenerError(
            "Could not find a screen named '%s' in your screener.in account. "
            "Check the name in backend/config.yaml matches exactly." % screen_name)

    def fetch_screen_stocks(self, screen_name):
        """Return [{'code': 'TCS', 'name': 'Tata Consultancy...'}] for every stock
        currently in the saved screen, following pagination."""
        url = self.find_screen_url(screen_name)
        stocks, seen, page = [], set(), 1
        while True:
            sep = "&" if "?" in url else "?"
            html = self._get("%s%spage=%d" % (url, sep, page))
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", class_=re.compile("data-table"))
            if table is None:
                break
            rows_added = 0
            for a in table.find_all("a", href=re.compile(r"^/company/")):
                m = re.match(r"^/company/([^/]+)/", a["href"])
                if not m:
                    continue
                code = m.group(1)
                if code in seen:
                    continue
                seen.add(code)
                stocks.append({"code": code, "name": a.get_text(strip=True)})
                rows_added += 1
            if rows_added == 0:
                break
            # stop when there is no link to the next page
            if not soup.find("a", href=re.compile(r"page=%d" % (page + 1))):
                break
            page += 1
        if not stocks:
            raise ScreenerError(
                "Screen '%s' returned zero stocks — either the screen is empty or the "
                "page layout changed." % screen_name)
        return stocks

    # ----------------------------------------------------------- company page

    def fetch_company(self, code):
        """Parse the company page into raw fundamental tables.

        Returns a dict of sections; every value is best-effort and may be missing.
        """
        try:
            html = self._get("%s/company/%s/consolidated/" % (BASE, code))
        except Exception:
            html = self._get("%s/company/%s/" % (BASE, code))
        soup = BeautifulSoup(html, "html.parser")
        out = {
            "top_ratios": self._parse_top_ratios(soup),
            "quarters": self._parse_section_table(soup, "quarters"),
            "profit_loss": self._parse_section_table(soup, "profit-loss"),
            "balance_sheet": self._parse_section_table(soup, "balance-sheet"),
            "cash_flow": self._parse_section_table(soup, "cash-flow"),
            "shareholding": self._parse_section_table(soup, "shareholding"),
        }
        return out

    def _parse_top_ratios(self, soup):
        ratios = {}
        for li in soup.select("#top-ratios li"):
            name_el = li.select_one(".name")
            val_el = li.select_one(".value") or li.select_one(".number")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            val = (val_el.get_text(" ", strip=True) if val_el else
                   li.get_text(" ", strip=True).replace(name, "", 1))
            ratios[name] = val.strip()
        return ratios

    def _parse_section_table(self, soup, section_id):
        """Parse a screener section table into {'columns': [...], 'rows': {label: [floats]}}"""
        section = soup.find("section", id=section_id)
        if section is None:
            return None
        table = section.find("table")
        if table is None:
            return None
        head = table.find("thead")
        columns = [th.get_text(strip=True) for th in head.find_all("th")][1:] if head else []
        rows = {}
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            label = re.sub(r"\s*[+\-]\s*$", "", cells[0].get_text(" ", strip=True))
            label = label.replace("\xa0", " ").strip()
            values = [_num(td.get_text(strip=True)) for td in cells[1:]]
            if label:
                rows[label] = values
        return {"columns": columns, "rows": rows}


def _num(text):
    """'1,234.5' -> 1234.5 ; '12%' -> 12.0 ; '' -> None"""
    t = text.replace(",", "").replace("%", "").strip()
    if t in ("", "-", "--"):
        return None
    try:
        return float(t)
    except ValueError:
        return None
