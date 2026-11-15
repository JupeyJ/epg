"""Build a UK-only EPG: your provider's channels, with listings and logos from open-epg.com.

Run on GitHub Actions (reads login from secrets) or locally:
  python build_epg.py                          -> downloads both guides (needs env vars below)
  python build_epg.py provider.xml openepg.xml -> uses local files instead

Env vars: XTREAM_HOST (e.g. http://example.com:80), XTREAM_USER, XTREAM_PASS
Output:   uk.xml
"""
import gzip
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict

OPEN_EPG_URL = "https://www.open-epg.com/files/unitedkingdom.xml"
KEEP_PREFIXES = ("UK:",)
OUTPUT = "uk.xml"

# Provider ID -> open-epg ID, where they differ beyond upper/lower case.
ALIASES = {
    "BBC1.uk": "BBCOne.uk", "bbc1.uk": "BBCOne.uk",
    "BBC2.uk": "BBCTwo.uk", "BBC2NI.uk": "BBCTwoNI.uk",
    "TS": "BBCThree.uk", "BBC4.uk": "BBCFour.uk",
    "BBCOneEastMidland.uk": "BBCOneEM.uk", "BBCOneOxford.uk": "BBCOneSouth.uk",
    "BBC1Yorkshire.uk": "BBCOneYorks.uk", "BBCOneSouthEast.uk": "BBCOneSE.uk",
    "BBCOneWestMidland.uk": "BBCOneWM.uk", "BBCOneNorthWest.uk": "BBCOneNW.uk",
    "BBCOneNorthernIreland.uk": "BBCOneNI.uk",
    "ITV1.uk": "ITV1London.uk", "ITVGranada.uk": "ITV1Granada.uk",
    "ITVWestcountry.uk": "ITV1WestCountry.uk", "ITVCentral.uk": "ITV1Central.uk",
    "ITVWales.uk": "ITV1Wales.uk", "UTV.uk": "ITV1UTV.uk",
    "Channel5.uk": "5.uk", "FiveUSA.uk": "5USA.uk",
    "Alibi.uk": "UandAlibi.uk", "Eden.uk": "UandEden.uk", "GOLD.uk": "UandGold.uk",
    "Dave.uk": "UandDave.uk", "W.uk": "UandW.uk", "Drama.uk": "UandDrama.uk",
    "Yesterday.uk": "UandYesterday.uk",
    "DisneyJunior.uk": "DisneyJr.uk", "NickJunior.uk": "NickJr.uk",
    "DiscoveryDMAX.uk": "DMAX.uk", "InvestigationDiscovery.uk": "ID.uk",
    "NatGeo.uk": "NationalGeographic.uk", "HistoryChannel.uk": "SkyHistory.uk",
    "CrimeInvestigation.uk": "CrimeandInvestigation.uk",
    "PlaneteJustice.fr": "CrimeandInvestigation.uk",
    "TheJewelryChannel.uk": "TJC.uk", "Now90s.uk": "Now90s00s.uk",
    "LoveWorld.us": "LoveWorld.uk", "NewVision.uk": "NewVisionTV.uk",
    "HighStreet1.uk": "HighStreetTV1.uk", "HighStreet2.uk": "HighStreetTV2.uk",
    "HighStreet3.uk": "HighStreetTV3.uk", "HighStreet4.uk": "HighStreetTV4.uk",
    "TalkTV.uk": "Talk.uk", "TalkingPictures.uk": "TalkingPicturesTV.uk",
    "RacingUK.uk": "RacingTV.uk",
    "TNTSport1.uk": "TNTSports1.uk", "TNTSport2.uk": "TNTSports2.uk",
    "TNTSport3.uk": "TNTSports3.uk", "TNTSport4.uk": "TNTSports4.uk",
    "SkySportsArena.uk": "SkySportsPlus.uk",
    "SkySportsPremiereLeague.uk": "SkySportsPremierLeague.uk",
    "skysportspremiereleague.uk": "SkySportsPremierLeague.uk",
    "skyaction.uk": "SkyCinemaAction.uk",
    "SkyCinemaSciFiHorror.uk": "SkyCinemaSciFiandHorror.uk",
    "PremierSports1.ie": "PremierSports1.uk", "PremierSports2.ie": "PremierSports2.uk",
    "StarGold.in": "UtsavGold.uk", "StarUtsav.in": "UtsavPlus.uk",
    "SonyMax.in": "SonyMax.uk", "Sony.in": "SonyTV.uk",
}

# ---- channel name tidying -------------------------------------------------
KEEP_UPPER = {"BBC", "ITV", "CBBC", "UK", "USA", "HD", "SD", "4K", "UHD", "FHD", "TV", "TNT",
              "ESPN", "PPV", "TCM", "AMC", "MTV", "CBS", "TJC", "JML", "DMAX", "UTV", "CITV",
              "HGTV", "WWE", "DAZN", "RT", "NI", "ARY", "GB", "QVC", "E4", "S4C", "LBC", "CNN",
              "CNBC", "TLC", "BET", "F1", "EFL", "EPL", "SPFL", "RAW", "XD", "ID", "PL", "IE",
              "EU", "US"}
SMALL = {"and", "of", "the", "in"}
SUPERSCRIPT = {"ᴴᴰ": "HD", "ᴿᴬᵂ": "RAW", "ᵁᴴᴰ": "UHD", "ᶠᴴᴰ": "FHD"}


def tidy(name):
    n = name
    for k, v in SUPERSCRIPT.items():
        n = n.replace(k, v)
    n = re.sub(r"^\s*UK\s*:+\s*", "", n)
    n = n.replace("⚽", "O").replace("◉", "")
    n = re.sub(r"\bHEVC\b", "", n)
    n = re.sub(r"\bDOCUMENTRY\b", "DOCUMENTARY", n, flags=re.I)
    n = n.replace("&", " & ")
    words = []
    for i, w in enumerate(n.split()):
        u = w.upper()
        if re.fullmatch(r"[0-9]+S", u):
            words.append(u.lower())
        elif u in KEEP_UPPER or re.fullmatch(r"[0-9]+[A-Z]?|\+1", u):
            words.append(u)
        elif i and w.lower() in SMALL:
            words.append(w.lower())
        else:
            words.append(w[:1].upper() + w[1:].lower())
    n = re.sub(r"\s+\+\s*1\b", " +1", " ".join(words))
    return n.strip() or name.strip()


# ---- helpers ---------------------------------------------------------------
def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if b"<tv" not in data[:2000]:
        raise SystemExit(f"Download from {url.split('?')[0]} did not return an XMLTV guide.")
    with open(dest, "wb") as f:
        f.write(data)


def main():
    if len(sys.argv) >= 3:
        provider_xml, open_xml = sys.argv[1], sys.argv[2]
    else:
        host = os.environ["XTREAM_HOST"].rstrip("/")
        user, pw = os.environ["XTREAM_USER"], os.environ["XTREAM_PASS"]
        provider_xml, open_xml = "provider.xml", "openepg.xml"
        print("Downloading provider EPG...")
        fetch(f"{host}/xmltv.php?username={user}&password={pw}", provider_xml)
        print("Downloading open-epg UK guide...")
        fetch(OPEN_EPG_URL, open_xml)

    # open-epg: channels (for logos) and listings
    open_logo, open_by_lower, open_progs = {}, {}, {}
    for _, el in ET.iterparse(open_xml):
        if el.tag == "channel":
            oid = el.get("id")
            open_by_lower[oid.lower()] = oid
            icon = el.find("icon")
            if icon is not None and icon.get("src"):
                open_logo[oid] = icon.get("src")
        elif el.tag == "programme":
            open_progs.setdefault(el.get("channel"), []).append(el)

    # provider: keep UK channels only (stream through the big file)
    channels, my_ids, my_progs = [], OrderedDict(), {}
    for _, el in ET.iterparse(provider_xml):
        if el.tag == "channel":
            cid = el.get("id", "")
            name = (el.findtext("display-name") or "").strip()
            if cid and name.startswith(KEEP_PREFIXES):
                channels.append(el)
                my_ids[cid] = None
            else:
                el.clear()
        elif el.tag == "programme":
            if el.get("channel") in my_ids:
                my_progs.setdefault(el.get("channel"), []).append(el)
            else:
                el.clear()

    link = {}
    for mid in my_ids:
        oid = ALIASES.get(mid) or open_by_lower.get(mid.lower())
        if oid and (open_progs.get(oid) or oid in open_logo):
            link[mid] = oid

    stats = Counter()
    with open(OUTPUT, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="utf-8" ?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
        out.write('<tv generator-info-name="UK + open-epg">\n')
        for c in channels:
            for dn in c.findall("display-name"):
                dn.text = tidy(dn.text or "")
            oid = link.get(c.get("id"))
            if oid in open_logo:
                for ic in c.findall("icon"):
                    c.remove(ic)
                ET.SubElement(c, "icon", src=open_logo[oid])
                stats["logos from open-epg"] += 1
            out.write(ET.tostring(c, encoding="unicode").strip() + "\n")
        for mid in my_ids:
            oid = link.get(mid)
            if oid and open_progs.get(oid):
                src = open_progs[oid]
                stats["channel IDs with open-epg listings"] += 1
            else:
                src = my_progs.get(mid, [])
                stats["channel IDs with provider listings" if src else "channel IDs with no listings"] += 1
            for p in src:
                q = ET.Element("programme", dict(p.attrib))
                q.set("channel", mid)
                q.extend(list(p))
                out.write(ET.tostring(q, encoding="unicode").strip() + "\n")
                stats["programmes"] += 1
        out.write("</tv>\n")

    print(f"Wrote {OUTPUT}: {len(channels)} channels")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
