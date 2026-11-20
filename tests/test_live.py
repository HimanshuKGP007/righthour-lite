import datetime as dt
import json

import pandas as pd
import pytest

from righthour import data as D
from righthour import live


def _hist(days=30):
    h = D.synthetic_history(days=days)
    h.attrs["scale"] = 1.5
    return h


def test_split_holds_back_last_days_and_keeps_attrs():
    h = _hist()
    vis, fut = live.split_for_live(h, 7)
    assert len(fut) == 7 and all(len(f) == 96 for f in fut)
    assert len(D.full_days(vis)) == 23
    assert vis["ts"].max() < fut[0]["ts"].min()
    assert fut[0]["ts"].iloc[0] < fut[-1]["ts"].iloc[0]
    assert vis.attrs["scale"] == 1.5 and fut[3].attrs["scale"] == 1.5


def test_split_needs_enough_days():
    with pytest.raises(ValueError):
        live.split_for_live(_hist(days=20), 7)


def test_release_next_day_until_empty():
    vis, fut = live.split_for_live(_hist(), 3)
    h2, rest, day = live.release_next_day(vis, fut)
    assert day == fut[0]["ts"].iloc[0].date() and len(rest) == 2
    assert len(h2) == len(vis) + 96 and h2.attrs["scale"] == 1.5
    h3, rest, _ = live.release_next_day(h2, rest)
    h4, rest, _ = live.release_next_day(h3, rest)
    with pytest.raises(IndexError):
        live.release_next_day(h4, rest)


def test_merge_new_wins_and_counts():
    h = _hist(days=2)
    new = h.tail(4).copy()
    new["kw"] = 999.0
    extra = pd.DataFrame({"ts": [h["ts"].max() + pd.Timedelta(minutes=15)], "kw": [5.0], "pf": [0.9]})
    merged, n_new = live.merge_readings(h, pd.concat([new, extra], ignore_index=True))
    assert n_new == 1 and len(merged) == len(h) + 1
    assert (merged.tail(5).iloc[:4]["kw"] == 999.0).all()
    assert merged["ts"].is_monotonic_increasing and merged.attrs == h.attrs


def _write_csv(path, stamps):
    rows = "date,Usage_kWh,Lagging_Current_Power_Factor\n" + "".join(f"{s},4.0,90\n" for s in stamps)
    path.write_text(rows)


def test_scan_folder_new_changed_and_bad(tmp_path):
    _write_csv(tmp_path / "a.csv", ["01/03/2018 00:15", "01/03/2018 00:30"])
    (tmp_path / "bad.csv").write_text("nothing,useful\n1,2\n")
    frames, seen, errs = live.scan_folder(tmp_path, {}, None)
    assert [n for n, _ in frames] == ["a.csv"] and len(frames[0][1]) == 2
    assert len(errs) == 1 and errs[0].startswith("bad.csv")
    frames2, seen2, errs2 = live.scan_folder(tmp_path, seen, None)
    assert frames2 == [] and seen2 == seen and len(errs2) == 0
    assert live.scan_folder(tmp_path / "missing", {}, None) == ([], {}, [])


def test_is_plan_due():
    t = dt.time(18, 0)
    assert not live.is_plan_due(dt.datetime(2026, 11, 20, 17, 59), t, None)
    assert live.is_plan_due(dt.datetime(2026, 11, 20, 18, 0), t, None)
    assert live.is_plan_due(dt.datetime(2026, 11, 21, 7, 0), dt.time(6, 30), dt.date(2026, 11, 20))
    assert not live.is_plan_due(dt.datetime(2026, 11, 20, 19, 0), t, dt.date(2026, 11, 20))


def test_describe_feed_is_json_safe():
    d = live.describe_feed(_hist(days=3), now=dt.datetime(2026, 11, 21, 0, 0))
    json.dumps(d)
    assert d["n_full_days"] == 3 and d["n_rows"] == 288 and d["hours_behind"] > 0
