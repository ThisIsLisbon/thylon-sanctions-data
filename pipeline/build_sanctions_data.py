#!/usr/bin/env python3
"""Thylon Sanktionslisten-Check: data build.

Downloads the official sanctions lists server-side (GitHub Actions), normalises
them into a common record format, shards them into static JSON and writes a
manifest. The browser does the matching; no visitor data ever reaches a server.

Sources (all official, all free):
  EU  - Consolidated Financial Sanctions List (FSF), XML 1.1, public token
  UN  - UN Security Council Consolidated List, XML
  UK  - UK Sanctions List (FCDO), XML (OGL v3.0 - attribution kept in manifest)
  US  - Consolidated Screening List (trade.gov), JSON (includes OFAC SDN/non-SDN,
        BIS Entity List, DDTC debarred parties and more)
  TW  - Taiwan MOEA SHTC Entity List, CSV (best effort - column names configurable)

Usage:
  python build_sanctions_data.py --out ../site/data/sanctions
  python build_sanctions_data.py --fixture tests/fixture.json --out /tmp/out   # offline test
"""
import argparse, csv, datetime, hashlib, io, json, os, re, sys, unicodedata, xml.etree.ElementTree as ET
from collections import defaultdict

try:
    import requests
except ImportError:  # fixture mode works without requests
    requests = None

SOURCES = {
    "EU": {"name": "EU Consolidated Financial Sanctions List (FSF)",
           "url": "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw",
           "home": "https://webgate.ec.europa.eu/fsd/fsf"},
    "UN": {"name": "UN Security Council Consolidated List",
           "url": "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
           "home": "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list"},
    "UK": {"name": "UK Sanctions List (FCDO) - Open Government Licence v3.0",
           "url": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml",
           "home": "https://www.gov.uk/government/publications/the-uk-sanctions-list"},
    "US": {"name": "US Consolidated Screening List (trade.gov)",
           "url": "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json",
           "home": "https://www.trade.gov/consolidated-screening-list"},
    "TW": {"name": "Taiwan MOEA International Trade Administration - SHTC Entity List",
           "url": os.environ.get("TW_SHTC_CSV_URL", ""),
           "home": "https://www.trade.gov.tw/Pages/Detail.aspx?nodeID=4433&pid=725764"},
}
REC_SHARDS = 32
MIN_RATIO_VS_PREVIOUS = 0.8   # fail if a list shrinks by more than 20 %

# ---------------------------------------------------------------- normalisation (mirrors assets/sanctions-match.js)
CYR = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i","й":"y","к":"k","л":"l",
       "м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh",
       "щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya","і":"i","ї":"i","є":"e","ґ":"g"}
LEGAL = {"gmbh","ag","ug","kg","ohg","se","ltd","limited","llc","inc","corp","corporation","co","plc","sa","sas",
         "sarl","srl","spa","bv","nv","oy","ab","as","oao","ooo","zao","pao","ao","pjsc","jsc","ojsc","cjsc","pte",
         "pty","llp","lp","company","the","of","und","and","de","del","la","le"}

def translit(s):
    return "".join(CYR.get(c.lower(), c) for c in s)

def _strip(x):
    x = unicodedata.normalize("NFKD", x)
    x = "".join(c for c in x if not unicodedata.combining(c)).replace("ß", "ss")
    x = re.sub(r"[^a-z0-9]+", " ", x)
    return re.sub(r"\s+", " ", x).strip()

def normalize_variants(s):
    base = translit(s or "").lower()
    ger = base.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    a, b = _strip(base), _strip(ger)
    return [a] if a == b else [a, b]

def tokens(norm):
    return [t for t in norm.split(" ") if t and t not in LEGAL]

# ---------------------------------------------------------------- helpers
def local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag

def txt(el):
    return (el.text or "").strip() if el is not None else ""

def mk(rid_src, list_id, regime, act, listed, typ, names, dob, countries, url):
    names = [n.strip() for n in names if n and n.strip()]
    seen, uniq = set(), []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen.add(k); uniq.append(n)
    norm, nmap = [], []
    for i, n in enumerate(uniq[:40]):
        for v in normalize_variants(n):
            if v and v not in norm:
                norm.append(v); nmap.append(i)
    if not norm:
        return None
    return {"id": "%s:%s" % (list_id.lower(), rid_src), "list": list_id, "regime": regime or "", "act": act or "",
            "listed": (listed or "")[:10], "type": typ or "", "names": uniq[:40], "norm": norm[:60], "nmap": nmap[:60],
            "dob": sorted(set(d for d in dob if d))[:6], "countries": sorted(set(c for c in countries if c))[:8],
            "url": url or ""}

# ---------------------------------------------------------------- parsers
def parse_eu(data):
    recs = []
    root = ET.fromstring(data)
    for ent in root.iter():
        if local(ent.tag) != "sanctionEntity":
            continue
        rid = ent.get("logicalId") or ent.get("euReferenceNumber") or str(len(recs))
        names, dob, cc, typ, regime, act, listed = [], [], [], "", "", "", ""
        for ch in ent:
            t = local(ch.tag)
            if t == "nameAlias":
                w = ch.get("wholeName") or " ".join(x for x in [ch.get("firstName"), ch.get("middleName"), ch.get("lastName")] if x)
                if w: names.append(w)
            elif t == "birthdate":
                y, m, d = ch.get("year"), ch.get("monthOfYear"), ch.get("dayOfMonth")
                if y: dob.append("-".join(p for p in [y, m, d] if p))
            elif t == "citizenship":
                if ch.get("countryIso2Code"): cc.append(ch.get("countryIso2Code"))
            elif t == "subjectType":
                typ = {"person": "Person", "enterprise": "Unternehmen", "vessel": "Schiff", "aircraft": "Luftfahrzeug"}.get(ch.get("code", ""), ch.get("code", ""))
            elif t == "regulation":
                regime = ch.get("programme") or regime
                act = ch.get("numberTitle") or act
                listed = ch.get("entryIntoForceDate") or ch.get("publicationDate") or listed
        r = mk(rid, "EU", regime, act, listed, typ, names, dob, cc, "https://webgate.ec.europa.eu/fsd/fsf#!/files")
        if r: recs.append(r)
    return recs

def parse_un(data):
    recs = []
    root = ET.fromstring(data)
    for ind in root.iter():
        t = local(ind.tag)
        if t not in ("INDIVIDUAL", "ENTITY"):
            continue
        get = lambda name: txt(next((c for c in ind if local(c.tag) == name), None))
        full = " ".join(x for x in [get("FIRST_NAME"), get("SECOND_NAME"), get("THIRD_NAME"), get("FOURTH_NAME")] if x)
        names, dob, cc = [full], [], []
        for c in ind:
            lt = local(c.tag)
            if lt in ("INDIVIDUAL_ALIAS", "ENTITY_ALIAS"):
                a = txt(next((x for x in c if local(x.tag) == "ALIAS_NAME"), None))
                if a: names.append(a)
            elif lt == "INDIVIDUAL_DATE_OF_BIRTH":
                y = txt(next((x for x in c if local(x.tag) == "YEAR"), None)) or txt(next((x for x in c if local(x.tag) == "DATE"), None))
                if y: dob.append(y[:10])
            elif lt == "NATIONALITY":
                v = txt(next((x for x in c if local(x.tag) == "VALUE"), None))
                if v: cc.append(v)
        r = mk(get("DATAID") or get("REFERENCE_NUMBER") or str(len(recs)), "UN", get("UN_LIST_TYPE"), get("REFERENCE_NUMBER"),
               get("LISTED_ON"), "Person" if t == "INDIVIDUAL" else "Unternehmen", names, dob, cc,
               "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list")
        if r: recs.append(r)
    return recs

def parse_uk(data):
    recs = []
    root = ET.fromstring(data)
    for des in root.iter():
        if local(des.tag) != "Designation":
            continue
        get = lambda name: txt(next((c for c in des if local(c.tag) == name), None))
        names, dob, cc, typ = [], [], [], ""
        for el in des.iter():
            lt = local(el.tag)
            if lt == "Name":
                parts = [txt(x) for x in el if local(x.tag).startswith("Name") and local(x.tag) != "NameType"]
                parts = [p for p in parts if p]
                if parts: names.append(" ".join(parts))
            elif lt == "DOB" and txt(el):
                dob.append(txt(el)[:10])
            elif lt in ("Nationality", "Country") and txt(el):
                cc.append(txt(el))
            elif lt == "RegimeName":
                pass
        typ_raw = get("IndividualEntityShip") or ""
        typ = {"Individual": "Person", "Entity": "Unternehmen", "Ship": "Schiff"}.get(typ_raw, typ_raw)
        r = mk(get("UniqueID") or str(len(recs)), "UK", get("RegimeName"), get("UKSanctionsListDate") and "UK Sanctions List" or "",
               get("DateDesignated") or get("LastUpdated"), typ, names, dob, cc,
               "https://www.gov.uk/government/publications/the-uk-sanctions-list")
        if r: recs.append(r)
    return recs

def parse_us(data):
    recs = []
    obj = json.loads(data)
    for i, e in enumerate(obj.get("results", [])):
        names = [e.get("name", "")] + list(e.get("alt_names") or [])
        dob = [d for d in (e.get("dates_of_birth") or []) if d]
        cc = [c for c in (e.get("nationalities") or []) + (e.get("citizenships") or []) if c]
        typ = {"Individual": "Person", "Entity": "Unternehmen", "Vessel": "Schiff", "Aircraft": "Luftfahrzeug"}.get(e.get("type") or "", e.get("type") or "")
        r = mk(e.get("id") or e.get("entity_number") or str(i), "US", e.get("source", ""), ", ".join(e.get("programs") or []),
               e.get("start_date") or "", typ, names, dob, cc, e.get("source_list_url") or e.get("source_information_url") or "")
        if r: recs.append(r)
    return recs

def parse_tw(data):
    recs = []
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    reader = csv.DictReader(io.StringIO(text))
    name_cols = [c for c in os.environ.get("TW_NAME_COLS", "Name,Entity Name,Entity,名稱,實體名稱").split(",") if c]
    for i, row in enumerate(reader):
        names = [row[c] for c in name_cols if c in row and row[c]]
        if not names:
            continue
        country = next((row[c] for c in ("Country", "國家", "Country/Region") if c in row and row[c]), "")
        r = mk(str(i), "TW", "SHTC Entity List", "Foreign Trade Act Art. 13", "", "Unternehmen", names, [], [country] if country else [],
               SOURCES["TW"]["home"])
        if r: recs.append(r)
    return recs

PARSERS = {"EU": parse_eu, "UN": parse_un, "UK": parse_uk, "US": parse_us, "TW": parse_tw}

# ---------------------------------------------------------------- fetch
def fetch(url):
    if requests is None:
        raise RuntimeError("requests not installed")
    r = requests.get(url, timeout=180, headers={"User-Agent": "thylon-sanctions-check/1.0 (+https://thylon.com)"})
    r.raise_for_status()
    return r.content

# ---------------------------------------------------------------- build
def build(records, out_dir, sources_meta, previous):
    os.makedirs(out_dir, exist_ok=True)
    # validation against previous manifest
    counts = defaultdict(int)
    for r in records: counts[r["list"]] += 1
    if previous:
        for s in previous.get("sources", []):
            prev = s.get("records", 0); now = counts.get(s["id"], 0)
            if prev and now < prev * MIN_RATIO_VS_PREVIOUS and not os.environ.get("FORCE_BUILD"):
                raise SystemExit("ABORT: list %s shrank from %d to %d records (>20%%). Set FORCE_BUILD=1 to override." % (s["id"], prev, now))
    idx = defaultdict(lambda: defaultdict(set))
    shards = defaultdict(dict)
    for rid, r in enumerate(records):
        shards[rid % REC_SHARDS][rid] = r
        for n in r["norm"]:
            for t in tokens(n):
                key = t[0] if re.match(r"[a-z0-9]", t[0]) else "_"
                idx[key][t].add(rid)
    # wipe old shards
    for f in os.listdir(out_dir):
        if f.startswith(("idx_", "rec_")) and f.endswith(".json"):
            os.remove(os.path.join(out_dir, f))
    for key, toks in idx.items():
        with open(os.path.join(out_dir, "idx_%s.json" % key), "w", encoding="utf-8") as fh:
            json.dump({t: sorted(ids) for t, ids in toks.items()}, fh, ensure_ascii=False, separators=(",", ":"))
    for n, recs in shards.items():
        with open(os.path.join(out_dir, "rec_%d.json" % n), "w", encoding="utf-8") as fh:
            json.dump(recs, fh, ensure_ascii=False, separators=(",", ":"))
    manifest = {
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recordCount": len(records), "recShards": REC_SHARDS, "idxKeys": sorted(idx.keys()),
        "sources": [dict(s, records=counts.get(s["id"], 0)) for s in sources_meta],
        "attribution": "Contains public sector information licensed under the Open Government Licence v3.0 (UK Sanctions List).",
        "disclaimer": "Informational tool. Not legal advice. Only the lists published in the Official Journal of the EU and by the competent authorities are binding.",
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    return manifest

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture", help="JSON file with raw records (offline test mode)")
    ap.add_argument("--only", help="comma-separated list ids to build, e.g. EU,UN")
    a = ap.parse_args()
    prev_path = os.path.join(a.out, "manifest.json")
    previous = json.load(open(prev_path)) if os.path.exists(prev_path) else None
    records, meta = [], []
    if a.fixture:
        raw = json.load(open(a.fixture, encoding="utf-8"))
        for r in raw:
            rec = mk(r["id"], r["list"], r.get("regime"), r.get("act"), r.get("listed"), r.get("type"), r["names"],
                     r.get("dob", []), r.get("countries", []), r.get("url"))
            if rec: records.append(rec)
        meta = [{"id": k, "name": "FIXTURE " + k, "url": "", "fetched": "fixture"} for k in sorted(set(r["list"] for r in records))]
        previous = None
    else:
        wanted = (a.only or "EU,UN,UK,US,TW").split(",")
        for lid in wanted:
            src = SOURCES[lid]
            if not src["url"]:
                print("skip", lid, "(no URL configured)"); continue
            try:
                data = fetch(src["url"])
                recs = PARSERS[lid](data)
                if not recs:
                    raise RuntimeError("parser returned 0 records")
                records.extend(recs)
                meta.append({"id": lid, "name": src["name"], "url": src["home"],
                             "fetched": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             "sha256": hashlib.sha256(data).hexdigest()[:16]})
                print("ok  ", lid, len(recs), "records")
            except Exception as e:
                if lid == "TW":
                    print("warn", lid, "skipped:", e); continue
                raise SystemExit("FAIL %s: %s" % (lid, e))
    m = build(records, a.out, meta, previous)
    print("manifest:", m["recordCount"], "records across", [s["id"] for s in m["sources"]])

if __name__ == "__main__":
    main()
