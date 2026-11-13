# Preprocessing & Data Normalization Report

**Project:** `Sholinganallur_RouteWise`  
**Phase:** Phase 3 — Dataset Preparation and Ingestion (Step 2: Preprocessing and Normalization)  
**Execution Timestamp:** 2026-11-08  
**Validation Verdict:** **PASSED — DATASET READY FOR CHUNKING & EMBEDDINGS**

---

## 1. Input File Inventory

* **Source Archive:** `data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip`
* **File Size:** 666,566 bytes
* **Archive Format:** Standard ZIP Archive
* **Files Extracted & Processed:**
  1. `README.txt` (1,010 bytes) — Documentation and static schedule disclaimers
  2. `area_summary.json` (819 bytes) — Area metrics and provenance
  3. `bus_stops.csv` (7,652 bytes) — Stop geographic metadata
  4. `route_service_summary.csv` (19,569 bytes) — Bus route services and daily frequencies
  5. `scheduled_departures.csv` (2,349,390 bytes) — Granular timetable departures
  6. `direct_t_nagar_connections.csv` (850,930 bytes) — Express corridor connection itineraries
  7. `routewise_sholinganallur_dataset.json` (8,567,296 bytes) — Master composite dataset

---

## 2. Output File Inventory

All normalized datasets are saved in JSON format under `data/processed/`:

| Output File | Size on Disk | Record Count | Schema / Contents |
|---|---|---|---|
| [`bus_stops.json`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/data/processed/bus_stops.json) | 19,144 bytes | 26 records | Normalized stop IDs, names, float coordinates, and mapping flags |
| [`route_service_summary.json`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/data/processed/route_service_summary.json) | 106,679 bytes | 278 records | Route numbers, names, trip counts, earliest/latest departures in seconds |
| [`scheduled_departures.json`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/data/processed/scheduled_departures.json) | 7,180,133 bytes | 18439 records | Full timetable departures with stop sequence and departure seconds |
| [`direct_t_nagar_connections.json`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/data/processed/direct_t_nagar_connections.json) | 1,165,647 bytes | 170 records | Deserialized intermediate stop arrays, journey duration in minutes |
| [`dataset_metadata.json`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/data/processed/dataset_metadata.json) | 4,211 bytes | 1 metadata object | Documented provenance, 8 limitations, coordinate patch, static disclaimer |
| [`quarantined_records.json`](file:///c:/Users/Kerthika%20M/Documents/AI%20Project/Sholinganallur_RouteWise/data/processed/quarantined_records.json) | 586 bytes | 2 records | Audited stops with missing/quarantined coordinates |

---

## 3. Before and After Record Counts

| Entity / Dataset | Raw Records (Input) | Normalized Records (Output) | Count Match Status |
|---|---|---|---|
| **Bus Stops** | 26 | 26 | **100% Match (Exact)** |
| **Route Service Summaries** | 278 | 278 | **100% Match (Exact)** |
| **Scheduled Departures** | 18439 | 18439 | **100% Match (Exact)** |
| **Direct T. Nagar Connections** | 170 | 170 | **100% Match (Exact)** |

*Zero records were dropped, lost, or duplicated during the ETL pipeline.*

---

## 4. Data Type Conversions

1. **UTF-8 Byte Order Mark (BOM):**
   - Correctly stripped using `utf-8-sig` encoding; column headers match exact identifiers without `﻿`.
2. **Geographic Coordinates:**
   - Converted valid `latitude` and `longitude` strings to Python `float`.
   - Empty coordinate strings converted to `None` (JSON `null`).
3. **Boolean Flags:**
   - Converted string flags (`'True'`, `'False'`) to native boolean `True`/`False` for `map_eligible`, `coordinates_within_chennai_bounds`, and `locality_area_membership_verified`.
4. **Timetable & Chronology:**
   - Converted `departure_time` (HH:MM:SS) to integer `departure_seconds` from midnight (supporting GTFS post-midnight trips with hours >= 24).
   - Calculated `duration_minutes` for all direct connections (`round((arr_sec - dep_sec) / 60.0, 2)`).
5. **Embedded JSON Deserialization:**
   - Deserialized `stops_between_origin_and_t_nagar` from raw JSON strings into structured native JSON arrays with integer `stop_sequence`.

---

## 5. Missing Values Audit

* **Bus Stops:**
  - `latitude`: 2 missing values (quarantined).
  - `longitude`: 2 missing values (quarantined).
  - All other fields (`stop_id`, `stop_name`, `normalized_stop_name`): 0 missing values.
* **Route Service Summary:** 0 missing values across all columns.
* **Scheduled Departures:** 0 missing values across all columns.
* **Direct T. Nagar Connections:** 0 missing values across all columns.

---

## 6. Invalid & Quarantined Records

In strict compliance with data integrity policies, missing or corrupted values were not invented:

| Quarantined Stop ID | Stop Name | Reason for Quarantine | `map_eligible` |
|---|---|---|---|
| `380` | `SHOLINGANALLUR` | Quarantined placeholder coordinate in raw GTFS | `False` |
| `168` | `SHOLINGANALLUR P.U.OFFICE` | Quarantined placeholder coordinate in raw GTFS | `False` |

* **Total Quarantined Records:** 2 records.
* **Handling Policy:** Preserved in `bus_stops.json` with `latitude: null, longitude: null, map_eligible: false` and explicitly audited in `quarantined_records.json`. These stops can still be queried by name/text, but will be excluded from geospatial distance math until verified.

---

## 7. Referential Integrity Results

| Referential Link | Validation Rule | Status |
|---|---|---|
| `scheduled_departures.stop_id` $ightarrow$ `bus_stops.stop_id` | Every departure references a valid stop | **100% Valid (0 orphans)** |
| `scheduled_departures.route_id` $ightarrow$ `route_service_summary.route_id` | Every departure references a valid route | **100% Valid (0 orphans)** |
| `direct_connections.origin_stop_id` $ightarrow$ `bus_stops.stop_id` | Origin boarding stops exist in area stops | **100% Valid (0 orphans)** |
| `direct_connections.route_id` $ightarrow$ `route_service_summary.route_id` | Direct routes exist in route summary | **100% Valid (0 orphans)** |

---

## 8. Skipped or Rejected Records

* **Total Skipped Records:** `0`
* **Total Rejected Records:** `0`
* **Reason:** All 18,913 total transit entities passed validation and conformed to required schemas.

---

## 9. Confirmation of Original Archive Immutability

* **Raw File Path:** `data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip`
* **SHA-256 Checksum (Before Processing):**  
  `6ed442d4e85db2000c5d150fc4bb8f14cb0c65ebb519122e71cd03f45e13a9e7`
* **SHA-256 Checksum (After Processing):**  
  `6ed442d4e85db2000c5d150fc4bb8f14cb0c65ebb519122e71cd03f45e13a9e7`
* **Immutability Result:** **100% UNCHANGED.** The raw ZIP was accessed via read-only file streams. No files were written to `data/raw/`.

---

## 10. Overall Preprocessing Verdict

**VERDICT: APPROVED FOR PHASE 3 STEP 3 (CHUNKING & EMBEDDINGS).**

The dataset has been completely normalized, typed, and validated. The resulting structured JSON files in `data/processed/` provide optimal semantic units for RAG document chunking and vector embedding generation.
