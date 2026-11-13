# Phase 3 — Step 5A: Document Generation Report (Corrected)

## Executive Summary
- **Project:** `Sholinganallur_RouteWise`
- **Phase & Step:** Phase 3 — Step 5A (Deterministic Semantic Document Generation — Corrected)
- **Execution Timestamp:** `2026-11-08 07:22:17 UTC`
- **Execution Duration:** `0.260 seconds`
- **Input Directory:** `data/processed`
- **Output Directory:** `data/documents`
- **Validation Status:** `PASSED (100% Deterministic & Valid)`
- **Total Documents Generated:** `86` (Target: 86)
- **Total Content Volume:** `150,075 characters` (3,271 lines, average `1745.1` chars/doc)

---

## 1. Corrections Implemented

### Correction 1: Portable Route Document Identity & Case-Insensitive Safety
- **Issue Identified:** Route numbers `102X` (9 service variants) and `102x` (1 service variant) are distinct source values. Default Windows filesystems (NTFS, FAT32) and git configurations (`core.ignorecase=true`) treat filenames differing only by case as identical, risking file collisions without special NTFS directory attributes.
- **Solution Implemented:**
  - Route profiles now generate document IDs using a deterministic format: `doc_route_{safe_route_slug}_{route_hash}` (e.g. `doc_route_102X_19066bd4` and `doc_route_102x_0bda064f`).
  - The `route_hash` is an 8-character hex digest computed from `sha256(route_number.encode('utf-8'))`.
  - Original route number (including exact casing and whitespace) is strictly preserved in document content (`# MTC BUS ROUTE PROFILE: ROUTE {route_number}`), title, and metadata (`metadata['route_number']`).
  - All 58 route profile filenames are guaranteed to be unique even under lowercase comparisons (`set(id.lower()) == 58`), providing 100% portability across Windows, macOS, and Linux without requiring NTFS case-sensitivity attributes.

### Correction 2: Governance Accuracy (Stop Matching & Provenance Distinction)
- **Issue Identified:** Upstream metadata contained a generic statement claiming stop matching was based on names. Furthermore, the upstream provenance package was not explicitly differentiated from the project input ZIP.
- **Solution Implemented:**
  - Corrected the governance document to accurately describe the operational stop-to-departure matching: in this dataset, scheduled departures are associated with stops strictly via exact GTFS `stop_id` relational joins (`scheduled_departures.stop_id == bus_stops.stop_id`). All 18,439 departures match one of the 26 Sholinganallur stops with 0 orphan records.
  - Preserved the important upstream context that the initial extraction of the 26 stops from the Greater Chennai GTFS feed was performed using locality name criteria.
  - Preserved all genuine dataset limitations: static timetable nature, no real-time GPS tracking, absence of passenger occupancy/crowd density, quarantined coordinates for out-of-bounds stops `380` and `168`, GTFS post-midnight continuous time notation, and same-trip direct connections without multi-leg transfers.
  - Clarified provenance hierarchy: **Upstream GTFS Source Provenance** (`static_mtc_gtfs_data(3).zip` / `chennai.bus.gtfs.zip`) vs. **Localized Project Input Archive** (`data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip`).

---

## 2. Regression Test Results

| # | Regression Test Requirement | Expected Target | Actual Result | Status |
| :---: | :--- | :--- | :--- | :---: |
| 1 | **Total Documents Generated** | Exactly 86 documents | 86 documents | **PASSED** |
| 2 | **Bus Stop Profiles** | Exactly 26 stop profiles | 26 stop profiles | **PASSED** |
| 3 | **Distinct Route Profiles** | Exactly 58 route profiles | 58 route profiles | **PASSED** |
| 4 | **Service Variants Representation** | All 278 variants mapped exactly once | 278 / 278 variants mapped (0 duplicate, 0 orphan) | **PASSED** |
| 5 | **Corridor Guide Connections** | All 170 direct runs represented | 170 / 170 direct runs across lines 19, 19A, M51, 19BET | **PASSED** |
| 6 | **102X vs 102x Independence** | Separate docs, IDs, and content | `doc_route_102X_19066bd4` (9 variants) & `doc_route_102x_0bda064f` (1 variant) | **PASSED** |
| 7 | **Idempotency & Hash Stability** | 100% hash and content match across runs | Zero diff across repeated executions | **PASSED** |
| 8 | **Case-Insensitive Filesystem Portability** | All 86 filenames unique under lowercase | 86 distinct lowercase IDs (0 collisions) on default Windows NTFS | **PASSED** |

---

## 3. Document Inventory & Taxonomy

| Document Type | Source Processed File | Output Documents | ID Format / Sample | Purpose in RAG Pipeline |
| :--- | :--- | :---: | :--- | :--- |
| **Stop Profile** | `bus_stops.json` | 26 | `doc_stop_{stop_id}` (e.g. `doc_stop_elxmPCDf`) | Grounding for stop names, coordinates, mapping status, serving route lines, departure tallies |
| **Consolidated Route Profile** | `route_service_summary.json` | 58 | `doc_route_{slug}_{hash}` (e.g. `doc_route_102_68d601d3`) | Grounding for public bus routes, terminals, service windows, operational GTFS variants |
| **Corridor Travel Guide** | `direct_t_nagar_connections.json` | 1 | `doc_corridor_sholinganallur_t_nagar` | Grounding for direct Sholinganallur-to-T. Nagar commuter services, routes, travel duration, key waypoints |
| **System Governance & Limits** | `dataset_metadata.json` | 1 | `doc_system_governance_limitations` | Grounding for assistant disclaimers, static timetable boundaries, no real-time GPS, relational stop_id matching |
| **Total** | *All Canonical Inputs* | **86** | - | Complete self-contained semantic knowledge base |

---

## 4. Cryptographic Manifest & Hash Catalog

| # | Document ID | Type | Title | Chars | Lines | SHA-256 Digest |
| :---: | :--- | :--- | :--- | :---: | :---: | :--- |
| 1 | `doc_stop_04f86069a53d1b4ba4c32567ecb51a40` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR (Stop ID: 04f86069a53d1b4ba4c32567ecb51a40) | 838 | 21 | `b84e3b9cba410f10...` |
| 2 | `doc_stop_12518ace15a486436b2a17347ed9dd7c` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR NUCLUEUS COMPANY (Stop ID: 12518ace15a486436b2a17347ed9dd7c) | 872 | 21 | `e1559e43a4484c3f...` |
| 3 | `doc_stop_168` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR P.U.OFFICE (Stop ID: 168) | 1341 | 22 | `2e7a0d0f0eae8055...` |
| 4 | `doc_stop_172bacebb7dba857432588d1cb692048` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR KUMARAN NAGAR (Stop ID: 172bacebb7dba857432588d1cb692048) | 872 | 21 | `dfd8aa495c3ee046...` |
| 5 | `doc_stop_24e7a438c8360f233d74a8f962941d7f` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR (Stop ID: 24e7a438c8360f233d74a8f962941d7f) | 845 | 21 | `566f832cf12f89e9...` |
| 6 | `doc_stop_374cd9d37f0af09bb60465a548d6d842` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR KUMARAN NAGAR (Stop ID: 374cd9d37f0af09bb60465a548d6d842) | 863 | 21 | `622f8b8db003b0c4...` |
| 7 | `doc_stop_380` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR (Stop ID: 380) | 1138 | 22 | `5faa95bc6a6fd908...` |
| 8 | `doc_stop_BMDeXhVp` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR HCL COMPANY (Stop ID: BMDeXhVp) | 849 | 21 | `a6de8e025ca71426...` |
| 9 | `doc_stop_GMrJWgHs` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR KUMARAN NAGAR (Stop ID: GMrJWgHs) | 953 | 21 | `0178722ec392b310...` |
| 10 | `doc_stop_HxnLIWQq` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR ACCENTURE (Stop ID: HxnLIWQq) | 943 | 21 | `766495e9b9781e43...` |
| 11 | `doc_stop_OaJoPLeC` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR NUCLUEUS COMPANY (Stop ID: OaJoPLeC) | 864 | 21 | `4a408c7c9a2cbc66...` |
| 12 | `doc_stop_ZqBGCTqq` | `stop_profile` | Bus Stop Profile: P U OFFICE SHOLINGANALLUR (Stop ID: ZqBGCTqq) | 955 | 21 | `5196d46ed98c5e09...` |
| 13 | `doc_stop_aZtqChJJ` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR ACCENTURE (Stop ID: aZtqChJJ) | 940 | 21 | `a7f8d0c944ac8634...` |
| 14 | `doc_stop_ddf9b7895d25353b687b012d51771836` | `stop_profile` | Bus Stop Profile: P U OFFICE SHOLINGANALLUR (Stop ID: ddf9b7895d25353b687b012d51771836) | 964 | 21 | `bc0a5328edad5c96...` |
| 15 | `doc_stop_deapEAbC` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR (Stop ID: deapEAbC) | 800 | 21 | `eac31300f646f6c9...` |
| 16 | `doc_stop_elxmPCDf` | `stop_profile` | Bus Stop Profile: GOVERNMENT SCHOOL SHOLINGANALLUR (Stop ID: elxmPCDf) | 984 | 21 | `67cea1cf0a59dfb3...` |
| 17 | `doc_stop_fd33ffa78a20fbe7d4ee10febdad0c08` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR (Stop ID: fd33ffa78a20fbe7d4ee10febdad0c08) | 858 | 21 | `5bbc259c0a0d292d...` |
| 18 | `doc_stop_gHieypBQ` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR HCL COMPANY (Stop ID: gHieypBQ) | 849 | 21 | `e47251c973cec2fd...` |
| 19 | `doc_stop_jQytwadI` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR (Stop ID: jQytwadI) | 800 | 21 | `5a5125922be8f5d9...` |
| 20 | `doc_stop_nunLDRbE` | `stop_profile` | Bus Stop Profile: GOVERNMENT SCHOOL SHOLINGANALLUR (Stop ID: nunLDRbE) | 971 | 21 | `8f5746c7a3ee62ce...` |
| 21 | `doc_stop_oRUBDfuD` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR PONNIAMMAN TEMPLE (Stop ID: oRUBDfuD) | 971 | 21 | `21d1d3d507f13e13...` |
| 22 | `doc_stop_qQJgvOFz` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR KUMARAN NAGAR (Stop ID: qQJgvOFz) | 963 | 21 | `59bb1e3861d07fad...` |
| 23 | `doc_stop_qVPPKIYf` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR NUCLUEUS COMPANY (Stop ID: qVPPKIYf) | 856 | 21 | `3611149054b738ea...` |
| 24 | `doc_stop_tTjpyeuP` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR PONNIAMMAN TEMPLE (Stop ID: tTjpyeuP) | 978 | 21 | `74265270a06f05c6...` |
| 25 | `doc_stop_tcbhlVZo` | `stop_profile` | Bus Stop Profile: SHOLINGANALLUR (Stop ID: tcbhlVZo) | 811 | 21 | `891da9be4f3b4fbc...` |
| 26 | `doc_stop_wrFiwNtD` | `stop_profile` | Bus Stop Profile: P U OFFICE SHOLINGANALLUR (Stop ID: wrFiwNtD) | 842 | 21 | `0d82e8749df234a2...` |
| 27 | `doc_route_102_37834f2f` | `route_profile` | Route Profile: Route 102 (12 Service Variants) | 3873 | 89 | `8f52ea85e62daf9f...` |
| 28 | `doc_route_102A_34722ae8` | `route_profile` | Route Profile: Route 102A (2 Service Variants) | 1434 | 29 | `e8cbbcc2e1c01b31...` |
| 29 | `doc_route_102C_b8e2e5e4` | `route_profile` | Route Profile: Route 102C (6 Service Variants) | 2346 | 53 | `1f3540aac31f03bf...` |
| 30 | `doc_route_102C_CD_4bf5e04e` | `route_profile` | Route Profile: Route 102C CD (2 Service Variants) | 1327 | 29 | `bab39e92534c78ab...` |
| 31 | `doc_route_102CCT_41fb6448` | `route_profile` | Route Profile: Route 102CCT (2 Service Variants) | 1328 | 29 | `94c0173c1697b988...` |
| 32 | `doc_route_102CT_7bdd93c4` | `route_profile` | Route Profile: Route 102CT (17 Service Variants) | 5192 | 119 | `44bf10eac47895a6...` |
| 33 | `doc_route_102E_018917ed` | `route_profile` | Route Profile: Route 102E (2 Service Variants) | 1320 | 29 | `f54c64b5cfc23038...` |
| 34 | `doc_route_102K_8bd479ca` | `route_profile` | Route Profile: Route 102K (1 Service Variants) | 1052 | 23 | `6a8d091f0cc6d17f...` |
| 35 | `doc_route_102M_fb7e4612` | `route_profile` | Route Profile: Route 102M (2 Service Variants) | 1322 | 29 | `caf327558ee0d5da...` |
| 36 | `doc_route_102P_a8774f31` | `route_profile` | Route Profile: Route 102P (14 Service Variants) | 4486 | 101 | `525ca1144187cc44...` |
| 37 | `doc_route_102P_CD_24f46c1a` | `route_profile` | Route Profile: Route 102P CD (2 Service Variants) | 1325 | 29 | `ec67c69361eefc6b...` |
| 38 | `doc_route_102PCT_d1798435` | `route_profile` | Route Profile: Route 102PCT (9 Service Variants) | 3178 | 71 | `31914e0456b8f910...` |
| 39 | `doc_route_102S_6daea2bd` | `route_profile` | Route Profile: Route 102S (2 Service Variants) | 1259 | 29 | `b25319a3f7e30c0f...` |
| 40 | `doc_route_102X_19066bd4` | `route_profile` | Route Profile: Route 102X (9 Service Variants) | 3171 | 71 | `8f1304947fab3d3d...` |
| 41 | `doc_route_102X_CD_f900c3e3` | `route_profile` | Route Profile: Route 102X CD (2 Service Variants) | 1285 | 29 | `a2bf41eac9665697...` |
| 42 | `doc_route_102x_0bda064f` | `route_profile` | Route Profile: Route 102x (1 Service Variants) | 1040 | 23 | `d67a38de7f02b8c5...` |
| 43 | `doc_route_119_3038bfb5` | `route_profile` | Route Profile: Route 119 (10 Service Variants) | 3368 | 77 | `20bb009d4c07dfe2...` |
| 44 | `doc_route_119G_f1e115d1` | `route_profile` | Route Profile: Route 119G (4 Service Variants) | 1854 | 41 | `6a8120fe333449e1...` |
| 45 | `doc_route_19_9400f1b2` | `route_profile` | Route Profile: Route 19 (14 Service Variants) | 4491 | 101 | `36c71e7dd03ed378...` |
| 46 | `doc_route_19A_69a56467` | `route_profile` | Route Profile: Route 19A (5 Service Variants) | 2237 | 47 | `3a0a39204c5f57ca...` |
| 47 | `doc_route_19AX_235a0219` | `route_profile` | Route Profile: Route 19AX (2 Service Variants) | 1342 | 29 | `11f43275802c1b59...` |
| 48 | `doc_route_19B_f6a6a85b` | `route_profile` | Route Profile: Route 19B (4 Service Variants) | 1855 | 41 | `f1af1cc0359b3311...` |
| 49 | `doc_route_19BET_3fa73211` | `route_profile` | Route Profile: Route 19BET (8 Service Variants) | 3002 | 65 | `5b070a0b7bd35bba...` |
| 50 | `doc_route_19D_6fd206a0` | `route_profile` | Route Profile: Route 19D (10 Service Variants) | 3343 | 77 | `9d7680fb2424e437...` |
| 51 | `doc_route_19D_PAD_2714f18a` | `route_profile` | Route Profile: Route 19D PAD (2 Service Variants) | 1310 | 29 | `eae6e17b010a364d...` |
| 52 | `doc_route_19PCT_8f531671` | `route_profile` | Route Profile: Route 19PCT (2 Service Variants) | 1286 | 29 | `bfbc1da9837964ff...` |
| 53 | `doc_route_221_67e9c3ac` | `route_profile` | Route Profile: Route 221 (4 Service Variants) | 1982 | 41 | `e6301cde38bc5906...` |
| 54 | `doc_route_221CT_3e867ea2` | `route_profile` | Route Profile: Route 221CT (4 Service Variants) | 1801 | 41 | `d46a8c95bc6c1985...` |
| 55 | `doc_route_23C_0932433f` | `route_profile` | Route Profile: Route 23C (4 Service Variants) | 1844 | 41 | `c21c647ab83a2403...` |
| 56 | `doc_route_519T_9b51f6cc` | `route_profile` | Route Profile: Route 519T (2 Service Variants) | 1374 | 29 | `9cf62b4981b28e18...` |
| 57 | `doc_route_523_a1e8154b` | `route_profile` | Route Profile: Route 523 (6 Service Variants) | 2396 | 53 | `a5b655ef06cdcb79...` |
| 58 | `doc_route_523A_0b516ab4` | `route_profile` | Route Profile: Route 523A (4 Service Variants) | 1948 | 41 | `49381637999f310a...` |
| 59 | `doc_route_552K_5d7b85f9` | `route_profile` | Route Profile: Route 552K (6 Service Variants) | 2267 | 53 | `ddb30d4916aa2cdd...` |
| 60 | `doc_route_555G_2b7b93fc` | `route_profile` | Route Profile: Route 555G (2 Service Variants) | 1366 | 29 | `dacfd07ebb36ebaf...` |
| 61 | `doc_route_555P_b7a10ba1` | `route_profile` | Route Profile: Route 555P (4 Service Variants) | 1901 | 41 | `66a7d01a1221e0c8...` |
| 62 | `doc_route_555S_d7375511` | `route_profile` | Route Profile: Route 555S (2 Service Variants) | 1363 | 29 | `ff675da84390a0eb...` |
| 63 | `doc_route_568B_3c3adb91` | `route_profile` | Route Profile: Route 568B (4 Service Variants) | 1812 | 41 | `aabffdb86989614c...` |
| 64 | `doc_route_568CT_5bd6ab14` | `route_profile` | Route Profile: Route 568CT (1 Service Variants) | 1048 | 23 | `e36757ad8b0ea805...` |
| 65 | `doc_route_570_085b2a38` | `route_profile` | Route Profile: Route 570 (10 Service Variants) | 3442 | 77 | `23e82ec335f4f2bb...` |
| 66 | `doc_route_570CT_1c56d69f` | `route_profile` | Route Profile: Route 570CT (2 Service Variants) | 1342 | 29 | `c9a42881390c9a8e...` |
| 67 | `doc_route_570ET_c3c1b590` | `route_profile` | Route Profile: Route 570ET (5 Service Variants) | 2150 | 47 | `26fe1af6fd8810af...` |
| 68 | `doc_route_570P_70327d52` | `route_profile` | Route Profile: Route 570P (4 Service Variants) | 1863 | 41 | `78eb699c76a6df3c...` |
| 69 | `doc_route_570T_2facc23d` | `route_profile` | Route Profile: Route 570T (2 Service Variants) | 1284 | 29 | `af1570341842ae41...` |
| 70 | `doc_route_570V_360e7e1a` | `route_profile` | Route Profile: Route 570V (3 Service Variants) | 1538 | 35 | `59d7d126d385a5b1...` |
| 71 | `doc_route_76V_b6a5c06f` | `route_profile` | Route Profile: Route 76V (2 Service Variants) | 1324 | 29 | `7f807acdefef2c7d...` |
| 72 | `doc_route_95_ad48ff99` | `route_profile` | Route Profile: Route 95 (7 Service Variants) | 2797 | 59 | `621f4f82bea27c47...` |
| 73 | `doc_route_95G_1e40872e` | `route_profile` | Route Profile: Route 95G (3 Service Variants) | 1661 | 35 | `e5950973159cacfc...` |
| 74 | `doc_route_95X_bd66fbd4` | `route_profile` | Route Profile: Route 95X (4 Service Variants) | 1913 | 41 | `9f525c97be18084d...` |
| 75 | `doc_route_95XCT_6e71735f` | `route_profile` | Route Profile: Route 95XCT (9 Service Variants) | 3276 | 71 | `0d2c0502b71ced16...` |
| 76 | `doc_route_99_8c1f1046` | `route_profile` | Route Profile: Route 99 (4 Service Variants) | 1810 | 41 | `8debfc1e94d18b39...` |
| 77 | `doc_route_99A_4eed2e87` | `route_profile` | Route Profile: Route 99A (10 Service Variants) | 3461 | 77 | `41ba45bb361cd24a...` |
| 78 | `doc_route_99CT_511e46dd` | `route_profile` | Route Profile: Route 99CT (3 Service Variants) | 1562 | 35 | `dd68658f1b166468...` |
| 79 | `doc_route_99X_e636a74d` | `route_profile` | Route Profile: Route 99X (5 Service Variants) | 2114 | 47 | `91b34068b808483f...` |
| 80 | `doc_route_B19_d614cbb6` | `route_profile` | Route Profile: Route B19 (6 Service Variants) | 2412 | 53 | `cc3a5fcf74ede363...` |
| 81 | `doc_route_B19CT_73b1feb7` | `route_profile` | Route Profile: Route B19CT (2 Service Variants) | 1378 | 29 | `9ff4e353f057ec2e...` |
| 82 | `doc_route_M51_44eabd33` | `route_profile` | Route Profile: Route M51 (4 Service Variants) | 2016 | 41 | `e4a4d3d48380dfcc...` |
| 83 | `doc_route_MAA2_e3e29c07` | `route_profile` | Route Profile: Route MAA2 (2 Service Variants) | 1337 | 29 | `6064c8c744c6bbc8...` |
| 84 | `doc_route_MAA2CT_07501c88` | `route_profile` | Route Profile: Route MAA2CT (2 Service Variants) | 1339 | 29 | `8c5738e92e1b7432...` |
| 85 | `doc_corridor_sholinganallur_t_nagar` | `corridor_guide` | Corridor Travel Guide: Sholinganallur to T. Nagar (Direct MTC Bus Service) | 2632 | 37 | `301b8de5e7faa1ab...` |
| 86 | `doc_system_governance_limitations` | `system_governance` | System Governance: MTC Transit Data Limitations & Operational Boundaries | 3376 | 32 | `b8905f2f76ed4659...` |

---

## 5. Scope Boundary Compliance

- [x] **No chunking performed:** Documents are emitted as complete semantic parent documents.
- [x] **No embedding calls or Gemini API usage:** No tokens spent; no API keys accessed.
- [x] **No database connection or SQL:** No tables created; Supabase state untouched.
- [x] **No modification of source files:** `data/raw/` and `data/processed/` unchanged.
- [x] **No modification of `.env`:** Configuration remains secure.
- [x] **Step 5B not started:** Batch chunking and document splitting held for subsequent review.

