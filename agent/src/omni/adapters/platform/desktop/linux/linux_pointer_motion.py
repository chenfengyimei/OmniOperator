"""Plan signed core XTEST coordinates and raw valuators from proven native geometry/configuration; never sends input."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from functools import reduce
from math import gcd, lcm

from omni.adapters.platform.desktop.linux.linux_bus import failure
from omni.adapters.platform.desktop.linux.linux_pointer_config import PointerState
from omni.adapters.platform.desktop.linux.linux_pointer_target import PointerPoint

Matrix = tuple[Fraction, ...]


@dataclass(frozen=True)
class MotionPlan:
    point: PointerPoint
    width: int
    height: int
    request: tuple[int, int]
    raw: tuple[int, int]
    processed: tuple[int, int]
    screen: tuple[float, float]


def multiply(left: Matrix, right: Matrix) -> Matrix:
    return tuple(
        sum((left[row * 3 + k] * right[k * 3 + column] for k in range(3)), Fraction())
        for row in range(3)
        for column in range(3)
    )


def inverse(value: Matrix) -> Matrix:
    a, b, c, d, e, f, g, h, i = value
    cofactors = (
        e * i - f * h,
        c * h - b * i,
        b * f - c * e,
        f * g - d * i,
        a * i - c * g,
        c * d - a * f,
        d * h - e * g,
        b * g - a * h,
        a * e - b * d,
    )
    determinant = a * cofactors[0] + b * cofactors[3] + c * cofactors[6]
    if not determinant:
        raise failure("desktop_input_unverified")
    return tuple(item / determinant for item in cofactors)


def project(matrix: Matrix, x: Fraction, y: Fraction) -> tuple[Fraction, Fraction]:
    divisor = matrix[6] * x + matrix[7] * y + matrix[8]
    if not divisor:
        raise failure("desktop_input_unverified")
    return (matrix[0] * x + matrix[1] * y + matrix[2]) / divisor, (matrix[3] * x + matrix[4] * y + matrix[5]) / divisor


def affine(sx: Fraction, sy: Fraction, tx: Fraction, ty: Fraction) -> Matrix:
    return (sx, Fraction(), tx, Fraction(), sy, ty, Fraction(), Fraction(), Fraction(1))


def native_multiply(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    # Explicit order avoids Python 3.12's compensated float sum: the native
    # matrix implementation uses ordinary sequential double arithmetic.
    return tuple(
        left[row * 3] * right[column] + left[row * 3 + 1] * right[3 + column] + left[row * 3 + 2] * right[6 + column]
        for row in range(3)
        for column in range(3)
    )


class Coordinates:
    def __init__(self, state: PointerState, width: int, height: int) -> None:
        axes = {axis.number: axis for axis in state.source.axes}
        if not {0, 1} <= axes.keys() or not 1 <= width <= 32767 or not 1 <= height <= 32767:
            raise failure("desktop_input_unverified")
        self.axes = axes[0], axes[1]
        self.extent = width, height
        bounds = [(Fraction(axis.minimum, 1 << 32), Fraction(axis.maximum, 1 << 32)) for axis in self.axes]
        spans = [high - low + 1 for low, high in bounds]
        if any(not span for span in spans):
            raise failure("desktop_input_unverified")
        scales = [
            (high - low + 1) / edge if low < high else Fraction(1)
            for (low, high), edge in zip(bounds, self.extent, strict=True)
        ]
        origins = [low if low < high else Fraction() for low, high in bounds]
        screen_to_device = affine(*scales, *origins)
        normalize = affine(1 / spans[0], 1 / spans[1], -bounds[0][0] / spans[0], -bounds[1][0] / spans[1])
        if len(state.source_transform) != 9 or not all(math.isfinite(value) for value in state.source_transform):
            raise failure("desktop_input_unverified")
        user = tuple(Fraction(value) for value in state.source_transform)
        self.device = multiply(multiply(inverse(normalize), user), normalize)
        native_bounds = [(axis.minimum / (1 << 32), axis.maximum / (1 << 32)) for axis in self.axes]
        nx, ny = (high - low + 1 for low, high in native_bounds)
        if not nx or not ny:
            raise failure("desktop_input_unverified")
        lx, ly = native_bounds[0][0], native_bounds[1][0]
        native_scale = (nx, 0.0, lx, 0.0, ny, ly, 0.0, 0.0, 1.0)
        native_normalize = (1 / nx, 0.0, -lx / nx, 0.0, 1 / ny, -ly / ny, 0.0, 0.0, 1.0)
        self.native = native_multiply(native_multiply(native_scale, state.source_transform), native_normalize)
        self.mapping = multiply(multiply(inverse(screen_to_device), self.device), screen_to_device)
        self.undo = inverse(self.mapping)

    def forward(self, x: int, y: int) -> tuple[tuple[float, float], tuple[int, int]] | None:
        # Follow the server's native double operations, including range clip.
        # Exact rational geometry is used for searching, not to replace the
        # floating-point result the native server will actually publish.
        values: list[float] = []
        for coordinate, axis, edge in zip((x, y), self.axes, self.extent, strict=True):
            low, high = axis.minimum / (1 << 32), axis.maximum / (1 << 32)
            values.append(coordinate * (high + 1 - low) / edge + low if low < high else float(coordinate))
        matrix = self.native
        divisor = matrix[6] * values[0] + matrix[7] * values[1] + matrix[8]
        if not divisor or not math.isfinite(divisor):
            return None
        device = (
            (matrix[0] * values[0] + matrix[1] * values[1] + matrix[2]) / divisor,
            (matrix[3] * values[0] + matrix[4] * values[1] + matrix[5]) / divisor,
        )
        clipped: list[float] = []
        screen: list[float] = []
        for value, axis, edge in zip(device, self.axes, self.extent, strict=True):
            if not math.isfinite(value):
                return None
            low, high = axis.minimum / (1 << 32), axis.maximum / (1 << 32)
            if low < high:
                value = max(low, min(high, value))
            if not math.isfinite(value) or not -(1 << 31) <= value < (1 << 31):
                return None
            clipped.append(value)
            screen.append((value - low) * edge / (high + 1 - low) if low < high else value)
        if not all(math.isfinite(value) for value in screen):
            return None
        return (screen[0], screen[1]), (math.floor(clipped[0] * (1 << 32)), math.floor(clipped[1] * (1 << 32)))

    def rows(self, x: int, y: int, sign: int) -> tuple[tuple[int, int, int], ...]:
        denominator = lcm(*(value.denominator for value in self.mapping))
        integer = [int(value * denominator) for value in self.mapping]
        common = reduce(gcd, integer)
        if common:
            integer = [value // abs(common) for value in integer]
        a, b, c, d, e, f, g, h, i = integer
        # Integer inequalities A*u+B*v+C>=0 describe the target pixel.
        # A strict positive rational numerator is >=1 after denominator
        # clearing. Both signs of the projective denominator are searched.
        return (
            (sign * g, sign * h, sign * i - 1),
            (sign * (a - x * g), sign * (b - x * h), sign * (c - x * i)),
            (sign * ((x + 1) * g - a), sign * ((x + 1) * h - b), sign * ((x + 1) * i - c) - 1),
            (sign * (d - y * g), sign * (e - y * h), sign * (f - y * i)),
            (sign * ((y + 1) * g - d), sign * ((y + 1) * h - e), sign * ((y + 1) * i - f) - 1),
        )


def plan(point: PointerPoint, state: PointerState, width: int, height: int) -> MotionPlan:
    if not 0 <= point.x < width or not 0 <= point.y < height:
        raise failure("desktop_input_unverified")
    coordinates = Coordinates(state, width, height)
    # Smooth scrolling on X/Y can turn the nominal motion into additional
    # input. Other axes are retained in configuration but are not supplied.
    if any(int.from_bytes(scroll[6:8], "little") in {0, 1} for scroll in state.source.scrolls):
        raise failure("desktop_input_unverified")
    center_x, center_y = Fraction(point.x) + Fraction(1, 2), Fraction(point.y) + Fraction(1, 2)
    if coordinates.undo[6] * center_x + coordinates.undo[7] * center_y + coordinates.undo[8]:
        ideal_x, ideal_y = project(coordinates.undo, center_x, center_y)
    else:
        # A horizon through the center need not make the entire pixel
        # unreachable. The finite integer-domain scan below is still valid.
        ideal_x, ideal_y = Fraction(point.x), Fraction(point.y)

    def candidate(x: int, y: int) -> MotionPlan | None:
        if not -32768 <= x <= 32767 or not -32768 <= y <= 32767:
            return None
        result = coordinates.forward(x, y)
        if result is None:
            return None
        screen, processed = result
        if (
            not 0 <= screen[0] < width
            or not 0 <= screen[1] < height
            or (math.trunc(screen[0]), math.trunc(screen[1])) != (point.x, point.y)
        ):
            return None
        return MotionPlan(point, width, height, (x, y), (x << 32, y << 32), processed, screen)

    near_x, near_y = round(ideal_x), round(ideal_y)
    for x in sorted({near_x - 1, near_x, near_x + 1}, key=lambda value: (abs(value - ideal_x), value)):
        for y in sorted({near_y - 1, near_y, near_y + 1}, key=lambda value: (abs(value - ideal_y), value)):
            found = candidate(x, y)
            if found is not None:
                return found
    for sign in (1, -1):
        rows = coordinates.rows(point.x, point.y, sign)
        first_x, last_x = -32768, 32767
        for a, b, c in rows:
            if b:
                continue
            if a > 0:
                first_x = max(first_x, -(c // a))
            elif a < 0:
                last_x = min(last_x, (-c) // a)
            elif c < 0:
                first_x, last_x = 1, 0
                break
        # At most 65536 rows per sign; integer bounds avoid a quadratic
        # search or an unbounded rounding/guessing loop for thin rotations.
        for x in range(first_x, last_x + 1):
            low, high = -32768, 32767
            for a, b, c in rows:
                required = -a * x - c
                if b > 0:
                    low = max(low, -(-required // b))
                elif b < 0:
                    high = min(high, required // b)
                elif required > 0:
                    low, high = 1, 0
                    break
            if low <= high:
                for y in dict.fromkeys((max(low, min(high, near_y)), low, high, (low + high) // 2)):
                    found = candidate(x, y)
                    if found is not None:
                        return found
    raise failure("desktop_input_unverified")
