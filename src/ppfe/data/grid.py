"""Regular lat/lon grid used for staging, inference, and serving.

Row 0 is the NORTH edge (image convention) so arrays map directly onto
map image overlays without flipping.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Grid:
    west: float
    south: float
    east: float
    north: float
    res: float  # degrees

    @property
    def width(self) -> int:
        return max(1, int(round((self.east - self.west) / self.res)))

    @property
    def height(self) -> int:
        return max(1, int(round((self.north - self.south) / self.res)))

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def lons(self) -> np.ndarray:
        return self.west + (np.arange(self.width) + 0.5) * self.res

    @property
    def lats(self) -> np.ndarray:
        return self.north - (np.arange(self.height) + 0.5) * self.res

    def mesh(self) -> tuple[np.ndarray, np.ndarray]:
        return np.meshgrid(self.lons, self.lats)

    def refine(self, factor: int) -> "Grid":
        return Grid(self.west, self.south, self.east, self.north, self.res / factor)

    def index(self, lon: float, lat: float) -> tuple[int, int]:
        """(row, col) of the cell containing lon/lat, clipped to grid."""
        c = int((lon - self.west) / self.res)
        r = int((self.north - lat) / self.res)
        return min(max(r, 0), self.height - 1), min(max(c, 0), self.width - 1)

    def cell_center(self, r: int, c: int) -> tuple[float, float]:
        return float(self.lons[c]), float(self.lats[r])

    def sample(self, arr: np.ndarray, lon: float, lat: float) -> float:
        r, c = self.index(lon, lat)
        return float(arr[r, c])

    def corners(self) -> list[list[float]]:
        """MapLibre image-source corner order: TL, TR, BR, BL."""
        return [
            [self.west, self.north],
            [self.east, self.north],
            [self.east, self.south],
            [self.west, self.south],
        ]

    def to_dict(self) -> dict:
        return {
            "west": self.west, "south": self.south, "east": self.east, "north": self.north,
            "res": self.res, "width": self.width, "height": self.height,
        }

    @classmethod
    def from_bbox(cls, bbox: list[float] | tuple[float, ...], res: float) -> "Grid":
        w, s, e, n = bbox
        # Snap to the resolution so cached staging keys are stable.
        w = np.floor(w / res) * res
        s = np.floor(s / res) * res
        e = np.ceil(e / res) * res
        n = np.ceil(n / res) * res
        return cls(float(w), float(s), float(e), float(n), float(res))
