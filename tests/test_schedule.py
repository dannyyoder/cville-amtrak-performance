import io
import zipfile
from datetime import date

from collector import schedule as S


def gtfs_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text.strip() + "\n")
    return buf.getvalue()


FEED = gtfs_zip({
    "feed_info.txt": "feed_publisher_name,feed_version\nAmtrak,20261012",
    "routes.txt": """route_id,route_long_name,route_type
36923,Crescent,2
88,Northeast Regional,2
42954,Amtrak Thruway Connecting Service,3""",
    "stops.txt": """stop_id,stop_name,stop_timezone
NOL,New Orleans,America/Chicago
CVS,Charlottesville,America/New_York
WAS,Washington,America/New_York
NYP,New York,America/New_York
RNK,Roanoke,America/New_York""",
    "calendar.txt": """service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
DAILY,1,1,1,1,1,1,1,20261011,20271011
WKDY,1,1,1,1,1,0,0,20261012,20261016
SAT,0,0,0,0,0,1,0,20261011,20271011""",
    "trips.txt": """route_id,service_id,trip_id,trip_short_name,trip_headsign
36923,DAILY,t20,20,New York
88,WKDY,t1151,1151,Roanoke
88,SAT,t66,66,Boston
42954,DAILY,bus,4050,Richmond""",
    "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,drop_off_type
t20,10:10:00,10:10:00,NOL,1,0,1
t20,34:54:00,34:59:00,CVS,2,0,0
t20,37:47:00,38:35:00,WAS,3,0,0
t20,42:42:00,42:42:00,NYP,4,1,0
t1151,7:00:00,7:30:00,WAS,1,0,0
t1151,9:59:00,10:07:00,CVS,2,0,0
t1151,12:40:00,12:40:00,RNK,3,0,0
t66,18:51:00,19:01:00,CVS,1,0,0
t66,21:28:00,21:38:00,WAS,2,1,0
bus,15:45:00,15:45:00,CVS,1,0,0""",
})


def sched():
    return S.extract(FEED, today=date(2026, 10, 12))


def test_extract_keeps_rail_trips_through_cvs_and_folds_1xxx_numbers():
    s = sched()
    assert s["feed_version"] == "20261012" and (s["start"], s["end"]) == ("2026-10-11", "2027-10-11")
    assert sorted((t["train"], t["number"]) for t in s["trips"]) == [(20, "20"), (66, "66"), (151, "1151")]
    t20 = next(t for t in s["trips"] if t["train"] == 20)
    assert t20["route"] == "Crescent" and t20["days"] == "1111111"
    # agency (Eastern) minutes past service-day midnight, Central offset, pickup/drop-off flags
    assert t20["stops"][0] == ["NOL", 610, 610, -60, 1, 0]
    assert t20["stops"][1] == ["CVS", 34 * 60 + 54, 34 * 60 + 59, 0, 1, 1]


def test_options_from_new_orleans():
    s = sched()
    [o] = S.options(s, "NOL", 1, date(2026, 10, 13))
    assert (o["train"], o["dep"], o["arr"], o["days_later"]) == (20, 9 * 60 + 10, 10 * 60 + 54, 1)


def test_options_cvs_departure_uses_previous_service_day():
    s = sched()
    # CVS -> WAS on Wed Oct 14: the 10:59 AM Crescent belongs to Tuesday's service date (34:59 in GTFS);
    # 1151 runs WAS -> CVS and 66 only runs Saturdays, so only train 20
    opts = S.options(s, "WAS", 0, date(2026, 10, 14))
    assert [(o["train"], o["dep"], o["arr"], o["dur"]) for o in opts] == [(20, 10 * 60 + 59, 13 * 60 + 47, 168)]


def test_options_respect_days_dates_and_pickup_rules():
    s = sched()
    assert [o["train"] for o in S.options(s, "WAS", 1, date(2026, 10, 14))] == [151]   # weekday-only trip
    assert S.options(s, "WAS", 1, date(2026, 10, 19)) == []                            # its dates have ended
    assert [o["train"] for o in S.options(s, "WAS", 0, date(2026, 10, 17))] == [20, 66]  # Saturday adds 66
    assert S.options(s, "NYP", 1, date(2026, 10, 14)) == []                            # NYP is not a boarding stop here
    assert S.options(s, "RNK", 1, date(2026, 10, 14)) == []                            # 1151 goes the other way


def test_covers():
    s = sched()
    assert S.covers(s, date(2027, 10, 11)) and not S.covers(s, date(2027, 10, 12)) and not S.covers(None, date(2026, 1, 1))
