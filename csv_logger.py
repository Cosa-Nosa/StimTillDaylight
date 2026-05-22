"""
csv_logger.py — Dynamic-schema CSV writer.

Old version hardcoded FIELDNAMES. New version unions all keys it ever sees
across records, rewriting the header if a new column appears. This means
adding a new tracked stat (by adding a YAML entry) doesn't require editing
this file.
"""

import os
import csv
from typing import Optional


class CSVLogger:
    def __init__(self, path: str):
        self.path = path
        self._fieldnames: list[str] = []
        # Always-present columns first
        self._base_fields = [
            "match_start_iso", "match_duration_s", "match_outcome", "survivor",
        ]
        self._load_existing_header()

    def _load_existing_header(self):
        if not os.path.exists(self.path) or os.path.getsize(self.path) == 0:
            return
        try:
            with open(self.path, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header:
                    self._fieldnames = header
        except Exception as ex:
            print(f"[CSV] Could not read existing header from {self.path}: {ex}")

    def write(self, record: dict):
        if record is None:
            return

        # Compute the union of existing fields + new record's keys
        new_keys = [k for k in record.keys() if k not in self._fieldnames]
        if new_keys or not self._fieldnames:
            # Rebuild fieldnames: base fields first (if present), then alpha rest
            all_keys = set(self._fieldnames) | set(record.keys())
            base = [k for k in self._base_fields if k in all_keys]
            rest = sorted(k for k in all_keys if k not in self._base_fields)
            new_fieldnames = base + rest

            if new_fieldnames != self._fieldnames:
                self._rewrite_with_new_header(new_fieldnames)
                self._fieldnames = new_fieldnames

        # Append the row
        with open(self.path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self._fieldnames, extrasaction="ignore")
            # Fill missing keys with empty
            row = {k: record.get(k, "") for k in self._fieldnames}
            writer.writerow(row)
        print(f"[CSV] Wrote row to {self.path}")

    def _rewrite_with_new_header(self, new_fieldnames: list[str]):
        """Reads existing CSV, rewrites with the new (expanded) header."""
        existing_rows = []
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
            try:
                with open(self.path, "r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    existing_rows = list(reader)
            except Exception as ex:
                print(f"[CSV] Could not read existing rows for header expansion: {ex}")
                existing_rows = []

        with open(self.path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=new_fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in existing_rows:
                writer.writerow({k: row.get(k, "") for k in new_fieldnames})

    def close(self):
        pass  # we open/close per write
