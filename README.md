# Dial Residential – availability widgets

Live availability for dialresidential.com, published with GitHub Pages:

- `site-files/mowbray-sightmap-standalone.html`: Mowbray Manor site map
- `site-files/tributary-50-sightmap-standalone.html`: Tributary 50 site map
- `site-files/dial-residential-homepage-availability-widget.html`: homepage carousel

`source/listings.json` is the source of truth.

**Automatic daily sync:** the `Sync listings` workflow (`.github/workflows/sync-listings.yml`) runs every morning at about 6am Eastern. It reads AppFolio's public listings (`scripts/fetch_appfolio.py`), updates `listings.json`, rebuilds the widgets (`scripts/sync_listings.py`), commits and publishes. On days with no changes it does nothing. You can also run it any time from the **Actions** tab with **Run workflow**.

If a run fails, GitHub emails the account owner and the website keeps showing the last good data. A failure means one of these:
- AppFolio couldn't be read (page redesign or blocked). Nothing was changed.
- A unit can't go on the maps (a new unit with no floor plan, or another property). Everything else was still published.

For a manual update, edit `listings.json`, run `python3 scripts/sync_listings.py`, then commit and push. Any push to `main` publishes. Everything here is public listing data from AppFolio. **Never commit credentials or tenant information.**
