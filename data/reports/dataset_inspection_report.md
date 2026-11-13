# Dataset Inspection Report: Sholinganallur Public Transport GTFS Extract

**Project:** `Sholinganallur_RouteWise`  
**Phase:** Phase 3 — Dataset Preparation & Ingestion (Step 1: Dataset Inspection Only)  
**Dataset Source:** `data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip`  
**Generated Date:** 2026-11-08  
**Inspector:** Senior Data & GenAI Engineer  

---

## 1. Executive Summary

A comprehensive, non-destructive inspection was performed on `data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip`. The archive contains an extract from the Chennai Metropolitan Transport Corporation (MTC) GTFS dataset specifically filtered for the **Sholinganallur** corridor (`area_id = 'sholinganallur'`).

* **Total Files Inspected:** 7 files (all residing inside the top-level directory `Simple_RouteWise_Sholinganallur_Extract/`).
* **Nested Folders:** 1 parent folder (`Simple_RouteWise_Sholinganallur_Extract/`), 0 subdirectories.
* **Integrity Adherence:** The original ZIP archive remains untouched; no files were extracted over the archive, no Supabase tables were created, and no chunking or embeddings were performed.
* **Overall Dataset Assessment:** **Highly Suitable** for building the Sholinganallur Public Transport Assistant (RouteWise AI), offering complete coverage of stops, route summaries, timetables, and high-frequency corridor connections to T. Nagar.

---

## 2. Inventory and Record Counts

| # | File Name | Format | Uncompressed Size | Total Records | Integrity / Data Quality Status |
|---|---|---|---|---|---|
| 1 | `README.txt` | Plain Text | 1,010 bytes | 28 lines (4 sections) | Clean documentation |
| 2 | `area_summary.json` | JSON | 819 bytes | 1 object (13 keys) | Complete metadata |
| 3 | `bus_stops.csv` | CSV (UTF-8 BOM) | 7,652 bytes | 26 stop records | 2 stops quarantined (null coordinates) |
| 4 | `route_service_summary.csv` | CSV (UTF-8) | 19,569 bytes | 278 route records | 0 nulls, 0 duplicates |
| 5 | `scheduled_departures.csv` | CSV (UTF-8) | 2,349,390 bytes | 18,439 stop departures | 0 nulls, 0 duplicates |
| 6 | `direct_t_nagar_connections.csv` | CSV (UTF-8) | 850,930 bytes | 170 direct trip runs | Contains nested JSON intermediate stop lists |
| 7 | `routewise_sholinganallur_dataset.json` | JSON | 8,567,296 bytes | 1 master hierarchical object | Comprehensive canonical dataset |

---

## 3. Detailed Inspection of Each File

### 3.1 `Simple_RouteWise_Sholinganallur_Extract/README.txt`

1. **Filename and Format:** `Simple_RouteWise_Sholinganallur_Extract/README.txt` (Plain Text ASCII/UTF-8).
2. **Number of Records:** 28 lines (4 distinct structural sections).
3. **Structure & Content:**
   - Section 1: Title and provenance (`static_mtc_gtfs_data(3).zip` $\rightarrow$ `static_mtc_gtfs_data/chennai.bus.gtfs.zip`).
   - Section 2: Canonical record counts (26 stops, 278 route summaries, 58 unique route numbers, 5,285 unique trips, 18,439 departures, 170 direct T. Nagar runs).
   - Section 3: File list inventory.
   - Section 4: Boundary and GTFS static schedule disclaimers.
4. **Missing or Null Values:** None.
5. **Duplicate Records:** None.
6. **Sample Content:**
   ```text
   ROUTEWISE AI — SHOLINGANALLUR ONLY DATASET
   Extracted from the previously reviewed Chennai MTC 16-area GTFS-derived dataset.
   Source upload: static_mtc_gtfs_data(3).zip
   Source archive: static_mtc_gtfs_data/chennai.bus.gtfs.zip
   RECORD COUNTS: Bus stop records: 26, Route service summary records: 278...
   ```
7. **Suitability:** **Suitable.** Serves as authoritative grounding context for system prompt generation and assistant boundaries.

---

### 3.2 `Simple_RouteWise_Sholinganallur_Extract/area_summary.json`

1. **Filename and Format:** `Simple_RouteWise_Sholinganallur_Extract/area_summary.json` (JSON).
2. **Number of Records:** 1 root JSON dictionary with 13 metadata keys.
3. **Column Names and Data Types:**
   - `area` (`string`): Area name (`"Sholinganallur"`).
   - `bus_stop_records` (`integer`): `26`.
   - `route_service_summary_records` (`integer`): `278`.
   - `unique_route_ids_in_summary` (`integer`): `278`.
   - `unique_route_numbers` (`integer`): `58`.
   - `unique_trips_serving_matched_stops` (`integer`): `5285`.
   - `scheduled_stop_departure_records` (`integer`): `18439`.
   - `direct_t_nagar_connection_records` (`integer`): `170`.
   - `source_total_direct_connections_all_areas` (`integer`): `1121`.
   - `source_file` (`string`): `"static_mtc_gtfs_data(3).zip"`.
   - `source_gtfs_archive` (`string`): `"static_mtc_gtfs_data/chennai.bus.gtfs.zip"`.
   - `extraction_method` (`string`): Description of SQL/script filter.
   - `quality_note` (`string`): Guidance on locality membership validation.
4. **Missing or Null Values:** 0 null values across all 13 keys.
5. **Duplicate Records:** 0 duplicates.
6. **Sample Record:**
   ```json
   {
     "area": "Sholinganallur",
     "bus_stop_records": 26,
     "route_service_summary_records": 278,
     "unique_route_numbers": 58,
     "scheduled_stop_departure_records": 18439,
     "direct_t_nagar_connection_records": 170
   }
   ```
7. **Suitability:** **Suitable.** Ideal for lightweight API response metadata, quick validation checks, and prompt grounding.

---

### 3.3 `Simple_RouteWise_Sholinganallur_Extract/bus_stops.csv`

1. **Filename and Format:** `Simple_RouteWise_Sholinganallur_Extract/bus_stops.csv` (CSV with UTF-8 BOM encoding).
2. **Number of Records:** 26 stop rows (+ 1 header row).
3. **Column Names and Data Types:**
   - `stop_id` (`string`): Alphanumeric unique stop ID (e.g., `elxmPCDf`, `380`).
   - `stop_code` (`string`): Public stop code.
   - `stop_name` (`string`): Uppercase official bus stop name.
   - `latitude` (`float` or empty): WGS84 latitude coordinate.
   - `longitude` (`float` or empty): WGS84 longitude coordinate.
   - `coordinates_within_chennai_bounds` (`boolean` string): `'True'` / `'False'`.
   - `map_eligible` (`boolean` string): `'True'` / `'False'`.
   - `coordinate_status` (`string`): Quality flag string.
   - `normalized_stop_name` (`string`): Lowercase trimmed stop name.
   - `locality_area_membership_verified` (`boolean` string): `'False'` for legacy name matches.
   - `locality_area_membership_status` (`string`): Review status flag.
   - `coordinate_source` (`string`): Source file identifier.
   - `coordinate_verification_status` (`string`): Bound verification status.
   - `original_latitude` (`string` / empty): Pre-patch raw coordinate.
   - `original_longitude` (`string` / empty): Pre-patch raw coordinate.
4. **Missing or Null Values:**
   - `latitude`: **2 missing values** (Stops: `380` "SHOLINGANALLUR" and `168` "SHOLINGANALLUR P.U.OFFICE").
   - `longitude`: **2 missing values** (Stops: `380` and `168`).
   - `original_latitude`: 24 empty values (only populated where coordinates were patched).
   - `original_longitude`: 24 empty values.
5. **Duplicate Records:** 0 duplicate rows.
6. **Sample Records (3 of 26):**
   - **Record 1:** `stop_id`: `elxmPCDf`, `stop_name`: `"GOVERNMENT SCHOOL SHOLINGANALLUR"`, `latitude`: `12.89353`, `longitude`: `80.22767`, `map_eligible`: `True`
   - **Record 2:** `stop_id`: `ddf9b7895d25353b687b012d51771836`, `stop_name`: `"P U OFFICE SHOLINGANALLUR"`, `latitude`: `12.90205`, `longitude`: `80.22794`, `map_eligible`: `True`
   - **Record 3 (Quarantined):** `stop_id`: `380`, `stop_name`: `"SHOLINGANALLUR"`, `latitude`: `""`, `longitude`: `""`, `map_eligible`: `False`
7. **Suitability:** **Highly Suitable.** Fundamental reference for stop discovery, origin/destination matching, and route corridor navigation. The 2 quarantined stops are properly flagged with `map_eligible=False`.

---

### 3.4 `Simple_RouteWise_Sholinganallur_Extract/route_service_summary.csv`

1. **Filename and Format:** `Simple_RouteWise_Sholinganallur_Extract/route_service_summary.csv` (CSV, UTF-8).
2. **Number of Records:** 278 route service rows (+ 1 header row).
3. **Column Names and Data Types:**
   - `route_id` (`string` / `integer`): MTC internal route identifier (e.g., `"3"`, `"540"`, `"3342"`).
   - `route_number` (`string`): Public bus number (e.g., `"102"`, `"570"`, `"19"`, `"555S"`, `"95"`).
   - `route_name` (`string`): Full terminal-to-terminal descriptive name (e.g., `"ADYAR B.T To SIRUSERI I.T PARK"`).
   - `distinct_trips_in_feed` (`integer`): Number of scheduled trips in the daily GTFS feed.
   - `scheduled_stop_departures_in_area` (`integer`): Departures occurring across Sholinganallur stops.
   - `earliest_departure_time_string` (`string` HH:MM:SS): Time of first departure in feed.
   - `latest_departure_time_string` (`string` HH:MM:SS): Time of last departure in feed.
4. **Missing or Null Values:** 0 null values across all columns.
5. **Duplicate Records:** 0 duplicate rows.
6. **Sample Records (3 of 278):**
   - **Record 1:** `route_id`: `"3"`, `route_number`: `"102"`, `route_name`: `"ADYAR B.T To SIRUSERI I.T PARK"`, `distinct_trips_in_feed`: `2`, `earliest`: `"00:16:00"`, `latest`: `"00:16:00"`
   - **Record 2:** `route_id`: `"540"`, `route_number`: `"570"`, `route_name`: `"KELAMBAKKAM B.T To M.G.R.KOYAMBEDU B.T"`, `distinct_trips_in_feed`: `73`, `earliest`: `"01:11:30"`, `latest`: `"23:11:30"`
   - **Record 3:** `route_id`: `"419"`, `route_number`: `"19"`, `route_name`: `"THIRUPORUR BUS STAND To THYAGARAYA NAGAR BUS TERMINUS"`, `distinct_trips_in_feed`: `34`, `earliest`: `"05:14:15"`, `latest`: `"22:19:15"`
7. **Suitability:** **Highly Suitable.** Key table for answering route queries ("Which bus goes from Sholinganallur to Koyambedu or Adyar?"), operating hours, and daily service frequencies.

---

### 3.5 `Simple_RouteWise_Sholinganallur_Extract/scheduled_departures.csv`

1. **Filename and Format:** `Simple_RouteWise_Sholinganallur_Extract/scheduled_departures.csv` (CSV, UTF-8).
2. **Number of Records:** 18,439 scheduled stop departure rows (+ 1 header row).
3. **Column Names and Data Types:**
   - `stop_id` (`string`): Unique stop ID.
   - `stop_name` (`string`): Official stop name.
   - `route_id` (`string` / `integer`): Route identifier.
   - `route_number` (`string`): Public bus number.
   - `route_name` (`string`): Route origin and destination.
   - `trip_id` (`string`): Unique trip code (e.g., `"3342-S-DOWN-70498"`).
   - `direction_id` (`integer`): Direction code (`0` = UP / Outbound, `1` = DOWN / Inbound).
   - `arrival_time` (`string` HH:MM:SS): Scheduled stop arrival time.
   - `departure_time` (`string` HH:MM:SS): Scheduled stop departure time.
   - `stop_sequence` (`integer`): Stop order index along the trip.
4. **Missing or Null Values:** 0 null values across all 18,439 rows.
5. **Duplicate Records:** 0 duplicate rows.
6. **Sample Records (3 of 18,439):**
   - **Record 1:** `stop_id`: `"elxmPCDf"`, `stop_name`: `"GOVERNMENT SCHOOL SHOLINGANALLUR"`, `route_number`: `"555S"`, `route_name`: `"SHOLINGANALLUR To KILAMBAKKAM BUS TERMINUS"`, `arrival`: `"00:02:15"`, `departure`: `"00:02:30"`, `stop_sequence`: `2`
   - **Record 2:** `stop_id`: `"nunLDRbE"`, `stop_name`: `"GOVERNMENT SCHOOL SHOLINGANALLUR"`, `route_number`: `"570"`, `route_name`: `"KELAMBAKKAM B.T To M.G.R.KOYAMBEDU B.T"`, `arrival`: `"01:11:15"`, `departure`: `"01:11:30"`, `stop_sequence`: `18`
   - **Record 3:** `stop_id`: `"ddf9b7895d25353b687b012d51771836"`, `stop_name`: `"P U OFFICE SHOLINGANALLUR"`, `route_number`: `"102"`, `route_name`: `"BROADWAY To KELAMBAKKAM B.T"`, `arrival`: `"06:21:00"`, `departure`: `"06:21:15"`, `stop_sequence`: `27`
7. **Suitability:** **Highly Suitable.** Provides granular schedule intelligence for "What is the next scheduled bus?", timetable lookups, and early morning / late night trip planning.

---

### 3.6 `Simple_RouteWise_Sholinganallur_Extract/direct_t_nagar_connections.csv`

1. **Filename and Format:** `Simple_RouteWise_Sholinganallur_Extract/direct_t_nagar_connections.csv` (CSV, UTF-8).
2. **Number of Records:** 170 direct trip rows (+ 1 header row).
3. **Column Names and Data Types:**
   - `origin_area_id` (`string`): `"sholinganallur"`.
   - `origin_area_name` (`string`): `"Sholinganallur"`.
   - `trip_id` (`string`): Unique trip code.
   - `route_id` (`string` / `integer`): Route identifier.
   - `route_number` (`string`): Bus route number (e.g., `"19"`, `"19B"`).
   - `route_name` (`string`): Full route name.
   - `origin_stop_id` (`string`): Boarding stop ID in Sholinganallur.
   - `origin_stop_name` (`string`): Boarding stop name.
   - `scheduled_departure_at_origin` (`string` HH:MM:SS): Departure time from origin stop.
   - `destination_stop_id` (`string`): Destination stop ID in T. Nagar.
   - `destination_stop_name` (`string`): Destination stop name (e.g., `"CIT NAGAR"`, `"THYAGARAYA NAGAR"`).
   - `scheduled_arrival_at_t_nagar_stop` (`string` HH:MM:SS): Arrival time at T. Nagar destination.
   - `stops_between_origin_and_t_nagar` (`string` JSON array): Serialized JSON array of all intermediate stops with `stop_sequence`, `stop_id`, `stop_name`, `arrival_time`, and `departure_time`.
4. **Missing or Null Values:** 0 null values across all 170 rows.
5. **Duplicate Records:** 0 duplicate rows.
6. **Sample Records (2 of 170):**
   - **Record 1:** Route `"19"` from `"SHOLINGANALLUR KUMARAN NAGAR"` at `15:34:15` to `"CIT NAGAR"` at `16:39:15` (30 intermediate stops including Perungudi, Tidel Park, Madhya Kailash, Anna University, Saidapet).
   - **Record 2:** Route `"19"` from `"SHOLINGANALLUR KUMARAN NAGAR"` at `09:59:15` to `"CIT NAGAR"` at `11:04:15`.
7. **Suitability:** **Extremely High Value.** Solves one of the most critical commuter corridors in Chennai (OMR IT corridor to commercial hub T. Nagar). The embedded intermediate stop list provides complete step-by-step journey visibility.

---

### 3.7 `Simple_RouteWise_Sholinganallur_Extract/routewise_sholinganallur_dataset.json`

1. **Filename and Format:** `Simple_RouteWise_Sholinganallur_Extract/routewise_sholinganallur_dataset.json` (Hierarchical JSON, UTF-8, 8.56 MB).
2. **Number of Records:** 1 master composite JSON root object.
3. **Top-Level Keys and Data Types:**
   - `dataset_title` (`string`): `"RouteWise AI — Chennai MTC Bus Data — Sholinganallur Only"`
   - `generated_utc` (`string` ISO timestamp): `"2026-10-29T14:51:47.251966+00:00"`
   - `source_uploaded_file` (`string`): `"static_mtc_gtfs_data(3).zip"`
   - `source_gtfs_archive` (`string`): `"static_mtc_gtfs_data/chennai.bus.gtfs.zip"`
   - `source_tables_used` (`list[string]`): `5` tables (`agency`, `stops`, `routes`, `trips`, `stop_times`)
   - `source_counts` (`dict`): Row counts of underlying source GTFS tables
   - `scope` (`dict`): Spatial and filtering scope parameters
   - `service_calendar` (`list[dict]`): Active service days and exceptions
   - `area` (`dict`):
     - `area_id`: `"sholinganallur"`
     - `matched_stop_count`: `26`
     - `distinct_trips_serving_matched_stops`: `5285`
     - `distinct_route_numbers_serving_matched_stops`: `58`
     - `stops` (`list[dict]`): 26 stop records
     - `route_service_summary` (`list[dict]`): 278 route service records
     - `scheduled_departures_at_matched_stops` (`list[dict]`): 18,439 departure records
   - `direct_scheduled_connections_to_t_nagar` (`list[dict]`): 170 direct connection objects
   - `data_quality_and_limitations` (`list[string]`): 8 documented domain warnings
   - `coordinate_quality_patch` (`dict`): Audit statistics for quarantined coordinates
   - `issue_resolution` (`dict`): Resolution log for earlier data anomalies
   - `extraction_summary` (`dict`): Summary metrics matching `area_summary.json`
4. **Missing or Null Values:**
   - In `area.stops`: 2 stops have `latitude: null`, `longitude: null` (compliant with quarantine patch policy).
   - In all other sections: 0 null values.
5. **Duplicate Records:** 0 duplicates.
6. **Sample Section (`data_quality_and_limitations`):**
   ```text
   1. Static GTFS schedules only; not live bus arrivals or vehicle positions.
   2. Scheduled time strings do not prove current bus is on time or approaching.
   3. The feed contains no verified live or historical passenger occupancy.
   4. Stop matching is based on names; review before production use.
   5. Some stop coordinates are flagged if outside broad Chennai bounds.
   6. Direct T. Nagar connections are same-trip candidates (no transfers generated).
   ```
7. **Suitability:** **Canonical Reference.** This is the unified JSON master source from which the CSVs were generated. Highly suitable for RAG chunk synthesis, knowledge grounding, and structured document embedding.

---

## 4. Cross-Dataset Findings & Data Quality Issues

1. **UTF-8 BOM in `bus_stops.csv`:**
   - The file begins with the byte order mark `\ufeff`. Standard `csv.reader` reads the first column as `'\ufeffstop_id'`. Ingestion pipelines must use encoding `'utf-8-sig'`.
2. **Quarantined Coordinates (2 Stops):**
   - Stops `380` (`"SHOLINGANALLUR"`) and `168` (`"SHOLINGANALLUR P.U.OFFICE"`) have empty/null coordinates and `map_eligible: False`. These coordinates were quarantined because the raw GTFS contained placeholder values outside Chennai bounds. These stops must be handled gracefully in distance queries.
3. **JSON Strings Embedded in CSV:**
   - `direct_t_nagar_connections.csv` stores `stops_between_origin_and_t_nagar` as a serialized JSON string. Ingestion pipelines should deserialize this column using `json.loads` to unlock intermediate stop analysis.
4. **Static Schedules vs. Live Vehicle Tracking:**
   - The dataset represents static MTC timetable schedules. It does not provide real-time GPS tracking or crowd levels. Responses generated by RouteWise AI must inform users that times are scheduled timetable departures.

---

## 5. Recommended Preparation Steps for Step 2

| Target File | Recommended Preparation Action |
|---|---|
| `bus_stops.csv` | Read with `utf-8-sig`; cast `latitude`/`longitude` to `Optional[float]`; store `map_eligible` as boolean; prepare spatial text representation for embedding (e.g., `"Bus Stop: {name}, Code: {code}, Locality: Sholinganallur"`). |
| `route_service_summary.csv` | Normalize route numbers (uppercase/strip); parse time strings; synthesize route overview documents for RAG (e.g., `"Route {number}: {name}. Operates from {earliest} to {latest} with {distinct_trips} scheduled trips."`). |
| `direct_t_nagar_connections.csv` | Parse nested JSON `stops_between_origin_and_t_nagar`; generate rich corridor itinerary documents detailing key stops (Tidel Park, Madhya Kailash, Saidapet, Panagal Maligai). |
| `scheduled_departures.csv` | Convert into structured SQL / PostgreSQL queryable format or aggregate into time-window buckets (Morning Peak, Afternoon, Evening Peak, Night) rather than generating 18,439 isolated small embeddings. |
| `routewise_sholinganallur_dataset.json` | Use metadata, quality rules, and area summaries to populate system prompts, domain constraints, and assistant instructions. |

---

## 6. Final Assessment: Suitability for Next Stage

**Verdict: SUITABLE FOR NEXT STAGE (APPROVED FOR STEP 2).**

The dataset is internally consistent, cleanly extracted, well-documented, and fully covers the public transport network of Sholinganallur without corruption or missing required fields.

