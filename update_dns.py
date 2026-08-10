#!/usr/bin/env python3
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request

LOG_DIR = "log"
LOG_FILE = os.path.join(
    LOG_DIR,
    time.strftime("%Y-%m-%d_%H-%M-%S.log")
)

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com/client/v4"
DOH_SERVERS = [
    "https://1.1.1.1/dns-query?name={name}&type=A",
    "https://dns.google/resolve?name={name}&type=A",
    "https://8.8.8.8/resolve?name={name}&type=A",
    "https://8.8.4.4/resolve?name={name}&type=A",
    "https://dns.alidns.com/resolve?name={name}&type=A",
]
SAMPLE_ROUNDS = 3
MIN_SAMPLE_RATIO = 0.4
REQUEST_TIMEOUT = 12
MAX_ATTEMPTS = 2
RETRY_DELAY = 2
IP_RE = re.compile(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$")


def query_doh(base: str, host: str) -> list[str]:
    url = base.format(name=urllib.parse.quote(host))
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/dns-json",
    })
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    ips: list[str] = []
    for answer in data.get("Answer", []):
        if answer.get("type") == 1:
            ip = answer.get("data", "")
            if IP_RE.match(ip):
                ips.append(ip)
    return list(dict.fromkeys(ips))


def resolve_ips(host: str) -> list[str]:
    import concurrent.futures as cf
    import socket

    counts: dict[str, int] = {}
    successful = 0

    for _ in range(SAMPLE_ROUNDS):
        with cf.ThreadPoolExecutor(max_workers=len(DOH_SERVERS)) as pool:
            futures = {pool.submit(query_doh, base, host): base for base in DOH_SERVERS}
            for future in cf.as_completed(futures):
                base = futures[future]
                try:
                    ips = future.result()
                except Exception as exc:
                    logger.warning(f"[!] {base} failed: {exc}")
                    continue
                if not ips:
                    logger.warning(f"[!] {base}: no A records")
                    continue
                successful += 1
                for ip in ips:
                    counts[ip] = counts.get(ip, 0) + 1
                logger.info(f"[OK] {base} -> {len(ips)} ips")
        if successful >= SAMPLE_ROUNDS * len(DOH_SERVERS) * MAX_ATTEMPTS:
            break

    if not counts:
        logger.warning("All DoH queries failed, falling back to system resolver ...")
        try:
            for info in socket.getaddrinfo(host, None, socket.AF_INET):
                counts.setdefault(info[4][0], 1)
        except Exception as exc:
            logger.error(f"System resolver also failed: {exc}")
        if not counts:
            raise RuntimeError(f"no IPs resolved for {host} (DoH + system DNS both failed)")

    sorted_ips = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    logger.info(f"Sample stats ({successful} successful samples):")
    for ip, cnt in sorted_ips:
        logger.info(f"  {ip}: {cnt}/{successful}")

    threshold = max(MIN_SAMPLE_RATIO * successful, 1.0)
    kept = [ip for ip, cnt in sorted_ips if cnt >= threshold]
    if not kept:
        kept = [sorted_ips[0][0]]
    logger.info(f"Kept {len(kept)} stable IP(s): {kept}")
    return kept


def normalize_target(target: str) -> str:
    target = target.strip()
    if not target:
        return ""
    if "://" in target:
        parsed = urllib.parse.urlparse(target)
        host = parsed.hostname or ""
    else:
        host = target.split("/")[0].split("?")[0].split("#")[0]
    if ":" in host:
        host = host.split(":")[0]
    return host.lower()


def cf(path: str, method: str = "GET", payload: dict | None = None, token: str | None = None):
    url = f"{API_BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def cloudflare_call(path, method="GET", payload=None):
    token = os.environ.get("CF_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("CF_API_TOKEN is not set")
    result = cf(path, method, payload, token)
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare API error: {json.dumps(result.get('errors', result), ensure_ascii=False)}")
    return result["result"]


def main() -> int:
    zone_id = os.environ.get("CF_ZONE_ID", "").strip()
    record = os.environ.get("CF_RECORD", "").strip()
    proxy = os.environ.get("CF_PROXY", "").strip()
    target = normalize_target(os.environ.get("CF_TARGET", ""))
    if not zone_id:
        raise RuntimeError("CF_ZONE_ID is not set")
    if not record:
        raise RuntimeError("CF_RECORD is not set (use '#' for the zone root)")
    if proxy not in ("#", "*"):
        raise RuntimeError("CF_PROXY must be '#' (grey) or '*' (orange)")
    if not target:
        raise RuntimeError("CF_TARGET is not set (e.g. www.shopify.com)")

    zone = cloudflare_call(f"/zones/{zone_id}")
    zone_name = zone.get("name")
    if not zone_name:
        raise RuntimeError("cannot resolve zone name from zone id")
    logger.info(f"Zone: {zone_name}")

    if record == "#":
        record = zone_name
    elif record != zone_name and not record.endswith(f".{zone_name}"):
        raise RuntimeError(f"record '{record}' does not belong to zone '{zone_name}'")

    logger.info(f"Target record: {record}")
    logger.info(f"Proxied: {proxy == '*'}")
    logger.info(f"Scraping IPs from: {target}")

    all_ips = resolve_ips(target)
    if not all_ips:
        raise RuntimeError(f"no IPs resolved for {target}")
    logger.info(f"Total unique IPs: {len(all_ips)}")
    for ip in all_ips:
        logger.info(f"  - {ip}")

    logger.info("Listing existing records ...")
    existing = cloudflare_call(f"/zones/{zone_id}/dns_records?name={record}")
    existing_by_ip = {r["content"]: r["id"] for r in existing}

    logger.info("Adding missing A records ...")
    added = 0
    for ip in all_ips:
        if ip in existing_by_ip:
            logger.info(f"  skip existing: {ip}")
            continue
        cloudflare_call(
            f"/zones/{zone_id}/dns_records",
            "POST",
            {
                "type": "A",
                "name": record,
                "content": ip,
                "ttl": 1,
                "proxied": proxy == "*",
            },
        )
        added += 1
    logger.info(f"Added {added} new A record(s) for {record}")

    logger.info("Removing stale A records ...")
    removed = 0
    for ip, rid in existing_by_ip.items():
        if ip not in all_ips:
            cloudflare_call(f"/zones/{zone_id}/dns_records/{rid}", method="DELETE")
            logger.info(f"  removed stale: {ip}")
            removed += 1
    logger.info(f"Removed {removed} stale A record(s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logger.critical(f"FATAL: {exc}")
        sys.exit(1)
