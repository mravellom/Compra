"""
Fixtures compartidos para toda la suite de tests.
Incluye 3 HTML mocks que simulan páginas de resultados de eBay:
  1. Estándar: estructura normal con 2 items válidos + 1 "Shop on eBay" a filtrar.
  2. Variante: títulos anidados en <span> extra y precios con separador de miles.
  3. Internacional: precios en rango, EUR y GBP.
"""
import sys
from pathlib import Path

import pytest

# Asegurar que el root del proyecto esté en el path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ─── Fixture 1: HTML estándar ───────────────────────────────────────

@pytest.fixture
def ebay_html_standard():
    """Estructura clásica de eBay: .s-item con título, precio, imagen y link."""
    return """
    <div class="srp-results">
        <div class="s-item">
            <div class="s-item__image-wrapper">
                <img src="https://i.ebayimg.com/images/g/fake1.jpg" />
            </div>
            <a class="s-item__link" href="https://www.ebay.com/itm/123456">
                <div class="s-item__title">Sony WH-1000XM4 Wireless Headphones</div>
            </a>
            <span class="s-item__price">$229.99</span>
        </div>
        <div class="s-item">
            <div class="s-item__image-wrapper">
                <img src="https://i.ebayimg.com/images/g/fake2.jpg" />
            </div>
            <a class="s-item__link" href="https://www.ebay.com/itm/789012">
                <div class="s-item__title">Apple AirPods Pro 2nd Generation</div>
            </a>
            <span class="s-item__price">$189.50</span>
        </div>
        <div class="s-item">
            <a class="s-item__link" href="https://www.ebay.com/itm/000000">
                <div class="s-item__title">Shop on eBay</div>
            </a>
            <span class="s-item__price">$0.00</span>
        </div>
    </div>
    """


# ─── Fixture 2: DOM variante (títulos en <span>) ───────────────────

@pytest.fixture
def ebay_html_variant():
    """
    Simula un cambio de DOM donde eBay envuelve el título en <span> anidados
    y los precios usan separadores de miles ($1,149.99).
    """
    return """
    <div class="srp-results">
        <div class="s-item">
            <div class="s-item__image-wrapper">
                <img data-src="https://i.ebayimg.com/lazy.jpg"
                     src="https://i.ebayimg.com/images/g/variant1.jpg" />
            </div>
            <a class="s-item__link" href="https://www.ebay.com/itm/555555">
                <div class="s-item__title">
                    <span class="BOLD">
                        <span class="s-item__title--text">Nintendo Switch OLED Model</span>
                    </span>
                </div>
            </a>
            <span class="s-item__price">$299.00</span>
        </div>
        <div class="s-item">
            <div class="s-item__image-wrapper">
                <img src="https://i.ebayimg.com/images/g/variant2.jpg" />
            </div>
            <a class="s-item__link" href="https://www.ebay.com/itm/666666">
                <div class="s-item__title">
                    <span>Samsung Galaxy S24 Ultra 256GB</span>
                </div>
            </a>
            <span class="s-item__price">$1,149.99</span>
        </div>
    </div>
    """


# ─── Fixture 3: Precios internacionales (rango, EUR, GBP) ──────────

@pytest.fixture
def ebay_html_price_range():
    """
    Casos de precio difíciles:
      - Rango: "$199.00 to $249.00" (debe tomar el primero)
      - EUR con texto: "EUR 89.99"
      - GBP con símbolo: "£59.99"
    """
    return """
    <div class="srp-results">
        <div class="s-item">
            <a class="s-item__link" href="https://www.ebay.com/itm/111111">
                <div class="s-item__title">Bose QuietComfort 45</div>
            </a>
            <span class="s-item__price">$199.00 to $249.00</span>
        </div>
        <div class="s-item">
            <a class="s-item__link" href="https://www.ebay.com/itm/222222">
                <div class="s-item__title">JBL Flip 6 Portable Speaker</div>
            </a>
            <span class="s-item__price">EUR 89.99</span>
        </div>
        <div class="s-item">
            <a class="s-item__link" href="https://www.ebay.com/itm/333333">
                <div class="s-item__title">Razer DeathAdder V3</div>
            </a>
            <span class="s-item__price">\u00a359.99</span>
        </div>
    </div>
    """
