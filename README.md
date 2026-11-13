# thylon-sanctions-data

Data feed for the Thylon Sanktionslisten-Check. A GitHub Actions job (`.github/workflows/sanctions-data.yml`)
runs `pipeline/build_sanctions_data.py` every 3 hours, fetches the official EU / UN / UK / US (trade.gov CSL)
lists (Taiwan optional via secret), and commits static JSON to `data/sanctions/`. GitHub Pages serves that
folder cross-origin to thylon.com.

Setup (one-time)
1. Settings -> Actions -> General -> Workflow permissions: "Read and write permissions" -> Save.
2. Settings -> Pages -> Build and deployment -> Source: "Deploy from a branch" -> Branch: main, folder: / (root) -> Save.
3. Actions -> "sanctions-data" -> Run workflow. Wait for the green check (2-5 minutes).
4. Verify: data/sanctions/manifest.json exists in the repo, then open
   https://<your-github-user-or-org>.github.io/thylon-sanctions-data/data/sanctions/manifest.json
   (Pages can take a few minutes after the commit). It must show JSON with "recordCount" and "sources".
5. The data URL for the website is that address without "manifest.json":
   https://<user>.github.io/thylon-sanctions-data/data/sanctions/

Optional: custom subdomain. Add a CNAME file with data.thylon.com, point DNS CNAME data -> <user>.github.io,
then the data URL becomes https://data.thylon.com/data/sanctions/
