# Thylon Sanktionslisten-Check — data pipeline

Builds the static JSON the on-page check uses. Runs in GitHub Actions every 3 hours
(see .github/workflows/sanctions-data.yml) and commits to `data/sanctions/` in the site repo.

Hosting the data (recommended: separate repo + GitHub Pages)
- Create a repo `thylon-sanctions-data` from this folder (pipeline at root, data committed to `data/sanctions/`).
- Settings -> Pages -> "Deploy from a branch" -> main, root. GitHub Pages serves the JSON with
  `Access-Control-Allow-Origin: *`, so the site (on Lovable) can fetch it cross-origin.
- Data URL = https://<org>.github.io/thylon-sanctions-data/data/sanctions/ (or CNAME data.thylon.com).
  Put that URL into the German page's `data-data-url` attribute (see LOVABLE_TASKS.md, Task 3).
- Each 3-hourly commit updates the published JSON automatically; the website never needs re-publishing.

Setup
1. Put this folder at `pipeline/` in the repository that deploys thylon.com (the site root must
   contain `assets/` and `data/`).
2. Enable Actions. The workflow needs `contents: write` (already declared).
3. Optional: add a repo secret `TW_SHTC_CSV_URL` with the Taiwan MOEA SHTC entity-list CSV URL and,
   if needed, `TW_NAME_COLS` with the name column headers. Without it, Taiwan is skipped (logged).
4. Run the workflow once manually ("Run workflow"). After it commits, deploy — the check goes live.

Local test without network:
    python build_sanctions_data.py --fixture tests/fixture.json --out /tmp/sanctions

Safety: the build aborts if any list shrinks by more than 20 % versus the previous manifest
(FORCE_BUILD=1 overrides). Parsers for EU FSF XML 1.1, UN XML, UK Sanctions List XML and the
trade.gov CSL JSON are written against the published schemas; verify the first live run's counts
in manifest.json against the sources' own figures.

Licences: EU (Commission reuse policy), UN (public), UK (OGL v3.0 — attribution kept in manifest),
US (public domain). OpenSanctions is deliberately NOT used: its bulk data needs a commercial licence.
