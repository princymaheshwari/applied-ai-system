"""OSV.dev vulnerability lookup for external evidence.

This module queries OSV.dev (Open Source Vulnerabilities) to find
known security vulnerabilities affecting packages with version deltas.

API: OSV.dev API v1
Rate limits: Generous (no documented limit)
Auth: Not required

Usage:
    from config_detective.retrieval.osv import lookup_vulnerabilities

    results = await lookup_vulnerabilities(
        packages=[
            ("cryptography", "41.0.0"),
            ("requests", "2.31.0"),
        ]
    )
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from .cache import get_cache, make_cache_key
from .models import ExternalEvidence, EvidenceSource, EvidenceType, OSVVulnerability

logger = logging.getLogger(__name__)

# OSV.dev API configuration
OSV_API_BASE = "https://api.osv.dev/v1"
OSV_QUERY = f"{OSV_API_BASE}/query"
OSV_VULNS = f"{OSV_API_BASE}/vulns"

# Default timeout
REQUEST_TIMEOUT = 10.0

# Ecosystem mappings for OSV
ECOSYSTEM_MAP: dict[str, str] = {
    "py_pkg": "PyPI",
    "node_pkg": "npm",
    "os_pkg": "Debian",  # Default for OS packages
}


async def lookup_vulnerabilities(
    packages: list[tuple[str, str]],
    ecosystem: str = "PyPI",
    use_cache: bool = True,
) -> list[ExternalEvidence]:
    """Look up vulnerabilities for a list of packages.

    Args:
        packages: List of (package_name, version) tuples
        ecosystem: Package ecosystem (PyPI, npm, Debian, etc.)
        use_cache: Whether to use cached results

    Returns:
        List of ExternalEvidence for found vulnerabilities
    """
    all_results: list[ExternalEvidence] = []

    for package_name, version in packages:
        results = await _lookup_package_vulns(
            package_name, version, ecosystem, use_cache
        )
        all_results.extend(results)

    # Sort by relevance (severity)
    all_results.sort()

    return all_results


async def _lookup_package_vulns(
    package_name: str,
    version: str,
    ecosystem: str,
    use_cache: bool,
) -> list[ExternalEvidence]:
    """Look up vulnerabilities for a single package.

    Args:
        package_name: Name of the package
        version: Version string
        ecosystem: Package ecosystem
        use_cache: Whether to use cache

    Returns:
        List of ExternalEvidence
    """
    cache = get_cache()
    cache_key = make_cache_key("osv", ecosystem, package_name, version)

    if use_cache:
        cached = cache.get("osv", cache_key)
        if cached is not None:
            logger.debug(f"OSV cache hit for {package_name}@{version}")
            return [ExternalEvidence.from_dict(e) for e in cached]

    try:
        vulns = await _query_osv(package_name, version, ecosystem)
    except Exception as e:
        logger.error(f"OSV lookup failed for {package_name}: {e}")
        return []

    # Convert to ExternalEvidence
    evidence_list = []
    for vuln in vulns:
        relevance = _compute_osv_relevance(vuln)
        evidence = vuln.to_evidence(relevance_score=relevance)
        evidence.cache_key = cache_key
        evidence_list.append(evidence)

    # Cache results (longer TTL for CVEs)
    if use_cache:
        cache.set("osv", cache_key, [e.to_dict() for e in evidence_list], ttl_hours=168)

    return evidence_list


async def _query_osv(
    package_name: str,
    version: str,
    ecosystem: str,
) -> list[OSVVulnerability]:
    """Query OSV.dev API for vulnerabilities.

    Args:
        package_name: Package name
        version: Package version
        ecosystem: Package ecosystem

    Returns:
        List of OSVVulnerability objects
    """
    payload = {
        "package": {
            "name": package_name,
            "ecosystem": ecosystem,
        },
        "version": version,
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(OSV_QUERY, json=payload)
        response.raise_for_status()
        data = response.json()

    vulns = []
    for item in data.get("vulns", []):
        try:
            vuln = _parse_osv_vuln(item, package_name)
            vulns.append(vuln)
        except Exception as e:
            logger.debug(f"Failed to parse OSV vulnerability: {e}")
            continue

    logger.info(f"OSV found {len(vulns)} vulnerabilities for {package_name}@{version}")
    return vulns


def _parse_osv_vuln(item: dict[str, Any], package_name: str) -> OSVVulnerability:
    """Parse an OSV API response item into an OSVVulnerability."""
    # Parse dates
    published = datetime.fromisoformat(
        item.get("published", "2000-01-01T00:00:00Z").replace("Z", "+00:00")
    )
    modified = datetime.fromisoformat(
        item.get("modified", published.isoformat()).replace("Z", "+00:00")
    )

    # Extract severity
    severity = _extract_severity(item)

    # Extract affected versions
    affected_versions = []
    for affected in item.get("affected", []):
        for version_range in affected.get("ranges", []):
            events = version_range.get("events", [])
            for event in events:
                if "introduced" in event:
                    affected_versions.append(f">={event['introduced']}")
                if "fixed" in event:
                    affected_versions.append(f"<{event['fixed']}")

    # Extract references
    references = [ref.get("url", "") for ref in item.get("references", []) if ref.get("url")]

    # Get aliases (CVE IDs, etc.)
    aliases = item.get("aliases", [])

    return OSVVulnerability(
        id=item["id"],
        summary=item.get("summary", "No summary available"),
        details=item.get("details", ""),
        severity=severity,
        published=published,
        modified=modified,
        affected_packages=[package_name],
        affected_versions=affected_versions,
        references=references[:5],  # Limit references
        aliases=aliases,
    )


def _extract_severity(item: dict[str, Any]) -> str | None:
    """Extract severity from OSV vulnerability data."""
    # Check severity array
    severities = item.get("severity", [])
    for sev in severities:
        if sev.get("type") == "CVSS_V3":
            score = sev.get("score", "")
            # Parse CVSS score to severity
            try:
                cvss_score = float(score.split("/")[0].split(":")[1])
                if cvss_score >= 9.0:
                    return "CRITICAL"
                elif cvss_score >= 7.0:
                    return "HIGH"
                elif cvss_score >= 4.0:
                    return "MEDIUM"
                else:
                    return "LOW"
            except (ValueError, IndexError):
                pass

    # Check database_specific
    db_specific = item.get("database_specific", {})
    if "severity" in db_specific:
        return db_specific["severity"].upper()

    # Check GHSA severity
    ghsa_severity = db_specific.get("github_reviewed_severity", "")
    if ghsa_severity:
        return ghsa_severity.upper()

    return None


def _compute_osv_relevance(vuln: OSVVulnerability) -> float:
    """Compute relevance score for a vulnerability.

    Higher severity = higher relevance.
    """
    severity_scores = {
        "CRITICAL": 0.95,
        "HIGH": 0.85,
        "MEDIUM": 0.70,
        "LOW": 0.50,
    }

    base_score = severity_scores.get(vuln.severity or "", 0.60)

    # Boost for recent vulnerabilities
    age_days = (datetime.utcnow() - vuln.published.replace(tzinfo=None)).days
    if age_days < 30:
        base_score = min(1.0, base_score + 0.05)
    elif age_days < 90:
        base_score = min(1.0, base_score + 0.02)

    return base_score


async def lookup_by_cve(cve_id: str, use_cache: bool = True) -> ExternalEvidence | None:
    """Look up a specific CVE by ID.

    Args:
        cve_id: CVE ID (e.g., "CVE-2024-1234" or "GHSA-xxxx-xxxx-xxxx")
        use_cache: Whether to use cache

    Returns:
        ExternalEvidence or None if not found
    """
    cache = get_cache()
    cache_key = make_cache_key("osv_cve", cve_id)

    if use_cache:
        cached = cache.get("osv", cache_key)
        if cached:
            return ExternalEvidence.from_dict(cached[0])

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{OSV_VULNS}/{cve_id}")

            if response.status_code == 404:
                logger.debug(f"CVE not found: {cve_id}")
                return None

            response.raise_for_status()
            data = response.json()

        vuln = _parse_osv_vuln(data, "unknown")
        relevance = _compute_osv_relevance(vuln)
        evidence = vuln.to_evidence(relevance_score=relevance)

        if use_cache:
            cache.set("osv", cache_key, [evidence.to_dict()], ttl_hours=168)

        return evidence

    except Exception as e:
        logger.error(f"OSV CVE lookup failed for {cve_id}: {e}")
        return None


async def lookup_from_deltas(
    deltas: list[tuple[str, str, str | None, str | None]],
    use_cache: bool = True,
) -> list[ExternalEvidence]:
    """Look up vulnerabilities for packages from delta information.

    Args:
        deltas: List of (node_type, name, version_a, version_b) tuples
        use_cache: Whether to use cache

    Returns:
        List of ExternalEvidence for affected packages
    """
    all_results: list[ExternalEvidence] = []

    for node_type, name, version_a, version_b in deltas:
        # Determine ecosystem
        ecosystem = ECOSYSTEM_MAP.get(node_type, "PyPI")

        # Check both versions if available
        versions_to_check = []
        if version_a:
            versions_to_check.append(version_a)
        if version_b and version_b != version_a:
            versions_to_check.append(version_b)

        for version in versions_to_check:
            results = await _lookup_package_vulns(name, version, ecosystem, use_cache)
            for result in results:
                # Tag with which version had the vulnerability
                result.metadata["checked_version"] = version
            all_results.extend(results)

    # Deduplicate by vulnerability ID
    seen_ids: set[str] = set()
    unique_results = []
    for result in all_results:
        vuln_id = result.metadata.get("vuln_id", result.title)
        if vuln_id not in seen_ids:
            seen_ids.add(vuln_id)
            unique_results.append(result)

    # Sort by relevance
    unique_results.sort()

    return unique_results
