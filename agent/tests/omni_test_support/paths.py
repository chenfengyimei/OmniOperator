"""Stable repository anchors for tests moved independently from the product."""

from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TEST_ROOT.parent
REPOSITORY_ROOT = PRODUCT_ROOT.parent
