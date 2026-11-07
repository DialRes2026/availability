#!/usr/bin/env python3
"""
Reads Dial Residential's public AppFolio listings and rewrites source/listings.json to match.

  python3 scripts/fetch_appfolio.py               # fetch live, write listings.json if anything changed
  python3 scripts/fetch_appfolio.py --dry-run     # fetch live, print the diff, write nothing
  python3 scripts/fetch_appfolio.py --fixtures DIR  # read DIR/index.html + DIR/<uid>.html instead of the web (tests)

Writes a human-readable change summary to .sync-changes.txt (used as the commit message).

Exit codes:
  0  success (changed or not)
  1  hard failure: AppFolio unreachable, page layout not recognised, or a listing could not be parsed.
     Nothing is written, so the website keeps showing the last good data.
  2  partial: everything publishable was written, but at least one available unit was skipped
     (unknown property, or no floor plan on the site map). The run is marked failed so GitHub emails the owner.
"""
import argparse, json, os, re, sys, time, urllib.request

from bs4 import BeautifulSoup, NavigableString, Tag

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LISTINGS = os.path.join(ROOT, "source", "listings.json")
CHANGES = os.path.join(ROOT, ".sync-changes.txt")
BASE = "https://dialresidential.appfolio.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
PROPERTIES = {"Mowbray Manor": "mowbray", "Tributary 50": "tributary"}
SIGHTMAPS = {
    "mowbray": os.path.join(ROOT, "site-files", "mowbray-sightmap-standalone.html"),
    "tributary": os.path.join(ROOT, "site-files", "tributary-50-sightmap-standalone.html"),
}
KEY_ORDER = ["property", "propertySlug", "unit", "uid", "beds", "baths", "sqft", "rent", "status",
             "matterportUrl", "description", "amenities", "appliances", "petPolicy", "fees"]


class LayoutError(Exception):
    pass


# ---------- fetching ----------

def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # network blips, 5xx
            last = e
            time.sleep(5 * (i + 1))
    raise LayoutError("Could not fetch %s: %s" % (url, last))


def make_getter(fixtures):
    if not fixtures:
        return lambda path: fetch(BASE + path + ("&" if "?" in path else "?") + "cb=%d" % time.time())

    def get(path):
        name = "index.html" if path.startswith("/listings?") or path == "/listings" else path.rsplit("/", 1)[-1] + ".html"
        with open(os.path.join(fixtures, name), encoding="utf-8") as f:
            return f.read()
    return get


# ---------- parsing ----------

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_index(html):
    # Guard against a block page / redesign: the real page always carries these markers.
    if "/listings/detail/" not in html and "Current Listings" not in html and "listing-item" not in html:
        raise LayoutError("AppFolio listings page not recognised (blocked or redesigned?)")
    uids = []
    for m in re.finditer(r"/listings/detail/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", html):
        if m.group(1) not in uids:
            uids.append(m.group(1))
    return uids


def section_items(soup, heading):
    for h in soup.find_all("h3"):
        if clean(h.get_text()) == heading:
            ul = h.find_next_sibling()
            if ul is not None and ul.name == "ul":
                return [clean(li.get_text()) for li in ul.find_all("li")]
    return []


def money(s):
    m = re.search(r"\$([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else None


def num(s):
    v = float(s)
    return int(v) if v == int(v) else v


def parse_fee(item):
    name, sep, amount = item.partition(": ")
    if not sep:
        raise LayoutError("Fee line not understood: %r" % item)
    return {"name": name.strip(), "amount": amount.strip()}


def parse_description(p):
    if p is None:
        return "", None
    tour = None
    for a in p.find_all("a"):
        if "matterport" in (a.get("href") or ""):
            tour = a["href"]
            break
    buf = []
    for node in p.descendants:
        if isinstance(node, NavigableString):
            if node.parent is not None and node.parent.name == "a":
                continue  # link text handled separately
            buf.append(str(node))
        elif isinstance(node, Tag) and node.name == "br":
            buf.append("\n")
    lines = [clean(l) for l in "".join(buf).split("\n")]
    keep = [l for l in lines if l and not re.match(r"^(virtual tour link|tour of similar unit|tour link)\b", l, re.I)]
    # A line that was only the tour label (link text removed) ends with ':' -> drop it too
    keep = [l for l in keep if not re.fullmatch(r"[^.]{0,40}:", l)]
    return "\n\n".join(keep), tour


def parse_detail(uid, html):
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h2.listing-detail__title")
    if title_el is None:
        raise LayoutError("Listing %s: title not found (layout changed?)" % uid)
    title = clean(title_el.get_text())
    m = re.match(r"^(.*?)\s+Unit\s+([A-Za-z0-9-]+)", title)
    if not m:
        raise LayoutError("Listing %s: title %r has no 'Unit N'" % (uid, title))
    prop, unit = m.group(1).strip(), m.group(2)

    summary = clean(soup.select_one(".header__summary").get_text()) if soup.select_one(".header__summary") else ""
    beds = 0 if re.search(r"\bstudio\b", summary, re.I) else None
    mb = re.search(r"([\d.]+)\s*bd", summary)
    if mb:
        beds = num(mb.group(1))
    mba = re.search(r"([\d.]+)\s*ba", summary)
    msq = re.search(r"([\d,]+)\s*Sq\.?\s*Ft", summary, re.I)

    terms = section_items(soup, "Rental Terms")
    t = {}
    for item in terms:
        k, sep, v = item.partition(":")
        if sep:
            t[k.strip().lower()] = v.strip()
    status = next((i for i in terms if i.lower().startswith("available")), None)
    if not status and "|" in summary:
        status = summary.split("|", 1)[1].strip()

    rent = money(t.get("rent"))
    app_fee = money(t.get("application fee"))
    deposit = money(t.get("security deposit"))

    desc, tour = parse_description(soup.select_one("p.listing-detail__description"))
    pets = section_items(soup, "Pet Policy")

    rec = {
        "property": prop,
        "unit": unit,
        "uid": uid,
        "beds": beds,
        "baths": num(mba.group(1)) if mba else None,
        "sqft": int(msq.group(1).replace(",", "")) if msq else None,
        "rent": rent,
        "status": status,
        "matterportUrl": tour,
        "description": desc,
        "amenities": section_items(soup, "Amenities"),
        "appliances": section_items(soup, "Appliances"),
        "petPolicy": " · ".join(pets) if pets else "No pets allowed",
        "fees": {
            "applicationFee": app_fee,
            "securityDeposit": deposit,
            "required": [parse_fee(i) for i in section_items(soup, "Required Recurring Fees")
                         + section_items(soup, "Required One Time Fees")],
            "optional": [parse_fee(i) for i in section_items(soup, "Optional Recurring Fees")
                         + section_items(soup, "Optional One Time Fees")],
        },
    }
    missing = [k for k in ("beds", "baths", "sqft", "rent", "status") if rec[k] is None]
    if missing or app_fee is None or deposit is None:
        raise LayoutError("Listing %s (%s): could not read %s" % (
            uid, title, missing + [k for k, v in (("applicationFee", app_fee), ("securityDeposit", deposit)) if v is None]))
    return rec


def has_floorplan(slug, unit):
    with open(SIGHTMAPS[slug], encoding="utf-8") as f:
        return ('"unit-%s.jpg"' % unit) in f.read()


# ---------- diff ----------

def describe_changes(old, new):
    key = lambda u: (u["propertySlug"], str(u["unit"]))
    o = {key(u): u for u in old}
    n = {key(u): u for u in new}
    lines = []
    name = lambda u: "%s %s" % ("Mowbray" if u["propertySlug"] == "mowbray" else "Tributary", u["unit"])
    for k in sorted(set(o) - set(n)):
        lines.append("%s: removed (leased / unlisted)" % name(o[k]))
    for k in sorted(set(n) - set(o)):
        lines.append("%s: added at $%s, %s" % (name(n[k]), format(n[k]["rent"], ","), n[k]["status"]))
    for k in sorted(set(o) & set(n)):
        a, b = o[k], n[k]
        bits = []
        if a["rent"] != b["rent"]:
            bits.append("rent $%s -> $%s" % (format(a["rent"], ","), format(b["rent"], ",")))
        if a["fees"].get("securityDeposit") != b["fees"].get("securityDeposit"):
            bits.append("deposit $%s -> $%s" % (a["fees"].get("securityDeposit"), b["fees"].get("securityDeposit")))
        if a["status"] != b["status"]:
            bits.append("%s -> %s" % (a["status"], b["status"]))
        other = [f for f in KEY_ORDER if f not in ("rent", "status", "fees") and a.get(f) != b.get(f)]
        fa = {k2: v for k2, v in a["fees"].items() if k2 != "securityDeposit"}
        fb = {k2: v for k2, v in b["fees"].items() if k2 != "securityDeposit"}
        if fa != fb:
            other.append("fees")
        if other:
            bits.append("updated " + ", ".join(other))
        if bits:
            lines.append("%s: %s" % (name(b), "; ".join(bits)))
    return lines


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixtures")
    a = ap.parse_args()
    get = make_getter(a.fixtures)

    try:
        uids = parse_index(get("/listings"))
        parsed = [parse_detail(uid, get("/listings/detail/" + uid)) for uid in uids]
    except LayoutError as e:
        print("FAILED - nothing written: %s" % e)
        return 1

    # Sanity check: catch AppFolio typos (e.g. a $3,385 deposit entered as 338500) before they go live.
    bad = []
    for r in parsed:
        rent, dep, app_fee = r["rent"], r["fees"]["securityDeposit"], r["fees"]["applicationFee"]
        if not 500 <= rent <= 15000:
            bad.append("%s Unit %s rent $%s" % (r["property"], r["unit"], format(rent, ",")))
        if dep > max(3 * rent, 1000):
            bad.append("%s Unit %s security deposit $%s (rent is $%s)" % (r["property"], r["unit"], format(dep, ","), format(rent, ",")))
        if app_fee > 500:
            bad.append("%s Unit %s application fee $%s" % (r["property"], r["unit"], format(app_fee, ",")))
    if bad:
        print("FAILED - nothing written. These AppFolio values look like typos; fix them in AppFolio:")
        for b in bad:
            print("  - " + b)
        return 1

    current = json.load(open(LISTINGS, encoding="utf-8"))
    skipped, new = [], []
    for r in parsed:
        slug = PROPERTIES.get(r["property"])
        if not slug:
            skipped.append("%s Unit %s: property isn't on the website maps" % (r["property"], r["unit"]))
            continue
        if not has_floorplan(slug, r["unit"]):
            skipped.append("%s Unit %s: no floor plan/marker on the %s map (needs development work)" % (r["property"], r["unit"], slug))
            continue
        r["propertySlug"] = slug
        new.append({k: r[k] for k in KEY_ORDER})
    new.sort(key=lambda u: (u["propertySlug"], str(u["unit"]).zfill(6)))

    print("AppFolio lists %d unit(s); %d publishable." % (len(parsed), len(new)))
    for u in new:
        print("  %-14s Unit %-4s $%s  %s" % (u["property"], u["unit"], format(u["rent"], ","), u["status"]))
    for s in skipped:
        print("  SKIPPED  " + s)

    changes = describe_changes(current["listings"], new)
    if not changes and json.dumps(current["listings"], sort_keys=True) != json.dumps(new, sort_keys=True):
        changes = ["Listing text/order refreshed from AppFolio"]
    print("\nChanges:" if changes else "\nNo changes vs. what is published.")
    for c in changes:
        print("  - " + c)

    if changes and not a.dry_run:
        current["listings"] = new
        with open(LISTINGS, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
            f.write("\n")
        with open(CHANGES, "w", encoding="utf-8") as f:
            f.write("Daily sync: " + ("; ".join(changes) if len(changes) <= 2 else "%d listing changes" % len(changes)))
            f.write("\n\n" + "\n".join("- " + c for c in changes) + "\n")
    return 2 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
