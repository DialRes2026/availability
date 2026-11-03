# Dial Residential – availability widgets

Live availability for dialresidential.com, published with GitHub Pages:

- `site-files/mowbray-sightmap-standalone.html`: Mowbray Manor site map
- `site-files/tributary-50-sightmap-standalone.html`: Tributary 50 site map
- `site-files/dial-residential-homepage-availability-widget.html`: homepage carousel

`source/listings.json` is the source of truth. To update the files, edit it and run `python3 scripts/sync_listings.py`, then commit and push. Everything here is public listing data from AppFolio. **Never commit credentials or tenant information.**
