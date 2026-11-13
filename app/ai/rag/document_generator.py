import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("uvicorn.error")

DEFAULT_PROCESSED_DIR = Path("data/processed")
DATASET_VERSION = "v1.0.0-sholinganallur"


def compute_sha256(text: str) -> str:
    """Computes deterministic SHA-256 hex digest of string content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DocumentGenerator:
    """
    Deterministic semantic document generator for Sholinganallur_RouteWise RAG pipeline.
    Synthesizes structured transit data into self-contained, human-readable markdown documents.
    """

    def __init__(self, processed_dir: Path | str = DEFAULT_PROCESSED_DIR):
        self.processed_dir = Path(processed_dir)
        self.bus_stops: List[Dict[str, Any]] = []
        self.route_services: List[Dict[str, Any]] = []
        self.scheduled_departures: List[Dict[str, Any]] = []
        self.direct_connections: List[Dict[str, Any]] = []
        self.dataset_metadata: Dict[str, Any] = {}
        self.quarantined_records: List[Dict[str, Any]] = []

    def load_data(self) -> None:
        """Loads canonical processed JSON files into memory."""
        logger.info("Loading processed datasets from %s", self.processed_dir)

        with open(self.processed_dir / "bus_stops.json", "r", encoding="utf-8") as f:
            self.bus_stops = json.load(f)

        with open(self.processed_dir / "route_service_summary.json", "r", encoding="utf-8") as f:
            self.route_services = json.load(f)

        with open(self.processed_dir / "scheduled_departures.json", "r", encoding="utf-8") as f:
            self.scheduled_departures = json.load(f)

        with open(self.processed_dir / "direct_t_nagar_connections.json", "r", encoding="utf-8") as f:
            self.direct_connections = json.load(f)

        with open(self.processed_dir / "dataset_metadata.json", "r", encoding="utf-8") as f:
            self.dataset_metadata = json.load(f)

        with open(self.processed_dir / "quarantined_records.json", "r", encoding="utf-8") as f:
            self.quarantined_records = json.load(f)

        logger.info(
            "Datasets loaded: %d stops, %d route services, %d departures, %d direct connections",
            len(self.bus_stops),
            len(self.route_services),
            len(self.scheduled_departures),
            len(self.direct_connections),
        )

    def generate_stop_documents(self) -> List[Dict[str, Any]]:
        """
        Generates 26 Bus Stop Profile documents from bus_stops.json.
        Enriches each stop profile with exact routes serving that stop from departures.
        """
        # Map stop_id to serving routes and total departures
        stop_routes_map: Dict[str, Set[str]] = defaultdict(set)
        stop_dep_count: Dict[str, int] = defaultdict(int)

        for dep in self.scheduled_departures:
            stop_routes_map[dep["stop_id"]].add(dep["route_number"])
            stop_dep_count[dep["stop_id"]] += 1

        documents = []
        for stop in sorted(self.bus_stops, key=lambda x: x["stop_id"]):
            stop_id = stop["stop_id"]
            stop_name = stop["stop_name"]
            stop_code = stop.get("stop_code", stop_id)
            lat = stop.get("latitude")
            lon = stop.get("longitude")
            map_eligible = stop.get("map_eligible", False)
            coord_status = stop.get("coordinate_status", "UNKNOWN")
            is_quarantined = not map_eligible or lat is None or lon is None

            routes_serving = sorted(list(stop_routes_map.get(stop_id, set())))
            total_deps = stop_dep_count.get(stop_id, 0)

            # Build markdown content
            content_lines = [
                f"# BUS STOP DIRECTORY: {stop_name}",
                "",
                "## Stop Identification & Locality",
                f"- Official Stop Name: {stop_name}",
                f"- Stop Identifier (GTFS stop_id): {stop_id}",
                f"- Stop Code: {stop_code}",
                "- Locality: Sholinganallur, Chennai (MTC Transit Zone)",
                "",
                "## Geographic Coordinates & Mapping Eligibility",
                f"- Latitude: {lat if lat is not None else 'Unavailable (Quarantined)'}",
                f"- Longitude: {lon if lon is not None else 'Unavailable (Quarantined)'}",
                f"- Map Display Eligible: {'Yes' if map_eligible else 'No'}",
                f"- Coordinate Status: {coord_status}",
            ]

            if is_quarantined:
                content_lines.extend([
                    "- Data Quality Advisory: Geographic coordinates for this stop were quarantined because source GTFS coordinates were outside verified Chennai boundaries. This stop remains fully active for timetable searches, but is excluded from geographic map pins and distance math.",
                ])

            content_lines.extend([
                "",
                "## Bus Services & Operational Departures",
                f"- Total Recorded Stop Departures: {total_deps} scheduled departures",
                f"- Number of Connecting Route Lines: {len(routes_serving)} lines",
                f"- Serving Bus Lines: {', '.join(routes_serving) if routes_serving else 'No departures recorded in this extract'}",
                "",
                "## Important Notice",
                "- All departure times and schedules represent official static Chennai MTC GTFS timetable feeds. Real-time GPS tracking and live bus arrival times are not provided.",
            ])

            content = "\n".join(content_lines).strip()
            doc_id = f"doc_stop_{stop_id}"

            metadata = {
                "document_type": "stop_profile",
                "stop_id": stop_id,
                "stop_code": stop_code,
                "stop_name": stop_name,
                "map_eligible": map_eligible,
                "has_coordinates": not is_quarantined,
                "latitude": lat,
                "longitude": lon,
                "total_departures": total_deps,
                "connecting_route_count": len(routes_serving),
                "connecting_routes": routes_serving,
            }

            documents.append({
                "document_id": doc_id,
                "document_type": "stop_profile",
                "title": f"Bus Stop Profile: {stop_name} (Stop ID: {stop_id})",
                "source_file": "bus_stops.json",
                "dataset_version": DATASET_VERSION,
                "metadata": metadata,
                "content": content,
                "document_hash": compute_sha256(content),
            })

        return documents

    def generate_route_documents(self) -> List[Dict[str, Any]]:
        """
        Generates exactly 58 Consolidated Route Profile documents from route_service_summary.json.
        Groups all 278 operational service records by public route_number.
        """
        # Group service records by route_number
        by_number: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for service in self.route_services:
            by_number[service["route_number"]].append(service)

        documents = []
        for route_number in sorted(by_number.keys()):
            variants = sorted(by_number[route_number], key=lambda x: x["route_id"])
            total_trips = sum(v["distinct_trips_in_feed"] for v in variants)
            total_deps = sum(v["scheduled_stop_departures_in_area"] for v in variants)

            # Earliest and latest departure across all variants
            earliest_sec = min(v["earliest_departure_seconds"] for v in variants)
            latest_sec = max(v["latest_departure_seconds"] for v in variants)
            earliest_variant = min(variants, key=lambda x: x["earliest_departure_seconds"])
            latest_variant = max(variants, key=lambda x: x["latest_departure_seconds"])

            # Unique terminals / destinations
            terminals = set()
            for v in variants:
                rname = v["route_name"]
                if " To " in rname:
                    orig, dest = rname.split(" To ", 1)
                    terminals.add(orig.strip())
                    terminals.add(dest.strip())
                else:
                    terminals.add(rname)

            content_lines = [
                f"# MTC BUS ROUTE PROFILE: ROUTE {route_number}",
                "",
                "## Public Transit Line Overview",
                f"- Public Route Number: {route_number}",
                f"- Total Operational Variants in Feed: {len(variants)} service profiles",
                f"- Total Scheduled Daily Trips: {total_trips} trips",
                f"- Total Sholinganallur Stop Departures: {total_deps} recorded departures",
                f"- Key Terminals & Hubs Served: {', '.join(sorted(terminals))}",
                "",
                "## Timetable Service Window",
                f"- Earliest Scheduled Departure in Area: {earliest_variant['earliest_departure_time_string']} (Variant ID: {earliest_variant['route_id']})",
                f"- Latest Scheduled Departure in Area: {latest_variant['latest_departure_time_string']} (Variant ID: {latest_variant['route_id']})",
                f"- Span of Operation: From {earliest_variant['earliest_departure_time_string']} to {latest_variant['latest_departure_time_string']}",
                "",
                f"## Operational Service Variants ({len(variants)} GTFS Service Profiles)",
            ]

            for v in variants:
                content_lines.extend([
                    f"### Variant Route ID: {v['route_id']} — {v['route_name']}",
                    f"- Terminal Path: {v['route_name']}",
                    f"- Scheduled Daily Trips: {v['distinct_trips_in_feed']} trips",
                    f"- Departures Serving Sholinganallur: {v['scheduled_stop_departures_in_area']} stop departures",
                    f"- Operational Hours: {v['earliest_departure_time_string']} to {v['latest_departure_time_string']}",
                    "",
                ])

            content_lines.extend([
                "## Static Schedule Disclaimer",
                "- All timetable benchmarks are derived from official static Chennai MTC GTFS data. Times indicate scheduled timetable departures and do not reflect real-time traffic conditions or live vehicle tracking.",
            ])

            content = "\n".join(content_lines).strip()

            # Generate stable, collision-safe document ID with deterministic hash suffix
            # Preserves original route number while ensuring portability on case-insensitive filesystems
            route_hash = hashlib.sha256(route_number.encode("utf-8")).hexdigest()[:8]
            safe_route_slug = route_number.replace(" ", "_")
            doc_id = f"doc_route_{safe_route_slug}_{route_hash}"

            metadata = {
                "document_id": doc_id,
                "document_type": "route_profile",
                "route_number": route_number,
                "route_hash_suffix": route_hash,
                "variant_count": len(variants),
                "variant_route_ids": [v["route_id"] for v in variants],
                "total_daily_trips": total_trips,
                "total_area_departures": total_deps,
                "earliest_departure_time": earliest_variant["earliest_departure_time_string"],
                "latest_departure_time": latest_variant["latest_departure_time_string"],
                "earliest_departure_seconds": earliest_sec,
                "latest_departure_seconds": latest_sec,
                "terminals": sorted(list(terminals)),
            }

            documents.append({
                "document_id": doc_id,
                "document_type": "route_profile",
                "title": f"Route Profile: Route {route_number} ({len(variants)} Service Variants)",
                "source_file": "route_service_summary.json",
                "dataset_version": DATASET_VERSION,
                "metadata": metadata,
                "content": content,
                "document_hash": compute_sha256(content),
            })

        return documents

    def generate_corridor_document(self) -> Dict[str, Any]:
        """
        Generates 1 Corridor Travel Guide document from direct_t_nagar_connections.json.
        Covers all 170 direct connections, route lines, boarding hubs, and milestones.
        """
        conns = self.direct_connections
        total_runs = len(conns)

        # Route breakdown
        route_dist = defaultdict(int)
        for c in conns:
            route_dist[c["route_number"]] += 1

        # Origin stop breakdown
        origin_dist = defaultdict(int)
        for c in conns:
            origin_dist[c["origin_stop_name"]] += 1

        # Destination stop breakdown
        dest_dist = defaultdict(int)
        for c in conns:
            dest_dist[c["destination_stop_name"]] += 1

        durations = [c["duration_minutes"] for c in conns]
        min_dur = min(durations)
        max_dur = max(durations)
        avg_dur = round(sum(durations) / len(durations), 2)

        earliest_dep = min(conns, key=lambda x: x["scheduled_departure_seconds"])
        latest_dep = max(conns, key=lambda x: x["scheduled_departure_seconds"])

        # Extract representative ordered intermediate stops from a sample trip
        sample_conn = next(c for c in conns if len(c["stops_between_origin_and_t_nagar"]) >= 30)
        sample_stops = sample_conn["stops_between_origin_and_t_nagar"]

        content_lines = [
            "# CORRIDOR TRAVEL GUIDE: SHOLINGANALLUR TO T. NAGAR (DIRECT MTC BUS SERVICE)",
            "",
            "## Corridor Overview",
            f"Direct public bus connectivity between the Sholinganallur IT hub and Thyagaraya Nagar (T. Nagar) is operated by Chennai MTC across {total_runs} scheduled daily direct runs. All direct trips terminate at CIT Nagar, providing direct pedestrian and feeder access to the central T. Nagar shopping and commercial district.",
            "",
            "## Operating Bus Routes & Service Breakdown",
            f"- Total Daily Direct Trips: {total_runs} scheduled runs",
        ]

        for rnum, count in sorted(route_dist.items(), key=lambda x: x[1], reverse=True):
            content_lines.append(f"- Line {rnum}: {count} direct scheduled daily trips")

        content_lines.extend([
            "",
            "## Journey Duration & Operating Hours",
            f"- Average Scheduled Travel Time: {avg_dur} minutes",
            f"- Journey Duration Range: {min_dur} minutes to {max_dur} minutes",
            f"- First Morning Direct Bus: {earliest_dep['scheduled_departure_at_origin']} (Route {earliest_dep['route_number']} from {earliest_dep['origin_stop_name']})",
            f"- Last Night Direct Bus: {latest_dep['scheduled_departure_at_origin']} (Route {latest_dep['route_number']} from {latest_dep['origin_stop_name']})",
            "",
            "## Primary Boarding Hubs in Sholinganallur",
        ])

        for sname, count in sorted(origin_dist.items(), key=lambda x: x[1], reverse=True):
            content_lines.append(f"- {sname}: {count} direct departures daily")

        content_lines.extend([
            "",
            "## Destination Terminals in T. Nagar Zone",
        ])
        for dname, count in sorted(dest_dist.items(), key=lambda x: x[1], reverse=True):
            content_lines.append(f"- {dname}: {count} arrivals daily (Gateway terminal to Panagal Park and T. Nagar)")

        content_lines.extend([
            "",
            f"## Intermediate Corridor Path & Landmarks ({len(sample_stops)} Consecutive Stops)",
            "Direct trips follow the Old Mahabalipuram Road (OMR) express corridor northward into Central Chennai:",
            "1. Sholinganallur to Karapakkam: Originates at Sholinganallur Kumaran Nagar / Ponniamman Temple / Nucleus, moving through Oodai, Government School, and Accenture.",
            "2. OMR IT Arterial: Passes Karapakkam, KCG College, Okkiyampet, PTC Quarters (Mootakaran Chavadi), Mettukuppam, D.B. Jain College, Seevaram, Perungudi, Kandanchavadi, and Perungudi IGP.",
            "3. Tharamani & Transit Junctions: Crosses Perungudi S.R.P. Tools, Tidel Park, Women's Polytechnic College, Tharamani VHS Hospital, and Madhya Kailash.",
            "4. Guindy / Little Mount Corridor: Passes IIT Madras / Guindy National Park, Anna University, Raj Bhavan Quarters, Chellammal College, and Little Mount.",
            "5. Saidapet & T. Nagar Approach: Crosses Panagal Maligai, Saidapet, Saidapet Veterinary Hospital, and terminates at CIT Nagar.",
            "",
            "## Commuter Advisory & Boundaries",
            "- These records reflect direct same-trip candidate runs. Multi-leg transfer itineraries (e.g. changing buses at Adyar or Guindy) are not included in this direct guide.",
            "- Timetable departure and arrival values are based on official Chennai MTC static GTFS schedules. Real-time GPS bus positions and traffic delays are not included.",
        ])

        content = "\n".join(content_lines).strip()
        doc_id = "doc_corridor_sholinganallur_t_nagar"

        metadata = {
            "document_type": "corridor_guide",
            "corridor_id": "sholinganallur_t_nagar",
            "origin_area": "Sholinganallur",
            "destination": "T. Nagar (CIT Nagar)",
            "total_direct_runs": total_runs,
            "routes_operating": sorted(list(route_dist.keys())),
            "route_breakdown": dict(route_dist),
            "origin_stops": sorted(list(origin_dist.keys())),
            "destination_stops": sorted(list(dest_dist.keys())),
            "min_duration_minutes": min_dur,
            "max_duration_minutes": max_dur,
            "avg_duration_minutes": avg_dur,
            "earliest_departure_time": earliest_dep["scheduled_departure_at_origin"],
            "latest_departure_time": latest_dep["scheduled_departure_at_origin"],
        }

        return {
            "document_id": doc_id,
            "document_type": "corridor_guide",
            "title": "Corridor Travel Guide: Sholinganallur to T. Nagar (Direct MTC Bus Service)",
            "source_file": "direct_t_nagar_connections.json",
            "dataset_version": DATASET_VERSION,
            "metadata": metadata,
            "content": content,
            "document_hash": compute_sha256(content),
        }

    def generate_governance_document(self) -> Dict[str, Any]:
        """
        Generates 1 System Governance & Data Limitations document from dataset_metadata.json and README.txt.
        Provides authoritative grounding on GTFS static schedules, absence of live GPS, coordinate policies,
        accurate relational stop matching via stop_id, and distinct upstream provenance boundaries.
        """
        meta = self.dataset_metadata
        patch_info = meta.get("coordinate_quality_patch", {})

        content_lines = [
            "# ROUTEWISE AI: TRANSIT DATA SPECIFICATIONS, ACCURACY & GOVERNANCE",
            "",
            "## System Authority & Dataset Scope",
            f"- Project Name: {meta.get('dataset_title', 'RouteWise AI — Chennai MTC Bus Data — Sholinganallur Only')}",
            f"- Target Area: {meta.get('area', 'Sholinganallur')}, Chennai, Tamil Nadu",
            "- Upstream GTFS Source Provenance: static_mtc_gtfs_data(3).zip (Nested archive: static_mtc_gtfs_data/chennai.bus.gtfs.zip) — Source Chennai MTC transit package from which raw tables (agency, stops, routes, trips, stop_times) were compiled.",
            "- Localized Project Input Archive: data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip — The verified project extract containing localized Sholinganallur transit feeds.",
            f"- Ingestion & Preprocessing Timestamp: {meta.get('preprocessed_at_utc', '2026-11-08')}",
            "",
            "## Core Operational Boundaries & Disclaimers",
            "1. Static GTFS schedules only; not live bus arrivals or real-time GPS vehicle positions.",
            "2. Scheduled time strings do not prove the current bus is on time or currently approaching.",
            "3. The feed contains no verified live or historical passenger occupancy, so crowd levels cannot be confirmed from this file.",
            "4. Stop-to-Departure Relational Matching: In the operational dataset, scheduled departures are associated with bus stops strictly via exact GTFS stop_id foreign keys (scheduled_departures.stop_id == bus_stops.stop_id). All 18,439 scheduled departures match one of the 26 Sholinganallur stops with 0 orphan records. Note: The upstream extraction of the 26 Sholinganallur stops from the Greater Chennai feed originally used stop name and locality keywords, which could omit stops with alternate spellings or include nearby named stops; however, all internal transit connections in this system use deterministic stop_id matching.",
            "5. Geographic coordinate verification: Out-of-bounds coordinates are quarantined (see policy below); review before map display.",
            "6. Trip counts and route counts describe records in this feed, not passenger demand, real-world service quality, or frequency ranking by themselves.",
            "7. Direct T. Nagar connections are same-trip candidates (no transfer-based itineraries are generated).",
            "8. Validate feed freshness, service dates, direction, and route operation before passenger-facing use.",
            "",
            "## Quarantined Coordinates Policy",
            f"- Patch Date: {patch_info.get('patch_date_utc', '2026-10-30')}",
            "- Audit Rule: Bus stop coordinates outside verified Greater Chennai bounds were quarantined to prevent inaccurate map placement.",
            "- Affected Sholinganallur Stops: Stop 380 (SHOLINGANALLUR) and Stop 168 (SHOLINGANALLUR P.U.OFFICE) have coordinates set to null.",
            "- Search Policy: Quarantined stops remain fully accessible for route, name, and timetable searches, but must be excluded from geographic map pins and spatial distance calculations.",
            "",
            "## GTFS Continuous Post-Midnight Time Notation",
            "- In GTFS static scheduling, late-night runs operating past midnight on the active service date are recorded continuously as 24:xx and 25:xx (for example, 25:17:30 represents 01:17:30 AM the next calendar morning).",
            "- RouteWise AI interprets continuous seconds from midnight to maintain chronological sorting across midnight transitions.",
            "",
            "## Grounding Rules for Assistant Responses",
            "- RouteWise AI must always state that transit times are scheduled timetable departures, not real-time bus locations.",
            "- The assistant will not confirm passenger crowd density, seat availability, or live traffic delays, as such information does not exist in static GTFS feeds.",
        ]

        content = "\n".join(content_lines).strip()
        doc_id = "doc_system_governance_limitations"

        metadata = {
            "document_type": "system_governance",
            "area": meta.get("area", "Sholinganallur"),
            "data_nature": "static_gtfs_timetable",
            "live_tracking_supported": False,
            "upstream_provenance_file": "static_mtc_gtfs_data(3).zip",
            "upstream_gtfs_archive": "static_mtc_gtfs_data/chennai.bus.gtfs.zip",
            "project_input_archive": "data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip",
            "stop_matching_method": "exact_gtfs_stop_id_relational_join",
            "quarantined_stops_count": len(self.quarantined_records),
            "quarantined_stop_ids": [q["id"] for q in self.quarantined_records],
            "limitations_count": 8,
        }

        return {
            "document_id": doc_id,
            "document_type": "system_governance",
            "title": "System Governance: MTC Transit Data Limitations & Operational Boundaries",
            "source_file": "dataset_metadata.json",
            "dataset_version": DATASET_VERSION,
            "metadata": metadata,
            "content": content,
            "document_hash": compute_sha256(content),
        }

    def generate_all_documents(self) -> List[Dict[str, Any]]:
        """
        Executes the full document generation suite:
        26 stop profiles + 58 route profiles + 1 corridor guide + 1 governance document = 86 documents.
        """
        if not self.bus_stops or not self.route_services:
            self.load_data()

        stop_docs = self.generate_stop_documents()
        route_docs = self.generate_route_documents()
        corridor_doc = self.generate_corridor_document()
        gov_doc = self.generate_governance_document()

        all_docs = stop_docs + route_docs + [corridor_doc, gov_doc]

        logger.info(
            "Document generation complete: %d total documents (%d stops, %d routes, 1 corridor guide, 1 governance)",
            len(all_docs),
            len(stop_docs),
            len(route_docs),
        )

        return all_docs

    def save_documents(
        self,
        docs: List[Dict[str, Any]],
        output_dir: Path | str = Path("data/documents"),
    ) -> Dict[str, Any]:
        """
        Saves each document as an individual JSON file in data/documents/
        and produces a master documents_manifest.json catalog.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        manifest = []
        for doc in docs:
            doc_file = out_path / f"{doc['document_id']}.json"
            with open(doc_file, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)

            manifest.append({
                "document_id": doc["document_id"],
                "document_type": doc["document_type"],
                "title": doc["title"],
                "source_file": doc["source_file"],
                "dataset_version": doc["dataset_version"],
                "document_hash": doc["document_hash"],
                "content_length_chars": len(doc["content"]),
                "content_lines": len(doc["content"].splitlines()),
                "metadata": doc["metadata"],
            })

        # Save manifest
        manifest_file = out_path / "documents_manifest.json"
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        logger.info("Saved %d documents and manifest to %s", len(docs), out_path)

        return {
            "total_documents": len(docs),
            "manifest_file": str(manifest_file),
            "output_directory": str(out_path),
        }

