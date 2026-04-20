"""
Funda Scraper - Phase 3
========================
- Filter op max. aankoopprijs EUR 300.000
- Automatische rendementberekening per woning
- Mobiel-vriendelijke HTML-email (kaartlayout voor iPhone)

Gebruik:
    python3 funda_scraper_phase3.py

Vereisten:
    pip3 install playwright beautifulsoup4
    playwright install chromium
"""

import csv
import json
import re
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ---------------------------------------------------------------------------
# Configuratie
# ---------------------------------------------------------------------------

MAX_PRICE = 300_000

DEFAULT_URL = (
    "https://www.funda.nl/zoeken/koop?"
    "sort=%22date_down%22"
    "&price_max=300000"
)

EMAIL_SENDER   = "bernhardkerkhoff14@gmail.com"
EMAIL_PASSWORD = "jato wwtx ikye hxcx"
EMAIL_RECEIVER = "bk@mypersonality.store"
EMAIL_SUBJECT  = "Funda Investeringskansen onder EUR 300.000"

SEEN_FILE = Path(__file__).parent / "funda_seen_phase3.json"
CSV_FILE  = Path(__file__).parent / "funda_investments.csv"

PAGE_LOAD_TIMEOUT = 15_000
AFTER_LOAD_WAIT   =  2_000
READY_SELECTOR    = 'a[href*="/detail/koop/"]'

HUUR_PER_M2 = {
    "amsterdam":   18.0,
    "rotterdam":   13.0,
    "den-haag":    13.5,
    "utrecht":     16.0,
    "eindhoven":   12.5,
    "groningen":    9.5,
    "haarlem":     15.0,
    "leiden":      14.0,
    "delft":       13.0,
    "breda":       10.5,
    "nijmegen":    10.5,
    "tilburg":      9.5,
    "maastricht":  10.0,
    "arnhem":       9.5,
    "zwolle":       9.5,
    "default":      8.5,
}

MIN_HUUR = 500
MAX_HUUR = 880


# ---------------------------------------------------------------------------
# Ophalen
# ---------------------------------------------------------------------------

def fetch_html(url):
    print(f"[*] Browser openen: {url}")
    print("[*] Er opent even een Chrome-venster -- dat is normaal.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page()
        try:
            page.goto(url, timeout=PAGE_LOAD_TIMEOUT)
            page.wait_for_selector(READY_SELECTOR, timeout=PAGE_LOAD_TIMEOUT)
            page.wait_for_timeout(AFTER_LOAD_WAIT)
            print("[*] Pagina geladen.")
        except PlaywrightTimeoutError:
            print("[!] Timeout: pagina laadde niet op tijd.")
            browser.close()
            sys.exit(1)

        html = page.content()
        browser.close()

    return html


# ---------------------------------------------------------------------------
# Parseer listings
# ---------------------------------------------------------------------------

def parse_listings(html):
    soup = BeautifulSoup(html, "html.parser")

    links = soup.find_all("a", href=lambda h: h and "/detail/koop/" in h)

    seen = set()
    urls = []
    for link in links:
        href = link["href"]
        full_url = href if href.startswith("http") else f"https://www.funda.nl{href}"
        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)

    if not urls:
        print("[!] Geen listing-links gevonden.")
        return []

    print(f"[*] {len(urls)} unieke links gevonden, data extraheren...")
    flat_text = re.sub(r"\s+", " ", soup.get_text(separator=" "))

    listings = []
    for url in urls:
        listing = extract_listing(flat_text, url)
        if listing:
            listings.append(listing)

    return listings


def extract_listing(flat_text, listing_url):
    url_match = re.search(
        r"/detail/koop/([^/]+)/[^-]+-(.+?)/\d+/",
        listing_url
    )
    if not url_match:
        return None

    city_slug     = url_match.group(1)
    city_from_url = city_slug.replace("-", " ").title()
    address       = url_match.group(2).replace("-", " ").title()

    words      = address.split()
    first_word = re.escape(words[0])
    last_part  = re.escape(words[-1]) if len(words) > 1 else first_word

    addr_match = re.search(first_word + r".{0,30}" + last_part, flat_text, re.IGNORECASE)
    snippet = ""
    if addr_match:
        snippet = flat_text[addr_match.start(): addr_match.start() + 300]

    # Stad
    city = ""
    city_match = re.search(
        r"(\d{4}\s?[A-Z]{2}\s[A-Z][a-zA-Z\s\-]{2,25}?)(?=\s*€|\s*\d{2,3}\s?m²|\s*\d{3,})",
        snippet
    )
    if city_match:
        full_city = re.search(r"(\d{4}\s?[A-Z]{2}\s[A-Z][a-zA-Z\s\-]{2,25}?)(?=€|\d)", snippet)
        city = full_city.group(1).strip() if full_city else city_match.group(1).strip()
    if not city:
        city = city_from_url

    # Prijs
    price_str = ""
    price_num = None
    price_match = re.search(r"€\s?([\d\.]+)\s?(?:k\.k\.|v\.o\.n\.)", snippet)
    if price_match:
        price_str = "€ " + price_match.group(1) + " k.k."
        try:
            price_num = int(price_match.group(1).replace(".", ""))
        except ValueError:
            pass

    # Filter op max prijs
    if price_num and price_num > MAX_PRICE:
        return None

    # Oppervlak
    living_area = ""
    area_m2 = None
    area_match = re.search(r"(\d{2,4})\s?m\u00b2", snippet)
    if area_match:
        living_area = area_match.group(1) + " m\u00b2"
        try:
            area_m2 = int(area_match.group(1))
        except ValueError:
            pass

    investment = calculate_investment(price_num, area_m2, city_slug)

    result = {
        "title":       address + ", " + city,
        "address":     address,
        "city":        city,
        "price":       price_str,
        "price_num":   price_num,
        "living_area": living_area,
        "area_m2":     area_m2,
        "listing_url": listing_url,
        "found_at":    datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    result.update(investment)
    return result


# ---------------------------------------------------------------------------
# Rendementberekening
# ---------------------------------------------------------------------------

def calculate_investment(price, m2, city_slug):
    result = {
        "est_rent":      None,
        "gross_yield":   None,
        "net_yield":     None,
        "own_capital":   None,
        "mortgage":      None,
        "verdict":       "Onbekend",
        "verdict_color": "#999999",
        "verdict_emoji": "",
    }

    if not price or price <= 0:
        return result

    huur_per_m2 = HUUR_PER_M2.get(city_slug, HUUR_PER_M2["default"])
    est_rent    = round((m2 if m2 and m2 > 0 else 60) * huur_per_m2)
    est_rent    = max(MIN_HUUR, min(MAX_HUUR, est_rent))

    kk               = round(price * 0.06)
    total_investment = price + kk
    mortgage         = round(price * 0.70)
    own_capital      = total_investment - mortgage
    annual_rent      = est_rent * 12
    gross_yield      = round((annual_rent / price) * 100, 1)
    net_annual       = annual_rent - (annual_rent * 0.15) - (price * 0.012)
    net_yield        = round((net_annual / own_capital) * 100, 1)

    if net_yield >= 8:
        verdict, verdict_color, verdict_emoji = "Uitstekend", "#27ae60", "&#11088;"
    elif net_yield >= 6:
        verdict, verdict_color, verdict_emoji = "Goed", "#2ecc71", "&#128077;"
    elif net_yield >= 4:
        verdict, verdict_color, verdict_emoji = "Matig", "#f39c12", "&#128528;"
    else:
        verdict, verdict_color, verdict_emoji = "Slecht", "#e74c3c", "&#128078;"

    result.update({
        "est_rent":      est_rent,
        "gross_yield":   gross_yield,
        "net_yield":     net_yield,
        "own_capital":   own_capital,
        "mortgage":      mortgage,
        "verdict":       verdict,
        "verdict_color": verdict_color,
        "verdict_emoji": verdict_emoji,
    })
    return result


# ---------------------------------------------------------------------------
# Deduplicatie
# ---------------------------------------------------------------------------

def load_seen():
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


def filter_new(listings, seen):
    return [l for l in listings if l["listing_url"] not in seen]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def append_to_csv(listings):
    if not listings:
        return

    fieldnames = [
        "found_at", "address", "city", "price", "living_area",
        "est_rent", "gross_yield", "net_yield",
        "own_capital", "mortgage", "verdict", "listing_url"
    ]
    file_exists = CSV_FILE.exists()

    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(listings)

    print(f"[*] {len(listings)} listing(s) opgeslagen in '{CSV_FILE.name}'")


# ---------------------------------------------------------------------------
# E-mail — mobiel-vriendelijke kaartlayout
# ---------------------------------------------------------------------------

def fmt_eur(amount):
    if amount is None:
        return "-"
    return "€ " + f"{amount:,.0f}".replace(",", ".")


def build_html_email(listings):
    # Sorteer op beste netto rendement
    sorted_listings = sorted(
        listings,
        key=lambda x: x.get("net_yield") or 0,
        reverse=True
    )

    cards = ""
    for l in sorted_listings:
        verdict_color = l.get("verdict_color", "#999")
        verdict       = l.get("verdict", "-")
        verdict_emoji = l.get("verdict_emoji", "")

        cards += f"""
        <!-- Woningkaart -->
        <table width="100%" cellpadding="0" cellspacing="0" border="0"
               style="max-width:600px; margin:0 auto 16px auto;
                      background:#ffffff; border-radius:12px;
                      box-shadow:0 2px 8px rgba(0,0,0,0.08);
                      overflow:hidden;">

          <!-- Kleurstrip bovenaan gebaseerd op oordeel -->
          <tr>
            <td style="background:{verdict_color}; height:6px; font-size:0;">&nbsp;</td>
          </tr>

          <!-- Adres + badge -->
          <tr>
            <td style="padding:16px 16px 8px 16px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="vertical-align:top;">
                    <div style="font-size:16px; font-weight:bold; color:#222;
                                line-height:1.3; margin-bottom:2px;">
                      {l['address']}
                    </div>
                    <div style="font-size:13px; color:#888;">
                      {l['city']}
                    </div>
                  </td>
                  <td style="vertical-align:top; text-align:right; white-space:nowrap;">
                    <span style="background:{verdict_color}; color:white;
                                 padding:4px 10px; border-radius:20px;
                                 font-size:12px; font-weight:bold;">
                      {verdict_emoji} {verdict}
                    </span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Prijs + oppervlak -->
          <tr>
            <td style="padding:4px 16px 12px 16px;">
              <span style="font-size:20px; font-weight:bold; color:#E37222;">
                {l['price'] or '-'}
              </span>
              &nbsp;
              <span style="font-size:14px; color:#888;">
                {l['living_area'] or '-'}
              </span>
            </td>
          </tr>

          <!-- Divider -->
          <tr>
            <td style="padding:0 16px;">
              <div style="border-top:1px solid #f0f0f0;"></div>
            </td>
          </tr>

          <!-- Rendement grid: 2 kolommen -->
          <tr>
            <td style="padding:12px 16px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <!-- Huur -->
                  <td width="50%" style="vertical-align:top; padding-right:8px;">
                    <div style="font-size:11px; color:#999; text-transform:uppercase;
                                letter-spacing:0.5px; margin-bottom:2px;">
                      Geschatte huur
                    </div>
                    <div style="font-size:16px; font-weight:bold; color:#2980b9;">
                      {fmt_eur(l.get('est_rent'))}<span style="font-size:12px;
                      font-weight:normal; color:#888;">/mnd</span>
                    </div>
                  </td>
                  <!-- Rendement -->
                  <td width="50%" style="vertical-align:top; padding-left:8px;">
                    <div style="font-size:11px; color:#999; text-transform:uppercase;
                                letter-spacing:0.5px; margin-bottom:2px;">
                      Rendement
                    </div>
                    <div style="font-size:16px; font-weight:bold; color:#27ae60;">
                      {l.get('net_yield') or '-'}%
                      <span style="font-size:12px; font-weight:normal; color:#888;">netto</span>
                    </div>
                    <div style="font-size:12px; color:#aaa;">
                      {l.get('gross_yield') or '-'}% bruto
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Financiering -->
          <tr>
            <td style="padding:0 16px 12px 16px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <!-- Eigen geld -->
                  <td width="50%" style="vertical-align:top; padding-right:8px;">
                    <div style="font-size:11px; color:#999; text-transform:uppercase;
                                letter-spacing:0.5px; margin-bottom:2px;">
                      Eigen inbreng
                    </div>
                    <div style="font-size:14px; font-weight:bold; color:#333;">
                      {fmt_eur(l.get('own_capital'))}
                    </div>
                    <div style="font-size:11px; color:#aaa;">incl. kosten koper</div>
                  </td>
                  <!-- Hypotheek -->
                  <td width="50%" style="vertical-align:top; padding-left:8px;">
                    <div style="font-size:11px; color:#999; text-transform:uppercase;
                                letter-spacing:0.5px; margin-bottom:2px;">
                      Hypotheek (70%)
                    </div>
                    <div style="font-size:14px; font-weight:bold; color:#333;">
                      {fmt_eur(l.get('mortgage'))}
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Bekijk knop -->
          <tr>
            <td style="padding:0 16px 16px 16px;">
              <a href="{l['listing_url']}"
                 style="display:block; background:#E37222; color:white;
                        text-align:center; padding:12px; border-radius:8px;
                        text-decoration:none; font-size:15px; font-weight:bold;">
                Bekijk op Funda
              </a>
            </td>
          </tr>

        </table>"""

    timestamp = datetime.now().strftime("%d %B %Y om %H:%M")

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Funda Investeringskansen</title>
</head>
<body style="margin:0; padding:0; background:#f2f2f7;
             font-family:-apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;">

  <!-- Header -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="background:#E37222; padding:24px 16px; text-align:center;">
        <div style="font-size:24px; font-weight:bold; color:white;">
          &#127968; Funda Investeringskansen
        </div>
        <div style="font-size:14px; color:rgba(255,255,255,0.85); margin-top:4px;">
          {len(listings)} nieuwe woning(en) onder EUR {MAX_PRICE:,} &bull; {timestamp}
        </div>
      </td>
    </tr>
  </table>

  <!-- Uitleg -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="background:#fff3cd; padding:10px 16px;
                 font-size:12px; color:#856404; text-align:center;">
        Huurprijzen en rendementen zijn schattingen. Doe altijd eigen onderzoek.
      </td>
    </tr>
  </table>

  <!-- Kaarten -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="padding:16px 8px;">
        {cards}
      </td>
    </tr>
  </table>

  <!-- Footer -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="padding:16px; text-align:center; font-size:11px; color:#999;">
        Funda Scraper &bull; {timestamp}<br>
        Bruto rendement: jaarhuur / aankoopprijs &bull;
        Netto rendement: na 15% kosten + box 3 &bull;
        Eigen inbreng incl. 6% kosten koper
      </td>
    </tr>
  </table>

</body>
</html>"""


def send_email(listings):
    if not listings:
        print("[*] Geen nieuwe listings - geen e-mail verstuurd.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{EMAIL_SUBJECT} ({len(listings)} nieuw)"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg.attach(MIMEText(build_html_email(listings), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"[v] E-mail verstuurd naar {EMAIL_RECEIVER} met {len(listings)} listing(s).")
    except Exception as e:
        print(f"[!] E-mail versturen mislukt: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    html     = fetch_html(url)
    listings = parse_listings(html)

    if not listings:
        print("[!] Geen listings gevonden.")
        return

    print(f"[*] {len(listings)} listing(s) onder EUR {MAX_PRICE:,} gevonden.")

    seen         = load_seen()
    new_listings = filter_new(listings, seen)

    print(f"[*] {len(new_listings)} nieuwe listing(s).")

    if not new_listings:
        print("[*] Niets nieuws.")
        return

    seen.update(l["listing_url"] for l in new_listings)
    save_seen(seen)
    append_to_csv(new_listings)
    send_email(new_listings)


if __name__ == "__main__":
    main()
