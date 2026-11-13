import csv
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("uvicorn.error")

DEFAULT_ZIP_PATH = Path("data/raw/Simple_RouteWise_Sholinganallur_Dataset.zip")
ZIP_INTERNAL_DIR = "Simple_RouteWise_Sholinganallur_Extract"


class DataLoader:
    """
    Safe, read-only loader for the Sholinganallur GTFS dataset archive.
    Reads all raw files directly from the ZIP archive without modifying the source archive.
    """

    def __init__(self, zip_path: Path | str = DEFAULT_ZIP_PATH):
        self.zip_path = Path(zip_path)
        if not self.zip_path.exists():
            raise FileNotFoundError(f"Source dataset ZIP not found at: {self.zip_path}")

    def _get_internal_path(self, filename: str) -> str:
        return f"{ZIP_INTERNAL_DIR}/{filename}"

    def load_bus_stops_raw(self) -> List[Dict[str, Any]]:
        """
        Reads bus_stops.csv using utf-8-sig encoding to properly strip the UTF-8 BOM.
        """
        path = self._get_internal_path("bus_stops.csv")
        with zipfile.ZipFile(self.zip_path, "r") as z:
            with z.open(path) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                return list(reader)

    def load_route_service_summary_raw(self) -> List[Dict[str, Any]]:
        """
        Reads route_service_summary.csv using utf-8-sig encoding.
        """
        path = self._get_internal_path("route_service_summary.csv")
        with zipfile.ZipFile(self.zip_path, "r") as z:
            with z.open(path) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                return list(reader)

    def load_scheduled_departures_raw(self) -> List[Dict[str, Any]]:
        """
        Reads scheduled_departures.csv using utf-8-sig encoding.
        """
        path = self._get_internal_path("scheduled_departures.csv")
        with zipfile.ZipFile(self.zip_path, "r") as z:
            with z.open(path) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                return list(reader)

    def load_direct_t_nagar_connections_raw(self) -> List[Dict[str, Any]]:
        """
        Reads direct_t_nagar_connections.csv using utf-8-sig encoding.
        """
        path = self._get_internal_path("direct_t_nagar_connections.csv")
        with zipfile.ZipFile(self.zip_path, "r") as z:
            with z.open(path) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                return list(reader)

    def load_area_summary_raw(self) -> Dict[str, Any]:
        """
        Reads area_summary.json.
        """
        path = self._get_internal_path("area_summary.json")
        with zipfile.ZipFile(self.zip_path, "r") as z:
            with z.open(path) as f:
                return json.load(f)

    def load_dataset_json_raw(self) -> Dict[str, Any]:
        """
        Reads the canonical routewise_sholinganallur_dataset.json.
        """
        path = self._get_internal_path("routewise_sholinganallur_dataset.json")
        with zipfile.ZipFile(self.zip_path, "r") as z:
            with z.open(path) as f:
                return json.load(f)

    def load_readme_raw(self) -> str:
        """
        Reads README.txt as raw text.
        """
        path = self._get_internal_path("README.txt")
        with zipfile.ZipFile(self.zip_path, "r") as z:
            with z.open(path) as f:
                return f.read().decode("utf-8", errors="replace")

    def load_all_raw(self) -> Dict[str, Any]:
        """
        Loads all seven files from the ZIP archive into memory.
        """
        logger.info("Reading raw dataset files from archive: %s", self.zip_path)
        return {
            "bus_stops": self.load_bus_stops_raw(),
            "route_service_summary": self.load_route_service_summary_raw(),
            "scheduled_departures": self.load_scheduled_departures_raw(),
            "direct_t_nagar_connections": self.load_direct_t_nagar_connections_raw(),
            "area_summary": self.load_area_summary_raw(),
            "dataset_json": self.load_dataset_json_raw(),
            "readme": self.load_readme_raw(),
        }

