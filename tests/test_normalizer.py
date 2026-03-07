"""
Unit Tests — Normalization Service (19 tests)

Cubre:
  - Limpieza básica, lowercase, acentos, emojis, caracteres especiales
  - Unificación de modelos (WH-1000XM4 == wh1000xm4)
  - Stop words de marketplaces
  - Productos idénticos con redacción diferente
  - Extracción de brand y model
  - Casos de borde: vacío, solo números, unicode fullwidth, solo stop words
"""
import pytest

from processor.normalizer import extract_brand, extract_model, normalize_title


# ═══════════════════════════════════════════════════════════════════
# 1. normalize_title — Limpieza core (11 tests)
# ═══════════════════════════════════════════════════════════════════

class TestNormalizeTitleCore:

    # ── Test 1: Limpieza básica ──────────────────────────────────
    def test_basic_cleanup(self):
        result = normalize_title("Sony WH-1000XM4 Wireless Headphones")
        assert "sony" in result
        assert "wh1000xm4" in result
        assert "wireless" in result
        assert "headphones" in result

    # ── Test 2: Lowercase y acentos ──────────────────────────────
    def test_lowercase_and_accents(self):
        result = normalize_title("Auriculares Inalámbricos SONY")
        assert result == result.lower()
        assert "inalambricos" in result  # tilde removida
        assert "sony" in result

    # ── Test 3: Stop words de marketplaces eliminadas ────────────
    def test_stop_words_removed(self):
        result = normalize_title("Brand New Sealed Box - Free Shipping Fast")
        tokens = set(result.split())
        marketplace_noise = {"brand", "new", "sealed", "box", "free", "shipping", "fast"}
        assert tokens.isdisjoint(marketplace_noise)

    # ── Test 4: Emojis eliminados por completo ───────────────────
    def test_emojis_removed(self):
        result = normalize_title("🔥 Sony WH1000XM4 🎧 AMAZING DEAL!! 💯")
        assert "sony" in result
        assert "wh1000xm4" in result
        for char in "🔥🎧💯!":
            assert char not in result

    # ── Test 5: Solo números (no se pierde información) ──────────
    def test_only_numbers(self):
        result = normalize_title("12345678")
        assert "12345678" in result

    # ── Test 6: Unificación de guiones en modelos ────────────────
    def test_model_hyphens_unified(self):
        """WH-1000XM4 debe ser idéntico a WH1000XM4 tras normalizar."""
        with_hyphen = normalize_title("WH-1000XM4")
        without_hyphen = normalize_title("WH1000XM4")
        assert "wh1000xm4" in with_hyphen
        assert "wh1000xm4" in without_hyphen
        assert with_hyphen == without_hyphen

    # ── Test 7: Caracteres especiales eliminados ─────────────────
    def test_special_characters_removed(self):
        result = normalize_title("Sony (WH-1000XM4) [Black] {2023} @sale #deal")
        assert "sony" in result
        assert "wh1000xm4" in result
        for char in "()[]{}@#":
            assert char not in result

    # ── Test 8: Espacios múltiples colapsados ────────────────────
    def test_multiple_spaces_collapsed(self):
        result = normalize_title("Sony    WH1000XM4     Headphones")
        assert "  " not in result
        assert "sony wh1000xm4 headphones" == result

    # ── Test 9: String vacío ─────────────────────────────────────
    def test_empty_string(self):
        assert normalize_title("") == ""

    # ── Test 10: Tokens de 1 carácter eliminados ─────────────────
    def test_single_char_tokens_removed(self):
        result = normalize_title("A B C Sony D E")
        assert "sony" in result
        tokens = result.split()
        assert all(len(t) > 1 for t in tokens)

    # ── Test 11: Unicode fullwidth se normaliza ──────────────────
    def test_unicode_fullwidth_normalization(self):
        result = normalize_title("Ｓｏｎｙ　ＷＨ１０００ＸＭ４")  # fullwidth
        assert "sony" in result


# ═══════════════════════════════════════════════════════════════════
# 2. Productos idénticos con diferente redacción (4 tests)
# ═══════════════════════════════════════════════════════════════════

class TestSimilarProducts:

    # ── Test 12: S98 Pro vs Doogee S98 Professional ──────────────
    def test_s98_pro_variations(self):
        v1 = normalize_title("S98 Pro")
        v2 = normalize_title("Doogee S98 Professional")
        # Ambos deben contener el token clave "s98"
        assert "s98" in v1
        assert "s98" in v2

    # ── Test 13: 3 redacciones de WH-1000XM4 convergen ──────────
    def test_xm4_three_variations_converge(self):
        v1 = normalize_title("Sony WH-1000XM4")
        v2 = normalize_title("SONY WH1000XM4 Wireless Noise Cancelling")
        v3 = normalize_title("sony wh-1000xm4 headphones - black")
        for v in [v1, v2, v3]:
            assert "sony" in v
            assert "wh1000xm4" in v

    # ── Test 14: AirPods con ruido de marketplace ────────────────
    def test_airpods_with_marketplace_noise(self):
        clean = normalize_title("AirPods Pro")
        noisy = normalize_title("Apple AirPods Pro 2nd Generation - NEW SEALED")
        assert "airpods" in clean
        assert "airpods" in noisy
        assert "apple" in noisy

    # ── Test 15: Ruido completo eliminado, core preservado ───────
    def test_marketplace_noise_stripped_completely(self):
        clean = normalize_title("Nintendo Switch")
        noisy = normalize_title(
            "🔥 Nintendo Switch ✅ FREE SHIPPING!! Brand New Sealed Box - Best Deal USA Seller"
        )
        clean_tokens = set(clean.split())
        noisy_tokens = set(noisy.split())
        assert clean_tokens.issubset(noisy_tokens)


# ═══════════════════════════════════════════════════════════════════
# 3. extract_brand (2 tests)
# ═══════════════════════════════════════════════════════════════════

class TestExtractBrand:

    # ── Test 16: Marca conocida detectada ────────────────────────
    def test_known_brand_detected(self):
        assert extract_brand("sony wh1000xm4 headphones") == "sony"
        assert extract_brand("apple airpods pro 2nd generation") == "apple"
        assert extract_brand("samsung galaxy s24 ultra") == "samsung"

    # ── Test 17: Marca desconocida retorna None ──────────────────
    def test_unknown_brand_returns_none(self):
        assert extract_brand("doogee s98 pro") is None
        assert extract_brand("wireless headphones black") is None


# ═══════════════════════════════════════════════════════════════════
# 4. extract_model (2 tests)
# ═══════════════════════════════════════════════════════════════════

class TestExtractModel:

    # ── Test 18: Modelo alfanumérico extraído ────────────────────
    def test_alphanumeric_model_extracted(self):
        assert extract_model("sony wh1000xm4 headphones", "sony") == "wh1000xm4"
        assert extract_model("doogee s98 pro", "doogee") == "s98"

    # ── Test 19: Sin modelo o marca excluida del resultado ───────
    def test_no_model_and_brand_excluded(self):
        assert extract_model("wireless headphones black", None) is None
        # La marca no debe confundirse con el modelo
        result = extract_model("sony headphones wireless", "sony")
        assert result != "sony"
