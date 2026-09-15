import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import requests
from config import TOKEN_URL, INFO_URL

_cached_token: Optional[str] = None
_token_expires_at: float = 0

_cached_info: Optional[Dict[str, Any]] = None
_info_fetched_at: float = 0

def parse_iso_expiry(expires_at_str: str) -> float:
    try:
        clean_str = expires_at_str.strip()
        if "." in clean_str:
            base, frac = clean_str.split(".", 1)
            tz_part = ""
            for tz_symbol in ("+", "-", "Z"):
                if tz_symbol in frac:
                    idx = frac.index(tz_symbol)
                    tz_part = frac[idx:]
                    frac = frac[:idx]
                    break
            frac = frac[:6]
            clean_str = f"{base}.{frac}{tz_part}"

        dt = datetime.fromisoformat(clean_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return dt.timestamp()
    except Exception as e:
        print(f"[AUTH] ISO parse error ({expires_at_str}): {e}, defaulting to 3600s cache")
        return time.time() + 3600

def get_token(force_refresh: bool = False) -> str:
    global _cached_token, _token_expires_at

    now = time.time()
    if not force_refresh and _cached_token and now < (_token_expires_at - 60):
        return _cached_token

    print(f"[AUTH] Requesting new token from: {TOKEN_URL}")
    try:
        r = requests.get(TOKEN_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        if _cached_token:
            print(f"[AUTH] Token fetch failed ({e}), using last cached token")
            return _cached_token
        raise RuntimeError(f"Failed to fetch token from {TOKEN_URL}: {e}")

    token = data.get("token")
    if not token:
        raise RuntimeError(f"'token' field not found in response: {data}")

    expires_at_raw = data.get("expires_at")
    if expires_at_raw:
        if isinstance(expires_at_raw, (int, float)):
            _token_expires_at = float(expires_at_raw)
        else:
            _token_expires_at = parse_iso_expiry(str(expires_at_raw))
    else:
        _token_expires_at = now + 3600

    _cached_token = token
    print(f"[AUTH] New token acquired. Valid until: {time.ctime(_token_expires_at)}")
    return _cached_token

def get_info(force_refresh: bool = False) -> Dict[str, Any]:
    global _cached_info, _info_fetched_at

    now = time.time()
    if not force_refresh and _cached_info and (now - _info_fetched_at < 86400):
        return _cached_info

    print(f"[AUTH] Requesting device info from: {INFO_URL}")
    try:
        r = requests.get(INFO_URL, timeout=10)
        r.raise_for_status()
        info = r.json()
    except Exception as e:
        if _cached_info:
            print(f"[AUTH] Info fetch failed ({e}), using cached info")
            return _cached_info
        raise RuntimeError(f"Failed to fetch info from {INFO_URL}: {e}")

    if not info.get("serialNumber"):
        raise RuntimeError(f"'serialNumber' not found in info response: {info}")

    _cached_info = info
    _info_fetched_at = now
    print(f"[AUTH] Device info cached: serialNumber={info.get('serialNumber')}, truckId={info.get('truckId')}")
    return _cached_info

def get_serial_number() -> str:
    info = get_info()
    return str(info.get("serialNumber", ""))
