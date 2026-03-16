"""
Sample HTML fixtures for scraper parser tests.

Each fixture simulates a real marketplace search results page
with controlled, deterministic data for assertion.
"""

AMAZON_SEARCH_RESULTS = """
<div class="s-main-slot">
  <div data-component-type="s-search-result" data-asin="B09XS7JWHH">
    <h2><a><span>Samsung Galaxy S24 Ultra 256GB Negro</span></a></h2>
    <div class="a-price">
      <span class="a-offscreen">$18,999.00</span>
    </div>
    <img class="s-image" src="https://images-na.ssl-images-amazon.com/images/I/samsung-s24.jpg" />
    <span class="a-size-base s-underline-text">1,234 calificaciones</span>
    <span class="a-icon-alt">4.5 de 5 estrellas</span>
  </div>
  <div data-component-type="s-search-result" data-asin="B0CHX3QBCH">
    <h2><a><span>Apple AirPods Pro 2da Generación USB-C</span></a></h2>
    <div class="a-price">
      <span class="a-offscreen">$4,299.00</span>
    </div>
    <img class="s-image" src="https://images-na.ssl-images-amazon.com/images/I/airpods-pro.jpg" />
    <span class="a-size-base s-underline-text">567 calificaciones</span>
    <span class="a-icon-alt">4.7 de 5 estrellas</span>
    <span class="a-color-base a-text-bold">Envío GRATIS</span>
  </div>
</div>
"""

AMAZON_NO_RESULTS = """
<div class="s-main-slot">
  <div class="s-no-outline">
    <span>No se encontraron resultados para</span>
  </div>
</div>
"""

MERCADOLIBRE_SEARCH_RESULTS = """
<ol class="ui-search-layout">
  <li class="ui-search-layout__item">
    <div class="poly-card">
      <a href="https://articulo.mercadolibre.com.mx/MLM-123456-iphone-15-128gb">
        <img src="https://http2.mlstatic.com/iphone15.jpg" />
      </a>
      <a class="poly-component__title" href="https://articulo.mercadolibre.com.mx/MLM-123456-iphone-15-128gb">
        iPhone 15 128GB Negro Reacondicionado
      </a>
      <div class="poly-price">
        <span class="andes-money-amount__fraction">15999</span>
      </div>
      <span class="poly-reviews__total">(89)</span>
      <span class="poly-reviews__rating">4.3</span>
      <div class="poly-component__shipping">
        <span class="poly-component__shipped-text">Envío gratis</span>
      </div>
      <span class="poly-component__sold">+500 vendidos</span>
    </div>
  </li>
  <li class="ui-search-layout__item">
    <div class="poly-card">
      <a class="poly-component__title" href="https://articulo.mercadolibre.com.mx/MLM-789012-samsung-galaxy-a15">
        Samsung Galaxy A15 64GB Azul
      </a>
      <div class="poly-price">
        <span class="andes-money-amount__fraction">3499</span>
      </div>
    </div>
  </li>
</ol>
"""

EBAY_SEARCH_WITH_SHIPPING = """
<div class="srp-results">
  <li class="s-item">
    <div class="s-item__image-wrapper">
      <img src="https://i.ebayimg.com/images/g/item1.jpg" />
    </div>
    <a class="s-item__link" href="https://www.ebay.com/itm/100001">
      <div class="s-item__title">Sony WH-1000XM5 Wireless Headphones</div>
    </a>
    <span class="s-item__price">$279.99</span>
    <span class="SECONDARY_INFO">Brand New</span>
    <span class="s-item__shipping">+$12.50 shipping</span>
    <span class="s-item__reviews-count"><span>245 product ratings</span></span>
  </li>
  <li class="s-item">
    <div class="s-item__image-wrapper">
      <img src="https://i.ebayimg.com/images/g/item2.jpg" />
    </div>
    <a class="s-item__link" href="https://www.ebay.com/itm/100002">
      <div class="s-item__title">Sony WH-1000XM5 - Refurbished</div>
    </a>
    <span class="s-item__price">$199.00</span>
    <span class="SECONDARY_INFO">Certified - Refurbished</span>
    <span class="s-item__freeXDays">Free 3 day shipping</span>
    <span class="s-item__seller-info-text">99.2% positive</span>
  </li>
</div>
"""

EBAY_EMPTY_RESULTS = """
<div class="srp-results">
  <div class="s-item">
    <a class="s-item__link" href="https://www.ebay.com/itm/000000">
      <div class="s-item__title">Shop on eBay</div>
    </a>
    <span class="s-item__price">$0.00</span>
  </div>
</div>
"""

MALFORMED_HTML = """
<div class="srp-results">
  <div class="s-item">
    <a class="s-item__link" href="">
      <div class="s-item__title"></div>
    </a>
    <span class="s-item__price"></span>
  </div>
  <div class="s-item">
  </div>
</div>
"""
