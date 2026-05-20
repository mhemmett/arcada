"""
OOI M2M API data pull for aRCADA.
Handles all instruments served through the OOI REST API.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests
import xarray as xr

log = logging.getLogger(__name__)

OOI_BASE = "https://ooinet.oceanobservatories.org/api/m2m/12576/sensor/inv"

# stream name + delivery method for each instrument type.
# method is "streamed" for cabled real-time; "recovered_inst" for deep profiler WFP.
STREAM_MAP = {
    "pressure":        ("botpt_nano_sample",           "streamed"),
    "pressure_prest":  ("prest_real_time",             "streamed"),
    "ctd":             ("ctdpf_optode_sample",         "streamed"),
    "dissolved_oxygen":("do_stable_sample",            "streamed"),
    "ph":              ("phsen_data_record",           "streamed"),
    "fluorometer":     ("flort_d_data_record",         "streamed"),
    "nitrate":         ("nutnr_a_sample",              "streamed"),
    "adcp":            ("adcp_velocity_beam",          "streamed"),
    "velocimeter":     ("vel3d_b_sample",              "streamed"),
    "pco2":            ("pco2w_a_sami_data_record",    "streamed"),
    "hpies":           ("horizontal_electric_field",   "streamed"),
    "thermistor_array":("tmpsf_sample",                "streamed"),
    "thermistor":      ("trhph_sample",                "streamed"),
}

# Instrument class suffix → type override (for classes needing non-default streams)
CLASS_STREAM_OVERRIDES = {
    "PRESTA": ("prest_real_time",             "streamed"),
    "PRESTB": ("prest_real_time",             "streamed"),
    "BOTPTA": ("botpt_nano_sample",           "streamed"),
    "FLORDD": ("flord_d_data_record",         "streamed"),
    "FLORTD": ("flort_d_data_record",         "streamed"),
    "FLCDRA": ("flcd_r_dcl_instrument",       "recovered_inst"),
    "FLNTUA": ("flntu_a_dcl_instrument",      "recovered_inst"),
    "CTDPFL": ("ctdpf_optode_sample",         "recovered_inst"),
    "VEL3DA": ("vel3d_b_sample",              "recovered_inst"),
    "DOSTAD": ("do_stable_sample",            "streamed"),
    "DOFSTA": ("do_stable_sample",            "streamed"),
    "VADCPA": ("adcp_velocity_beam",          "streamed"),
    "VADCPB": ("adcp_velocity_beam",          "streamed"),
    "ADCPTD": ("adcp_velocity_beam",          "streamed"),
    "ADCPTE": ("adcp_velocity_beam",          "streamed"),
    "ADCPSK": ("adcp_velocity_beam",          "streamed"),
    "VEL3DB": ("vel3d_b_sample",              "streamed"),
    "VELPTD": ("velpt_velocity_data",         "streamed"),
    "THSPHA": ("thsph_a_dcl_instrument",      "streamed"),
    "TRHPHA": ("trhph_sample",                "streamed"),
    "TMPSFA": ("tmpsf_sample",                "streamed"),
}


def _get(url: str, auth: tuple[str, str] | None, **kwargs) -> requests.Response:
    for attempt in range(4):
        try:
            r = requests.get(url, auth=auth, timeout=60, **kwargs)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def _resolve_stream(instrument_cfg: dict) -> tuple[str, str]:
    """Return (stream_name, method) for this instrument config."""
    # Explicit stream in catalog entry takes priority
    if instrument_cfg.get("stream"):
        stream = instrument_cfg["stream"]
        # Detect method from instrument node prefix (DP = recovered)
        method = "recovered_inst" if instrument_cfg.get("node", "").startswith("DP") else "streamed"
        return stream, method

    # Check class-level override
    instr_code = instrument_cfg.get("instrument", "")
    cls = instr_code.split("-")[1][:6] if "-" in instr_code else ""
    if cls in CLASS_STREAM_OVERRIDES:
        return CLASS_STREAM_OVERRIDES[cls]

    # Fall back to type map
    itype = instrument_cfg["type"]
    if itype in STREAM_MAP:
        return STREAM_MAP[itype]

    raise ValueError(f"No stream defined for instrument type '{itype}' / class '{cls}'")


def check_data_availability(
    instrument_cfg: dict,
    start: datetime,
    end: datetime,
    auth: tuple[str, str] | None,
) -> dict:
    """
    Check if data exists for the requested time range by querying the metadata endpoint.
    Returns dict with: available (bool), coverage_start, coverage_end, record_count, note.
    """
    site   = instrument_cfg["site"]
    node   = instrument_cfg["node"]
    instr  = instrument_cfg["instrument"]

    url = f"{OOI_BASE}/{site}/{node}/{instr}/metadata"
    try:
        r = requests.get(url, auth=auth, timeout=20)
        r.raise_for_status()
        meta = r.json()
    except Exception as e:
        return {"available": None, "note": f"Could not fetch metadata: {e}"}

    try:
        stream_name, _ = _resolve_stream(instrument_cfg)
    except ValueError:
        stream_name = None

    # Find the best matching stream entry
    times = meta.get("times", [])
    good = [t for t in times if not t.get("method", "").startswith("bad_")]
    if stream_name:
        good = [t for t in good if t.get("stream") == stream_name] or good

    if not good:
        return {"available": False, "coverage_start": None, "coverage_end": None,
                "record_count": 0, "note": "No data streams found in catalog"}

    # Pick the stream with the most data
    best = max(good, key=lambda t: t.get("count", 0))
    cov_start = best.get("beginTime", "")
    cov_end   = best.get("endTime", "")
    count     = best.get("count", 0)

    # Check overlap
    req_start_s = start.isoformat()[:10]
    req_end_s   = end.isoformat()[:10]
    cov_start_s = cov_start[:10] if cov_start else ""
    cov_end_s   = cov_end[:10] if cov_end else ""

    if cov_end_s and req_start_s > cov_end_s:
        note = f"Request ({req_start_s}–{req_end_s}) is after data coverage ({cov_start_s}–{cov_end_s})"
        return {"available": False, "coverage_start": cov_start, "coverage_end": cov_end,
                "record_count": count, "note": note}

    if cov_start_s and req_end_s < cov_start_s:
        note = f"Request ({req_start_s}–{req_end_s}) is before data coverage ({cov_start_s}–{cov_end_s})"
        return {"available": False, "coverage_start": cov_start, "coverage_end": cov_end,
                "record_count": count, "note": note}

    return {"available": True, "coverage_start": cov_start, "coverage_end": cov_end,
            "record_count": count, "note": f"Data available {cov_start_s}–{cov_end_s}"}


class DataNotAvailableError(Exception):
    """Raised when data doesn't exist for the requested time range."""
    def __init__(self, instrument_id: str, avail: dict):
        self.instrument_id = instrument_id
        self.avail = avail
        super().__init__(avail.get("note", "No data available"))


def fetch_ooi_instrument(
    instrument_cfg: dict,
    start: datetime,
    end: datetime,
    out_dir: str,
    ooi_username: str = "",
    ooi_token: str = "",
) -> xr.Dataset:
    """
    Fetch one OOI API instrument for a time range.
    Returns an xarray Dataset. Raises DataNotAvailableError if no coverage.
    """
    site   = instrument_cfg["site"]
    node   = instrument_cfg["node"]
    instr  = instrument_cfg["instrument"]
    iid    = instrument_cfg["id"]

    auth = (ooi_username, ooi_token) if ooi_username else None

    # Check availability first
    avail = check_data_availability(instrument_cfg, start, end, auth)
    if avail["available"] is False:
        raise DataNotAvailableError(iid, avail)

    stream, method = _resolve_stream(instrument_cfg)
    log.info("Fetching %s / %s / %s  stream=%s", site, node, instr, stream)

    url = (
        f"{OOI_BASE}/{site}/{node}/{instr}/{method}/{stream}"
        f"?beginDT={start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
        f"&endDT={end.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
        f"&format=application/json&limit=20000"
    )

    r = _get(url, auth=auth)
    data = r.json()

    if not data:
        log.warning("OOI returned empty dataset for %s", iid)
        return xr.Dataset()

    if isinstance(data, dict) and "message" in data:
        raise RuntimeError(f"OOI API error for {iid}: {data}")

    df = pd.DataFrame(data)

    # Find time column
    time_col = next((c for c in ["time", "port_timestamp", "preferred_timestamp"] if c in df.columns), None)
    if time_col is None:
        log.warning("No time column in OOI response for %s", iid)
        return xr.Dataset()

    df["time"] = pd.to_datetime(df[time_col], unit="s", utc=True)
    df = df.set_index("time").sort_index()

    # Keep only numeric science columns; drop QC flags and internal cols
    drop_patterns = ["_qc_", "_qartod_", "driver_timestamp", "ingestion_timestamp",
                     "preferred_timestamp", "port_timestamp", "sensor_id", "provenance"]
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    science_cols = [c for c in numeric_cols if not any(p in c for p in drop_patterns)]
    df = df[science_cols]

    if df.empty:
        return xr.Dataset()

    ds = xr.Dataset.from_dataframe(df)
    ds.attrs.update({
        "instrument_id":   iid,
        "instrument_name": instrument_cfg["name"],
        "site":            site,
        "node":            node,
        "instrument":      instr,
        "stream":          stream,
        "method":          method,
        "latitude":        instrument_cfg.get("latitude"),
        "longitude":       instrument_cfg.get("longitude"),
        "depth_m":         instrument_cfg.get("depth_m"),
        "source":          "ooi_api",
        "data_coverage_start": avail.get("coverage_start", ""),
        "data_coverage_end":   avail.get("coverage_end", ""),
        "fetch_start":     start.isoformat(),
        "fetch_end":       end.isoformat(),
        "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
    })

    import os
    nc_path = os.path.join(out_dir, f"{iid.replace('/', '_')}.nc")
    ds.to_netcdf(nc_path)
    log.info("Saved NetCDF: %s (%d records)", nc_path, len(df))
    return ds


def check_data_gaps(ds: xr.Dataset, expected_freq_s: Optional[float] = None) -> list[dict]:
    """Identify gaps in a time series Dataset."""
    if "time" not in ds.dims or len(ds.time) < 2:
        return []

    times = pd.DatetimeIndex(ds.time.values)
    diffs = times[1:] - times[:-1]

    if expected_freq_s is None:
        expected_freq_s = float(np.median(diffs.total_seconds()))

    threshold = expected_freq_s * 5
    gaps = []
    for i, d in enumerate(diffs):
        if d.total_seconds() > threshold:
            gaps.append({
                "start":      times[i].isoformat(),
                "end":        times[i + 1].isoformat(),
                "duration_s": d.total_seconds(),
            })
    return gaps
