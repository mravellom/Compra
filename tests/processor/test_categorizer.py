"""
Unit Tests — Product Categorizer.

Tests keyword-based category classification across 18 categories.
"""
import pytest

from processor.categorizer import classify_product


class TestClassifyProduct:

    def test_laptops(self):
        assert classify_product("MacBook Pro 14 M3 512GB") == "Laptops"
        assert classify_product("ASUS ROG Strix laptop gaming") == "Laptops"

    def test_smartphones(self):
        assert classify_product("iPhone 15 Pro Max 256GB") == "Smartphones"
        assert classify_product("Samsung Galaxy S24 Ultra") == "Smartphones"

    def test_tablets(self):
        assert classify_product("iPad Air 5th Generation") == "Tablets"
        assert classify_product("Samsung Galaxy Tab S9") == "Tablets"

    def test_headphones(self):
        assert classify_product("Sony WH-1000XM5 headphones") == "Headphones"
        assert classify_product("AirPods Pro 2nd Generation") == "Headphones"

    def test_speakers(self):
        assert classify_product("JBL Flip 6 Portable Speaker") == "Speakers"

    def test_gaming_consoles(self):
        assert classify_product("PlayStation 5 PS5 Console") == "Gaming Consoles"
        assert classify_product("Nintendo Switch OLED") == "Gaming Consoles"
        assert classify_product("Xbox Series X 1TB") == "Gaming Consoles"

    def test_cameras(self):
        assert classify_product("Canon EOS R6 Mark II Camera") == "Cameras"

    def test_tvs(self):
        assert classify_product("Samsung 55 inch 4K TV Monitor") == "TVs & Monitors"

    def test_drones(self):
        assert classify_product("DJI Mini 4 Pro Drone") == "Drones"

    def test_gpus(self):
        assert classify_product("NVIDIA RTX 4090 GPU Graphics Card") == "GPUs"

    def test_storage(self):
        assert classify_product("WD Elements External Hard Drive 4TB USB") == "Storage"
        assert classify_product("SanDisk microSD 256GB memory card") == "Storage"

    def test_unknown_category(self):
        result = classify_product("Random thing without category keywords")
        assert result == "Other"

    def test_case_insensitive(self):
        assert classify_product("IPHONE 15 PRO MAX") == "Smartphones"
        assert classify_product("macbook pro laptop") == "Laptops"
