#!/usr/bin/env python3
"""
Rewrites the availability data inside the three published site files from source/listings.json.

  python3 scripts/sync_listings.py                 # uses today's date as LAST_SYNCED
  python3 scripts/sync_listings.py --date 2026-10-23
  python3 scripts/sync_listings.py --dry-run       # validate + print summary, write nothing

Files patched in place (inside site-files/):
  mowbray-sightmap-standalone.html                 -> SYNC block (REAL_DATA keyed by bare unit number)
  tributary-50-sightmap-standalone.html            -> SYNC block (REAL_DATA keyed by quoted unit number)
  dial-residential-homepage-availability-widget.html -> SYNC block (LISTINGS) + IMG floor-plan entries

Floor-plan photos for the homepage widget are copied automatically out of the matching sightmap file
(each sightmap already contains a floor plan for every unit), so no manual image work is needed.
"""
import argparse, datetime, json, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site-files")
FILES = {
    "mowbray":   os.path.join(SITE, "mowbray-sightmap-standalone.html"),
    "tributary": os.path.join(SITE, "tributary-50-sightmap-standalone.html"),
    "home":      os.path.join(SITE, "dial-residential-homepage-availability-widget.html"),
}
BASE = "https://dialresidential.appfolio.com/listings"

def js(v):
    """Python value -> JS literal with unquoted object keys (matches the existing files)."""
    if v is None: return "null"
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return json.dumps(v)
    if isinstance(v, list): return "[" + ",".join(js(x) for x in v) + "]"
    if isinstance(v, dict): return "{" + ",".join("%s:%s" % (k, js(x)) for k, x in v.items()) + "}"
    return json.dumps(v, ensure_ascii=False)

def urls(u):
    uid = u["uid"]
    return {
        "detailUrl":  "%s/detail/%s" % (BASE, uid),
        "contactUrl": "%s/detail/%s/contact_us_form" % (BASE, uid),
        "tourUrl":    "%s/showings/new?listable_uid=%s&source=Website" % (BASE, uid),
        "applyUrl":   "%s/rental_applications/new?listable_uid=%s&source=Website" % (BASE, uid),
    }

REQUIRED = ["property","propertySlug","unit","uid","beds","baths","sqft","rent","status","matterportUrl",
            "description","amenities","appliances","petPolicy","fees"]

def validate(listings):
    seen = set()
    for u in listings:
        miss = [k for k in REQUIRED if k not in u]
        if miss: sys.exit("Listing %s is missing fields: %s" % (u.get("unit"), miss))
        if u["propertySlug"] not in ("mowbray", "tributary"):
            sys.exit("propertySlug must be 'mowbray' or 'tributary' (got %r)" % u["propertySlug"])
        key = (u["propertySlug"], str(u["unit"]))
        if key in seen: sys.exit("Duplicate listing %s" % (key,))
        seen.add(key)
        if not isinstance(u["rent"], int) or u["rent"] <= 0: sys.exit("rent must be a whole number: %r" % u["rent"])
        if not re.fullmatch(r"[0-9a-f-]{36}", u["uid"]): sys.exit("uid looks wrong for unit %s: %r" % (u["unit"], u["uid"]))
        for k in ("applicationFee", "securityDeposit"):
            if k not in u["fees"]: sys.exit("fees.%s missing for unit %s" % (k, u["unit"]))

def sightmap_block(slug, listings, date):
    entries = []
    for u in sorted([x for x in listings if x["propertySlug"] == slug], key=lambda x: int(x["unit"])):
        key = str(u["unit"]) if slug == "mowbray" else '"%s"' % u["unit"]
        un = urls(u)
        entries.append(
            '    %s: { rent:%d, status:%s, matterportUrl:%s,\n'
            '         detailUrl:%s,\n         contactUrl:%s,\n         tourUrl:%s,\n         applyUrl:%s,\n'
            '         description:%s,\n         amenities:%s,\n         appliances:%s,\n         petPolicy:%s,\n'
            '         fees:%s }' % (key, u["rent"], js(u["status"]), js(u["matterportUrl"]),
                js(un["detailUrl"]), js(un["contactUrl"]), js(un["tourUrl"]), js(un["applyUrl"]),
                js(u["description"]), js(u["amenities"]), js(u["appliances"]), js(u["petPolicy"]), js(u["fees"])))
    return ('// SYNC:BEGIN\n  var REAL_DATA = {\n%s\n  };\n  var LAST_SYNCED = "%s";\n  // SYNC:END'
            % (",\n".join(entries), date))

def home_block(listings, date):
    ordered = sorted(listings, key=lambda x: (x["property"], x["rent"]))
    entries = []
    for u in ordered:
        un = urls(u)
        f = ["property:%s" % js(u["property"]), "propertySlug:%s" % js(u["propertySlug"]), "unit:%s" % js(str(u["unit"])),
             "beds:%d" % u["beds"], "baths:%d" % u["baths"], "sqft:%d" % u["sqft"], "rent:%d" % u["rent"],
             "status:%s" % js(u["status"]), "plan:%s" % js("%s-%s" % (u["propertySlug"], u["unit"])),
             "matterportUrl:%s" % js(u["matterportUrl"]),
             "contactUrl:%s" % js(un["contactUrl"]), "tourUrl:%s" % js(un["tourUrl"]), "applyUrl:%s" % js(un["applyUrl"]),
             "description:%s" % js(u["description"]), "petPolicy:%s" % js(u["petPolicy"]),
             "amenities:%s" % js(u["amenities"]), "appliances:%s" % js(u["appliances"]), "fees:%s" % js(u["fees"])]
        entries.append("    { " + ", ".join(f) + " }")
    return ('// SYNC:BEGIN\n  var LISTINGS = [\n%s\n  ];\n  var LAST_SYNCED = "%s";\n  // SYNC:END'
            % (",\n".join(entries), date))

def replace_sync(text, block, name):
    b = text.find("// SYNC:BEGIN"); e = text.find("// SYNC:END")
    if b < 0 or e < 0: sys.exit("SYNC markers not found in %s" % name)
    return text[:b] + block + text[e + len("// SYNC:END"):]

def unit_image(sightmap_text, unit):
    m = re.search(r'"unit-%s\.jpg":\s*"(data:image/[^"]+)"' % re.escape(str(unit)), sightmap_text)
    return m.group(1) if m else None

def patch_img_map(home_text, listings, sightmaps):
    m = re.search(r"var IMG = \{\n(.*?)\n  \};", home_text, re.S)
    if not m: sys.exit("IMG map not found in homepage widget")
    existing = dict(re.findall(r'\n?\s+"([^"]+)":\s*"([^"]*)"', m.group(1)))
    img = {k: v for k, v in existing.items() if k in ("mowbray", "tributary")}   # keep property exterior photos
    if set(img) != {"mowbray", "tributary"}: sys.exit("Property exterior photos missing from homepage IMG map")
    for u in listings:
        data = unit_image(sightmaps[u["propertySlug"]], u["unit"])
        if not data: sys.exit("No floor-plan image 'unit-%s.jpg' found in the %s sightmap" % (u["unit"], u["propertySlug"]))
        img["%s-%s" % (u["propertySlug"], u["unit"])] = data
    body = ",\n".join('    "%s": "%s"' % (k, v) for k, v in img.items())
    return home_text[:m.start(1)] + body + home_text[m.end(1):]

def syntax_check(label, text):
    node = shutil.which("node")
    if not node:
        print("  (node not found - skipped JS syntax check for %s)" % label); return True
    scripts = re.findall(r"<script>(.*?)</script>", text, re.S)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write("\n".join(scripts)); path = f.name
    r = subprocess.run([node, "--check", path], capture_output=True, text=True)
    os.unlink(path)
    if r.returncode: print("  SYNTAX ERROR in %s:\n%s" % (label, r.stderr)); return False
    return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = json.load(open(os.path.join(ROOT, "source", "listings.json"), encoding="utf-8"))
    listings = data["listings"]; validate(listings)
    texts = {k: open(p, encoding="utf-8").read() for k, p in FILES.items()}
    new = dict(texts)
    for slug in ("mowbray", "tributary"):
        new[slug] = replace_sync(texts[slug], sightmap_block(slug, listings, a.date), slug)
    h = replace_sync(texts["home"], home_block(listings, a.date), "homepage")
    new["home"] = patch_img_map(h, listings, texts)
    ok = all(syntax_check(k, new[k]) for k in new)
    if not ok: sys.exit("Aborting: nothing written.")
    print("Listings (%d):" % len(listings))
    for u in sorted(listings, key=lambda x: (x["property"], int(x["unit"]))):
        print("  %-14s Unit %-4s $%s  %s" % (u["property"], u["unit"], format(u["rent"], ","), u["status"]))
    norm = lambda t: re.sub(r'LAST_SYNCED = "[^"]*"', 'LAST_SYNCED = ""', t)
    changed = [k for k in FILES if norm(new[k]) != norm(texts[k])]
    NAMES = {"mowbray": "Mowbray sightmap  (mowbray-manor-sightmap)",
             "tributary": "Tributary sightmap (tributary-50-sightmap)",
             "home": "Homepage widget    (comfy-hummingbird-0c3074)"}
    if not changed:
        print("\nNO CONTENT CHANGES - nothing to write or deploy (saves Netlify credits).")
        return
    if a.dry_run:
        print("\nDry run - no files written. Would need deploy: " + ", ".join(changed)); return
    for k in changed:
        open(FILES[k], "w", encoding="utf-8").write(new[k])
    print("\nUpdated files (LAST_SYNCED = %s). DEPLOY THESE ONLY:" % a.date)
    for k in changed:
        print("  - %s  <- site-files/%s" % (NAMES[k], os.path.basename(FILES[k])))

if __name__ == "__main__":
    main()
