"""Offline tests for news.py. Fixtures are trimmed copies of the live pages as
they looked on Nov 19 2026 (OHA's table is the browser-rendered version; the
Timothy Lake row's date is moved to 11/2 so a 'lifted' row falls inside the
window). Run: python -m pytest tests -q   (or: python tests/test_news.py)"""
import datetime as dt
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import news  # noqa: E402

FX = HERE / "fixtures"


def titles(items):
    return [i["title"] for i in items]


def test_filter_examples():
    keep = news.is_angler_news
    assert keep("Free fishing weekend June 6-7")
    assert keep("Gear restrictions extended on Chetco, Winchuck rivers")
    assert keep("Two rods allowed in Willamette River June 3-July 31")
    assert keep("Bag limits lifted for Thief Valley, Pilcher Creek, and Wolf Creek reservoirs starting June 1")
    assert not keep("Fish and Wildlife Commission met virtually Oct. 9")
    assert not keep("ODFW seeks applicants for Fish Screening and Fish Passage Task Forces")
    assert not keep("Commercial crab season begins Dec. 16 from Cape Falcon to the California border")
    assert not keep("Be BearWise: the black bear fall eat-a-thon is on")


def test_odfw_news():
    got = news.odfw_news((FX / "odfw_news.html").read_text())
    assert titles(got) == [
        "One steelhead bag limit on Umatilla River below Threemile Dam",
        "Chetco and Winchuck anglers can stop bobbing around after ODFW lifts gear restrictions",
        "UPDATE: Leaburg Hatchery closed to public access through Nov. 20",
        "Free fishing weekend June 6-7",
    ]
    assert got[1]["url"] == ("https://myodfw.com/news/chetco-and-winchuck-anglers-can-stop-"
                             "bobbing-around-after-odfw-lifts-gear-restrictions")
    assert got[2]["date"] == "2026-11-13"


def test_odfw_report_skips_banner():
    got = news.odfw_report((FX / "odfw_report.html").read_text())
    assert len(got) == 1 and got[0]["date"] == "2026-11-13"
    assert got[0]["kind"] == "report"


def test_oha():
    got = news.oha_algae((FX / "oha.html").read_text())
    t = titles(got)
    assert "Toxic algae advisory: Detroit Reservoir" in t
    assert t.count("Toxic algae advisory: Detroit Reservoir") == 2      # 11/16 and 6/12 differ
    assert t.count("Toxic algae precaution: Siltcoos Lake") == 1         # two counties, one item
    assert t.count("Toxic algae advisory lifted: Thief Valley Reservoir") == 1
    assert "Toxic algae precaution: Eckman Lake" in t                    # name split by spans
    assert not any("Umpqua" in x for x in t)                             # permanent, undated
    det = next(i for i in got if i["date"] == "2026-11-16")
    assert det["url"].startswith("https://content.govdelivery.com/")
    tim = next(i for i in got if "Timothy" in i["title"])
    assert tim["url"] == news.OHA_PAGE


def test_wdfw_news():
    got = news.wdfw_news((FX / "wdfw_rss.xml").read_text())
    assert titles(got) == [
        "WDFW approves five days of razor clam digs at Copalis and Mocrocks beaches beginning Tuesday, Nov. 24",
        "Get outdoors on Black Friday with trout fishing opportunities",
    ]
    assert got[0]["date"] == "2026-11-17"


def test_wa_names():
    n = news.wa_water_name
    assert n("BALLINGER LK (SNOH)") == "Ballinger Lake"
    assert n("SILVER LK (SNOH) T28") == "Silver Lake"
    assert n("CRANBERRY CR 05.0390") == "Cranberry Creek"
    assert n("LK ALICE (KING)") == "Lake Alice"
    assert n("GISSBURG PD NORTH (SNOH)") == "Gissburg Pond North"
    assert n("BEAVER LKS (KING) T24") == "Beaver Lakes"
    assert n("FIO RITO LK-N (KITT)") == "Fio Rito Lake North"


def test_wdfw_plants():
    got = news.wdfw_plants((FX / "wdfw_plants.html").read_text())
    assert [i["date"] for i in got] == ["2026-11-17", "2026-11-16", "2026-11-10"]
    assert got[0]["title"] == "3,382 trout stocked at Ballinger Lake, Gissburg Pond North and 2 more waters"
    assert got[1]["title"] == "4,286 trout stocked at Beaver Lakes, Cranberry Creek and Green Lake"
    assert got[2]["title"] == "150 trout stocked at Lake Alice"


def test_build_window_and_order(tmp_path):
    data = news.build(dt.date(2026, 11, 19), FX, None)
    assert all(v.startswith("ok") for v in data["sources"].values()), data["sources"]
    dates = [i["date"] for i in data["items"]]
    assert "2026-05-26" not in dates and "2026-06-12" not in dates and "2026-07-08" not in dates
    orr = [i for i in data["items"] if i["state"] == "OR"]
    assert orr[0]["title"] == "Toxic algae advisory: Detroit Reservoir"   # newest first
    assert [i["date"] for i in orr] == sorted([i["date"] for i in orr], reverse=True)
    json.dumps(data)


def test_failed_source_keeps_previous(tmp_path):
    first = news.build(dt.date(2026, 11, 19), FX, None)
    broken = tmp_path / "fx"
    broken.mkdir()
    for f in FX.iterdir():
        (broken / f.name).write_text(f.read_text())
    (broken / "wdfw_plants.html").write_text("<html>maintenance</html>")
    second = news.build(dt.date(2026, 11, 19), broken, first)
    assert second["sources"]["wdfw_plants"].startswith("error")
    assert [i for i in second["items"] if i["kind"] == "stocking"] == \
           [i for i in first["items"] if i["kind"] == "stocking"]


if __name__ == "__main__":
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if "tmp_path" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(Path(tempfile.mkdtemp()))
            else:
                fn()
            print("ok", name)
