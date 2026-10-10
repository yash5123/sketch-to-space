"""Geometry and bounding box conventions for Sketch-to-Space.

All spatial bounding boxes in the system follow one canonical convention:
    Box(x0, y0, x1, y1) in page pixels, where:
      - x0: left edge (minimum x)
      - y0: top edge (minimum y)
      - x1: right edge (maximum x)
      - y1: bottom edge (maximum y)
"""

from typing import Any, NamedTuple, Optional, Sequence, Union


class Box(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def xc(self) -> float:
        """Horizontal center coordinate."""
        return (self.x0 + self.x1) / 2.0

    @property
    def yc(self) -> float:
        """Vertical center coordinate."""
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        """Box width in pixels."""
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        """Box height in pixels."""
        return max(0.0, self.y1 - self.y0)

    def to_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    @classmethod
    def from_coords(cls, x0: float, y0: float, x1: float, y1: float) -> "Box":
        """Construct Box ensuring x0 <= x1 and y0 <= y1."""
        xmin, xmax = (float(x0), float(x1)) if x0 <= x1 else (float(x1), float(x0))
        ymin, ymax = (float(y0), float(y1)) if y0 <= y1 else (float(y1), float(y0))
        return cls(round(xmin, 1), round(ymin, 1), round(xmax, 1), round(ymax, 1))

    @classmethod
    def from_any(cls, val: Any) -> Optional["Box"]:
        """Parse Box from NamedTuple, dict, sequence, or existing Box."""
        if val is None:
            return None
        if isinstance(val, Box):
            return val
        if isinstance(val, dict):
            if "x0" in val and "y0" in val and "x1" in val and "y1" in val:
                return cls.from_coords(val["x0"], val["y0"], val["x1"], val["y1"])
            if "box" in val:
                return cls.from_any(val["box"])
            if "ocr_box" in val:
                return cls.from_any(val["ocr_box"])
        if isinstance(val, (list, tuple)) and len(val) == 4:
            return cls.from_coords(val[0], val[1], val[2], val[3])
        return None
