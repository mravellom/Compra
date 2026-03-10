"""
Category URL mappings per marketplace.

Each marketplace maps a category slug (from .env CATEGORIES) to its
browseable category URL pattern. {page} is replaced with the page number.
"""

# MercadoLibre category slugs → real category paths
# ML uses "_OrderId_PRICE*DESC" to get full listing grids sorted by relevance
ML_CATEGORIES_AR: dict[str, str] = {
    "electronica": "https://listado.mercadolibre.com.ar/electronica-audio-video/audifonos/_Tienda_oficial",
    "celulares": "https://listado.mercadolibre.com.ar/celulares-telefonos/celulares-smartphones/nuevo/_Tienda_oficial",
    "accesorios-celulares": "https://listado.mercadolibre.com.ar/celulares-telefonos/accesorios-celulares/_Tienda_oficial",
    "computacion": "https://listado.mercadolibre.com.ar/computacion/laptops-accesorios/notebooks/_Tienda_oficial",
    "videojuegos": "https://listado.mercadolibre.com.ar/consolas-videojuegos/consolas/_Tienda_oficial",
    "herramientas": "https://listado.mercadolibre.com.ar/herramientas/herramientas-electricas/_Tienda_oficial",
    "camaras": "https://listado.mercadolibre.com.ar/camaras-accesorios/camaras-digitales/_Tienda_oficial",
    "electrodomesticos": "https://listado.mercadolibre.com.ar/electrodomesticos/pequenos/_Tienda_oficial",
    "relojes-joyas": "https://listado.mercadolibre.com.ar/relojes-joyas/relojes-pulsera/_Tienda_oficial",
}

ML_CATEGORIES_MX: dict[str, str] = {
    "electronica": "https://listado.mercadolibre.com.mx/electronica-audio-video/audifonos/_Tienda_oficial",
    "celulares": "https://listado.mercadolibre.com.mx/celulares-telefonos/celulares-smartphones/nuevo/_Tienda_oficial",
    "accesorios-celulares": "https://listado.mercadolibre.com.mx/celulares-telefonos/accesorios-celulares/_Tienda_oficial",
    "computacion": "https://listado.mercadolibre.com.mx/computacion/laptops-accesorios/notebooks/_Tienda_oficial",
    "videojuegos": "https://listado.mercadolibre.com.mx/consolas-videojuegos/consolas/_Tienda_oficial",
    "herramientas": "https://listado.mercadolibre.com.mx/herramientas/herramientas-electricas/_Tienda_oficial",
    "camaras": "https://listado.mercadolibre.com.mx/camaras-accesorios/camaras-digitales/_Tienda_oficial",
    "electrodomesticos": "https://listado.mercadolibre.com.mx/electrodomesticos/pequenos/_Tienda_oficial",
    "relojes-joyas": "https://listado.mercadolibre.com.mx/relojes-joyas/relojes-pulsera/_Tienda_oficial",
}

# Amazon MX browse nodes (category pages)
AMAZON_CATEGORIES: dict[str, str] = {
    "electronica": "https://www.amazon.com.mx/s?rh=n%3A9482558011&fs=true",
    "celulares": "https://www.amazon.com.mx/s?rh=n%3A9482620011&fs=true",
    "accesorios-celulares": "https://www.amazon.com.mx/s?rh=n%3A9482632011&fs=true",
    "computacion": "https://www.amazon.com.mx/s?rh=n%3A9482530011&fs=true",
    "videojuegos": "https://www.amazon.com.mx/s?rh=n%3A9482596011&fs=true",
    "herramientas": "https://www.amazon.com.mx/s?rh=n%3A13847951011&fs=true",
    "camaras": "https://www.amazon.com.mx/s?rh=n%3A9482508011&fs=true",
    "electrodomesticos": "https://www.amazon.com.mx/s?rh=n%3A9482494011&fs=true",
    "relojes-joyas": "https://www.amazon.com.mx/s?rh=n%3A9482644011&fs=true",
}

# Mapping of marketplace_id → category dict
CATEGORY_MAPS: dict[str, dict[str, str]] = {
    "mercadolibre_ar": ML_CATEGORIES_AR,
    "mercadolibre_mx": ML_CATEGORIES_MX,
    "amazon": AMAZON_CATEGORIES,
}


def get_category_url(marketplace_id: str, category_slug: str) -> str | None:
    """Returns the base URL for a marketplace+category, or None if not mapped."""
    cat_map = CATEGORY_MAPS.get(marketplace_id, {})
    return cat_map.get(category_slug)
