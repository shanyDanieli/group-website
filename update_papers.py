#!/usr/bin/env python3
"""Sync the Publications page of index.html with ADS.

    python3 update_papers.py            # edit index.html in place
    python3 update_papers.py --dry-run  # only report what would change

Rules:
  * Include refereed journal articles and arXiv preprints from the ADS query below.
  * Skip anything in EXCLUDE.
  * Danieli in author positions 1-3 -> "First, second, and third-author" list;
    otherwise -> "Additional publications".
  * When ADS has merged a listed preprint into its published version, the entry is
    switched to the journal reference, keeping its number.
  * New papers go at the top of their list with the next number; totals sentence updated.
Needs an ADS API token in ~/.ads/dev_key. Never commits or pushes.
"""
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

INDEX = Path(__file__).with_name("index.html")
TOKEN = (Path.home() / ".ads" / "dev_key").read_text().strip()
API = "https://api.adsabs.harvard.edu/v1/search/query"
QUERY = {
    "q": 'author:"Danieli, Shany" year:2010-2030',
    "fq": "{!type=aqp v=$fq_database}",
    "fq_database": "(database:astronomy OR database:physics)",
    "sort": "date desc,bibcode desc",
}
FIELDS = "bibcode,title,author,doctype,property,pub,volume,page,page_range,year,date,identifier"
MAX_AUTHORS = 12
PUB_SHORT = {"Publications of the Astronomical Society of the Pacific": "PASP"}

# Bibcodes deliberately left off the site.
EXCLUDE = {
    "2023arXiv230611784H",  # NANCY community white paper
    "2022arXiv220802781B",  # Rubin LSST community white paper
}


def ads_search(**params):
    params = {**params, "fl": FIELDS, "rows": 2000}
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)["response"]["docs"]


def wanted(doc):
    if doc["bibcode"] in EXCLUDE:
        return False
    if doc.get("doctype") == "eprint":
        return True
    return doc.get("doctype") == "article" and "REFEREED" in doc.get("property", [])


def danieli_pos(doc):
    for i, a in enumerate(doc["author"], 1):
        if a.startswith("Danieli, S"):
            return i


def fmt_author(name):
    if name.startswith("Danieli, S"):
        return "<b>Danieli S.</b>"
    last, _, first = name.partition(",")
    last, first = last.strip(), first.strip()
    if last and not last[0].isascii():  # ADS lowercases some accented initials, e.g. "çatmabacak"
        last = last[0].upper() + last[1:]
    return html.escape(f"{last} {first[0]}." if first else last, quote=False)


def fmt_authors(doc):
    names = [fmt_author(a) for a in doc["author"]]
    if len(names) > MAX_AUTHORS:
        names = names[:MAX_AUTHORS] + ["et al."]
    return ", ".join(names)


def fmt_title(doc):
    t = html.unescape(doc["title"][0]).replace("$", "")  # drop LaTeX math delimiters
    t = html.escape(t, quote=False)
    # ADS marks sub/superscripts with literal tags; keep them, lowercased.
    return re.sub(r"&lt;(/?)(sub|sup)&gt;", lambda m: f"<{m[1]}{m[2].lower()}>", t, flags=re.I)


def arxiv_id(doc):
    for ident in doc.get("identifier", []):
        if ident.startswith("arXiv:"):
            return ident
    m = re.match(r"\d{4}arXiv(\d{4})(\d{5})", doc["bibcode"])
    if m:
        return f"arXiv:{m[1]}.{m[2]}"


def fmt_venue(doc):
    if doc["doctype"] == "eprint":
        return f"{arxiv_id(doc) or doc['bibcode']} ({doc['year']})"
    page = doc.get("page_range") or (doc.get("page") or [""])[0]
    pub = PUB_SHORT.get(doc["pub"], doc["pub"])
    parts = [html.escape(pub, quote=False), doc.get("volume"), page]
    return ", ".join(p for p in parts if p) + f" ({doc['year']})"


def entry_html(doc, num):
    return (
        f'      <div class="pub"><div class="yr">{doc["year"]}</div><div>\n'
        f'        <h3>{num}. <a href="https://ui.adsabs.harvard.edu/abs/{doc["bibcode"]}" '
        f'target="_blank" rel="noopener">{fmt_title(doc)}</a></h3>\n'
        f'        <div class="authors">{fmt_authors(doc)}</div>\n'
        f'        <div class="venue">{fmt_venue(doc)}</div>\n'
        f"      </div></div>\n"
    )


ENTRY_RE = re.compile(
    r'      <div class="pub"><div class="yr">\d+</div><div>\n'
    r'        <h3>(\d+)\. <a href="https://ui\.adsabs\.harvard\.edu/abs/([^"]+)".*?</div></div>\n',
    re.S,
)


def main():
    dry = "--dry-run" in sys.argv
    src = INDEX.read_text()
    split = src.index('<h3 class="subhead">Additional publications</h3>')
    head, tail = src[:split], src[split:]

    on_site = {m[2]: m for m in ENTRY_RE.finditer(src)}
    docs = [d for d in ads_search(**QUERY) if wanted(d)]
    ads = {d["bibcode"]: d for d in docs}
    changes = []

    # Preprints on the site that ADS has since merged into a published record.
    for bib in [b for b in on_site if "arXiv" in b and b not in ads]:
        ident = arxiv_id({"bibcode": bib}) or bib
        found = [d for d in ads_search(q=f'identifier:"{ident}"') if wanted(d) and "arXiv" not in d["bibcode"]]
        if not found:
            changes.append(f"WARNING: {bib} is on the site but no longer in ADS results; left as is")
            continue
        new = found[0]
        old = on_site[bib]
        rep = entry_html(new, old[1])
        head, tail = head.replace(old[0], rep), tail.replace(old[0], rep)
        on_site[new["bibcode"]] = old
        changes.append(f"Published: #{old[1]} {bib} -> {new['bibcode']} ({new['pub']})")

    # New papers, oldest first so the newest ends up on top.
    for doc in sorted((d for d in docs if d["bibcode"] not in on_site), key=lambda d: d["date"]):
        lead = (danieli_pos(doc) or 99) <= 3
        section = head if lead else tail
        nums = [int(n) for n in re.findall(r"<h3>(\d+)\. <a", section)]
        num = max(nums, default=0) + 1
        first = ENTRY_RE.search(section)
        section = section[: first.start()] + entry_html(doc, num) + section[first.start():]
        if lead:
            head = section
        else:
            tail = section
        changes.append(f"New ({'lead' if lead else 'additional'} #{num}): {doc['bibcode']}  {doc['title'][0][:70]}")

    out = head + tail
    n_lead = len(re.findall(r"<h3>\d+\. <a", head))
    n_add = len(re.findall(r"<h3>\d+\. <a", tail))
    out = re.sub(
        r"\d+ publications in total — \d+ as first, second, or third author, and \d+ additional co-authored papers\.",
        f"{n_lead + n_add} publications in total — {n_lead} as first, second, or third author, "
        f"and {n_add} additional co-authored papers.",
        out,
    )

    if not changes:
        print("Publications are up to date.")
        return
    print("\n".join(changes))
    print(f"Totals: {n_lead + n_add} ({n_lead} lead, {n_add} additional)")
    if dry:
        print("(dry run: index.html not modified)")
    else:
        INDEX.write_text(out)
        print("index.html updated. Review with: git diff")


if __name__ == "__main__":
    main()
