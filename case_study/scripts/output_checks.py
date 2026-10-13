"""Deterministic checks of a case-study run's output against a reference.

A run that reaches `status == "completed"` has only shown that the generated code
executed and that something was loaded. It says nothing about the content. The
first real run of this case study (2026-10-12, scenario 1) completed while 19,974
of 20,000 timestamps had day and month swapped, and was counted as a success.

Each function here takes what the pipeline wrote and the same inputs the pipeline
read, computes with plain pandas what the specification asks for, and returns the
facts of the comparison plus one verdict (`match` or `mismatch`). The rule behind
each verdict is stated in its function. Nothing is interpreted: what a mismatch
means for the report is the author's call.

No model is called and no value is corrected. The reference never guesses a date
format: timestamps are parsed as ISO 8601, which is what the published data uses.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

PICKUP = "tpep_pickup_datetime"
DROPOFF = "tpep_dropoff_datetime"

MATCH = "match"
MISMATCH = "mismatch"
NOT_CHECKED = "not_checked"


def _column(frame: pd.DataFrame, name: str) -> str | None:
    """The frame's own spelling of `name`, ignoring case: a destination may fold
    identifiers, and a difference in case is not a difference in content."""
    for column in frame.columns:
        if str(column).lower() == name.lower():
            return str(column)
    return None


def _timestamps(values: pd.Series) -> pd.Series:
    """Sorted timestamps of a column, read as ISO 8601 with no day-first guess."""
    parsed = pd.to_datetime(values, format="ISO8601", errors="coerce")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    return parsed.dropna().sort_values().reset_index(drop=True)


def _compare_timestamps(output: pd.Series | None, reference: pd.Series) -> dict[str, Any]:
    expected = _timestamps(reference)
    facts: dict[str, Any] = {
        "present": output is not None,
        "equal_to_reference": False,
        "reference_min": str(expected.min()) if len(expected) else None,
        "reference_max": str(expected.max()) if len(expected) else None,
    }
    if output is None:
        return facts
    actual = _timestamps(output)
    facts["unparsed_values"] = int(len(output) - len(actual))
    facts["min"] = str(actual.min()) if len(actual) else None
    facts["max"] = str(actual.max()) if len(actual) else None
    facts["equal_to_reference"] = len(actual) == len(expected) and bool((actual == expected).all())
    return facts


def reference_scenario1(trips: pd.DataFrame) -> pd.DataFrame:
    kept = trips[(trips["fare_amount"] > 0) & (trips["trip_distance"] > 0)]
    return kept.drop_duplicates()


def check_scenario1(output: pd.DataFrame, trips: pd.DataFrame) -> dict[str, Any]:
    """`match` when the output has the reference's row count, carries the two renamed
    columns in place of the original ones, and both hold exactly the reference's
    timestamps."""
    reference = reference_scenario1(trips)
    pickup, dropoff = _column(output, "pickup_at"), _column(output, "dropoff_at")
    facts: dict[str, Any] = {
        "rows": len(output),
        "reference_rows": len(reference),
        "original_columns_still_present": sorted({PICKUP, DROPOFF} & set(map(str, output.columns))),
        "pickup_at": _compare_timestamps(output[pickup] if pickup else None, reference[PICKUP]),
        "dropoff_at": _compare_timestamps(output[dropoff] if dropoff else None, reference[DROPOFF]),
    }
    matches = (
        facts["rows"] == facts["reference_rows"]
        and not facts["original_columns_still_present"]
        and facts["pickup_at"]["equal_to_reference"]
        and facts["dropoff_at"]["equal_to_reference"]
    )
    return {"verdict": MATCH if matches else MISMATCH, "facts": facts}


def reference_scenario2(trips: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    joined = trips[trips["fare_amount"] > 0].merge(
        zones, left_on="PULocationID", right_on="LocationID"
    )
    return (
        joined.groupby(["Borough", "Zone"], dropna=False)
        .agg(trips=("total_amount", "size"), avg_total_amount=("total_amount", "mean"))
        .reset_index()
    )


def _groups(
    frame: pd.DataFrame, borough: str, zone: str, trips: str, average: str
) -> tuple[dict[tuple[str, str], tuple[int, float]], int, int]:
    """Groups keyed by (Borough, Zone), plus the count and the trips of the groups
    whose key has a null in it."""
    keyed: dict[tuple[str, str], tuple[int, float]] = {}
    null_key_groups = null_key_trips = 0
    for row in frame.itertuples(index=False):
        values = row._asdict()
        if pd.isna(values[borough]) or pd.isna(values[zone]):
            null_key_groups += 1
            null_key_trips += int(values[trips])
            continue
        keyed[(str(values[borough]), str(values[zone]))] = (
            int(values[trips]),
            float(values[average]),
        )
    return keyed, null_key_groups, null_key_trips


def check_scenario2(
    output: pd.DataFrame, trips: pd.DataFrame, zones: pd.DataFrame
) -> dict[str, Any]:
    """`match` when every (Borough, Zone) group of the reference whose key has no
    null is in the output with the same trip count and the same average, and the
    output has no other group with a non-null key.

    Groups with a null in the key are reported and not judged. The zone table as
    published has two such zones; SQL's GROUP BY keeps them, pandas' `groupby`
    drops them unless told otherwise, and the specification does not say which.
    """
    reference = reference_scenario2(trips, zones)
    names = {
        name: _column(output, name) for name in ("Borough", "Zone", "trips", "avg_total_amount")
    }
    missing = sorted(name for name, found in names.items() if found is None)
    expected, expected_null_groups, expected_null_trips = _groups(
        reference, "Borough", "Zone", "trips", "avg_total_amount"
    )
    facts: dict[str, Any] = {
        "missing_columns": missing,
        "groups": len(output),
        "reference_groups": len(reference),
        "reference_groups_with_null_key": expected_null_groups,
        "reference_trips": int(reference["trips"].sum()),
        "reference_trips_in_null_key_groups": expected_null_trips,
    }
    if missing:
        return {"verdict": MISMATCH, "facts": facts}

    renamed = output.rename(columns={found: name for name, found in names.items() if found})
    actual, actual_null_groups, actual_null_trips = _groups(
        renamed, "Borough", "Zone", "trips", "avg_total_amount"
    )
    shared = expected.keys() & actual.keys()
    facts.update(
        {
            "trips": int(renamed["trips"].sum()),
            "groups_with_null_key": actual_null_groups,
            "trips_in_null_key_groups": actual_null_trips,
            "groups_missing": len(expected.keys() - actual.keys()),
            "groups_not_in_reference": len(actual.keys() - expected.keys()),
            "groups_with_different_trips": sum(
                1 for key in shared if expected[key][0] != actual[key][0]
            ),
            "groups_with_different_average": sum(
                1
                for key in shared
                if not math.isclose(expected[key][1], actual[key][1], rel_tol=1e-9, abs_tol=1e-6)
            ),
        }
    )
    matches = not (
        facts["groups_missing"]
        or facts["groups_not_in_reference"]
        or facts["groups_with_different_trips"]
        or facts["groups_with_different_average"]
    )
    return {"verdict": MATCH if matches else MISMATCH, "facts": facts}


def reference_scenario3(trips: pd.DataFrame, zones: pd.DataFrame) -> pd.DataFrame:
    return trips[trips["fare_amount"] > 0].merge(
        zones, left_on="PULocationID", right_on="LocationID"
    )


def weather_from_api_payload(payload: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Open-Meteo's `daily` block as {date: (temperature_2m_max, precipitation_sum)}."""
    daily = payload["daily"]
    return {
        str(day): (float(temperature), float(precipitation))
        for day, temperature, precipitation in zip(
            daily["time"], daily["temperature_2m_max"], daily["precipitation_sum"], strict=True
        )
    }


def check_scenario3(
    output: pd.DataFrame,
    trips: pd.DataFrame,
    zones: pd.DataFrame,
    weather: dict[str, tuple[float, float]] | None,
) -> dict[str, Any]:
    """`match` when the output has the reference's row count, its pickup column
    holds exactly the reference's timestamps, and every row carries the weather of
    its own pickup date as the API reports it.

    `weather` is the API's answer fetched again by the caller. When it could not be
    fetched (`None`), the weather values cannot be judged: the facts record that
    and the verdict rests on the row count, the timestamps and on each date
    carrying one single, non-null pair of values.
    """
    reference = reference_scenario3(trips, zones)
    pickup = _column(output, PICKUP)
    temperature, precipitation = _column(output, "max_temp_c"), _column(output, "precipitation_mm")
    facts: dict[str, Any] = {
        "rows": len(output),
        "reference_rows": len(reference),
        "pickup": _compare_timestamps(output[pickup] if pickup else None, reference[PICKUP]),
        "missing_columns": sorted(
            name
            for name, found in (("max_temp_c", temperature), ("precipitation_mm", precipitation))
            if found is None
        ),
        "weather_reference": "fetched" if weather is not None else "unavailable",
    }
    if facts["missing_columns"] or pickup is None:
        return {"verdict": MISMATCH, "facts": facts}
    assert temperature is not None and precipitation is not None

    days = pd.to_datetime(output[pickup], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d")
    pairs = pd.DataFrame(
        {"day": days, "temperature": output[temperature], "precipitation": output[precipitation]}
    )
    facts["rows_without_weather"] = int(
        (pairs["temperature"].isna() | pairs["precipitation"].isna()).sum()
    )
    by_day = {
        str(day): sorted(
            {
                (float(t), float(p))
                for t, p in zip(g["temperature"], g["precipitation"], strict=True)
            }
        )
        for day, g in pairs.dropna().groupby("day")
    }
    facts["weather_by_pickup_date"] = {day: values for day, values in sorted(by_day.items())}
    facts["dates_with_more_than_one_value"] = sum(
        1 for values in by_day.values() if len(values) > 1
    )
    if weather is not None:
        facts["dates_with_weather_different_from_api"] = sum(
            1
            for day, values in by_day.items()
            if day not in weather
            or len(values) != 1
            or not all(
                math.isclose(got, want, abs_tol=1e-6)
                for got, want in zip(values[0], weather[day], strict=True)
            )
        )
    matches = (
        facts["rows"] == facts["reference_rows"]
        and facts["pickup"]["equal_to_reference"]
        and facts["rows_without_weather"] == 0
        and facts["dates_with_more_than_one_value"] == 0
        and not facts.get("dates_with_weather_different_from_api")
    )
    return {"verdict": MATCH if matches else MISMATCH, "facts": facts}
