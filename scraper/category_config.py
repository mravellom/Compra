"""
Category URL mappings per marketplace.

v2: Expanded from 9 categories to 25+ per marketplace.
Each marketplace maps a category slug to its browseable category URL.
Covers: electronics, computing, phones, gaming, tools, auto, cameras,
appliances, watches, smart home, audio, networking, home office, sports tech.
"""

# ── MercadoLibre AR ──────────────────────────────────────────
ML_CATEGORIES_AR: dict[str, str] = {
    # Audio
    "audifonos": "https://listado.mercadolibre.com.ar/electronica-audio-video/audifonos/",
    "parlantes": "https://listado.mercadolibre.com.ar/electronica-audio-video/equipos-parlantes/",
    "audio-profesional": "https://listado.mercadolibre.com.ar/electronica-audio-video/componentes-audio/",
    # Phones & Accessories
    "celulares": "https://listado.mercadolibre.com.ar/celulares-telefonos/celulares-smartphones/nuevo/",
    "accesorios-celulares": "https://listado.mercadolibre.com.ar/celulares-telefonos/accesorios-celulares/",
    "smartwatch": "https://listado.mercadolibre.com.ar/celulares-telefonos/smartwatches-accesorios/",
    # Computing
    "notebooks": "https://listado.mercadolibre.com.ar/computacion/laptops-accesorios/notebooks/",
    "tablets": "https://listado.mercadolibre.com.ar/computacion/tablets/",
    "monitores": "https://listado.mercadolibre.com.ar/computacion/monitores-accesorios/monitores/",
    "almacenamiento": "https://listado.mercadolibre.com.ar/computacion/almacenamiento/",
    "perifericos": "https://listado.mercadolibre.com.ar/computacion/perifericos-accesorios-pc/",
    "networking": "https://listado.mercadolibre.com.ar/computacion/conectividad-redes/",
    "impresoras": "https://listado.mercadolibre.com.ar/computacion/impresoras/",
    # Gaming
    "consolas": "https://listado.mercadolibre.com.ar/consolas-videojuegos/consolas/",
    "accesorios-gaming": "https://listado.mercadolibre.com.ar/consolas-videojuegos/accesorios-consolas/",
    "juegos-fisicos": "https://listado.mercadolibre.com.ar/consolas-videojuegos/videojuegos/",
    # Cameras
    "camaras-digitales": "https://listado.mercadolibre.com.ar/camaras-accesorios/camaras-digitales/",
    "accesorios-camaras": "https://listado.mercadolibre.com.ar/camaras-accesorios/accesorios-camaras/",
    "drones": "https://listado.mercadolibre.com.ar/camaras-accesorios/drones-accesorios/",
    # Tools
    "herramientas-electricas": "https://listado.mercadolibre.com.ar/herramientas/herramientas-electricas/",
    "instrumentos-medicion": "https://listado.mercadolibre.com.ar/herramientas/instrumentos-medicion/",
    # Home & Appliances
    "electrodomesticos": "https://listado.mercadolibre.com.ar/electrodomesticos/pequenos/",
    "climatizacion": "https://listado.mercadolibre.com.ar/electrodomesticos/climatizacion/",
    # Auto
    "accesorios-vehiculos": "https://listado.mercadolibre.com.ar/accesorios-vehiculos/",
    "electronica-vehicular": "https://listado.mercadolibre.com.ar/electronica-audio-video/audio-vehiculos/",
    # Watches & Smart Home
    "relojes": "https://listado.mercadolibre.com.ar/relojes-joyas/relojes-pulsera/",
    "hogar-inteligente": "https://listado.mercadolibre.com.ar/electronica-audio-video/controles-remoto-accesorios/",
    # New expanded categories
    "componentes-pc": "https://listado.mercadolibre.com.ar/computacion/componentes-pc/",
    "proyectores": "https://listado.mercadolibre.com.ar/electronica-audio-video/proyectores-accesorios/",
    "ups-reguladores": "https://listado.mercadolibre.com.ar/computacion/estabilizadores-ups/",
    "seguridad-electronica": "https://listado.mercadolibre.com.ar/electronica-audio-video/seguridad-electronica/",
    "audio-vehicular": "https://listado.mercadolibre.com.ar/electronica-audio-video/audio-vehiculos/",
    "fitness-tracker": "https://listado.mercadolibre.com.ar/celulares-telefonos/smartwatches-accesorios/",
    "memorias-usb": "https://listado.mercadolibre.com.ar/computacion/almacenamiento/pendrives-memorias-usb/",
    "cables-adaptadores": "https://listado.mercadolibre.com.ar/computacion/cables-conectores/",
    "baterias-cargadores": "https://listado.mercadolibre.com.ar/celulares-telefonos/cargadores-cables/",
    "robots-aspiradora": "https://listado.mercadolibre.com.ar/electrodomesticos/aspiradoras-limpieza/robots-aspiradoras/",
}

# ── MercadoLibre MX ──────────────────────────────────────────
ML_CATEGORIES_MX: dict[str, str] = {
    "audifonos": "https://listado.mercadolibre.com.mx/electronica-audio-video/audifonos/",
    "parlantes": "https://listado.mercadolibre.com.mx/electronica-audio-video/equipos-parlantes/",
    "audio-profesional": "https://listado.mercadolibre.com.mx/electronica-audio-video/componentes-audio/",
    "celulares": "https://listado.mercadolibre.com.mx/celulares-telefonos/celulares-smartphones/nuevo/",
    "accesorios-celulares": "https://listado.mercadolibre.com.mx/celulares-telefonos/accesorios-celulares/",
    "smartwatch": "https://listado.mercadolibre.com.mx/celulares-telefonos/smartwatches-accesorios/",
    "notebooks": "https://listado.mercadolibre.com.mx/computacion/laptops-accesorios/notebooks/",
    "tablets": "https://listado.mercadolibre.com.mx/computacion/tablets/",
    "monitores": "https://listado.mercadolibre.com.mx/computacion/monitores-accesorios/monitores/",
    "almacenamiento": "https://listado.mercadolibre.com.mx/computacion/almacenamiento/",
    "perifericos": "https://listado.mercadolibre.com.mx/computacion/perifericos-accesorios-pc/",
    "networking": "https://listado.mercadolibre.com.mx/computacion/conectividad-redes/",
    "impresoras": "https://listado.mercadolibre.com.mx/computacion/impresoras/",
    "consolas": "https://listado.mercadolibre.com.mx/consolas-videojuegos/consolas/",
    "accesorios-gaming": "https://listado.mercadolibre.com.mx/consolas-videojuegos/accesorios-consolas/",
    "juegos-fisicos": "https://listado.mercadolibre.com.mx/consolas-videojuegos/videojuegos/",
    "camaras-digitales": "https://listado.mercadolibre.com.mx/camaras-accesorios/camaras-digitales/",
    "accesorios-camaras": "https://listado.mercadolibre.com.mx/camaras-accesorios/accesorios-camaras/",
    "drones": "https://listado.mercadolibre.com.mx/camaras-accesorios/drones-accesorios/",
    "herramientas-electricas": "https://listado.mercadolibre.com.mx/herramientas/herramientas-electricas/",
    "instrumentos-medicion": "https://listado.mercadolibre.com.mx/herramientas/instrumentos-medicion/",
    "electrodomesticos": "https://listado.mercadolibre.com.mx/electrodomesticos/pequenos/",
    "climatizacion": "https://listado.mercadolibre.com.mx/electrodomesticos/climatizacion/",
    "accesorios-vehiculos": "https://listado.mercadolibre.com.mx/accesorios-vehiculos/",
    "electronica-vehicular": "https://listado.mercadolibre.com.mx/electronica-audio-video/audio-vehiculos/",
    "relojes": "https://listado.mercadolibre.com.mx/relojes-joyas/relojes-pulsera/",
    "hogar-inteligente": "https://listado.mercadolibre.com.mx/electronica-audio-video/controles-remoto-accesorios/",
    # New expanded categories
    "componentes-pc": "https://listado.mercadolibre.com.mx/computacion/componentes-pc/",
    "proyectores": "https://listado.mercadolibre.com.mx/electronica-audio-video/proyectores-accesorios/",
    "ups-reguladores": "https://listado.mercadolibre.com.mx/computacion/estabilizadores-ups/",
    "seguridad-electronica": "https://listado.mercadolibre.com.mx/electronica-audio-video/seguridad-electronica/",
    "audio-vehicular": "https://listado.mercadolibre.com.mx/electronica-audio-video/audio-vehiculos/",
    "memorias-usb": "https://listado.mercadolibre.com.mx/computacion/almacenamiento/pendrives-memorias-usb/",
    "cables-adaptadores": "https://listado.mercadolibre.com.mx/computacion/cables-conectores/",
    "baterias-cargadores": "https://listado.mercadolibre.com.mx/celulares-telefonos/cargadores-cables/",
    "robots-aspiradora": "https://listado.mercadolibre.com.mx/electrodomesticos/aspiradoras-limpieza/robots-aspiradoras/",
}

# ── MercadoLibre CL ──────────────────────────────────────────
ML_CATEGORIES_CL: dict[str, str] = {
    "audifonos": "https://listado.mercadolibre.cl/electronica-audio-video/audifonos/",
    "parlantes": "https://listado.mercadolibre.cl/electronica-audio-video/equipos-parlantes/",
    "celulares": "https://listado.mercadolibre.cl/celulares-telefonos/celulares-smartphones/nuevo/",
    "accesorios-celulares": "https://listado.mercadolibre.cl/celulares-telefonos/accesorios-celulares/",
    "smartwatch": "https://listado.mercadolibre.cl/celulares-telefonos/smartwatches-accesorios/",
    "notebooks": "https://listado.mercadolibre.cl/computacion/laptops-accesorios/notebooks/",
    "tablets": "https://listado.mercadolibre.cl/computacion/tablets/",
    "perifericos": "https://listado.mercadolibre.cl/computacion/perifericos-accesorios-pc/",
    "consolas": "https://listado.mercadolibre.cl/consolas-videojuegos/consolas/",
    "accesorios-gaming": "https://listado.mercadolibre.cl/consolas-videojuegos/accesorios-consolas/",
    "camaras-digitales": "https://listado.mercadolibre.cl/camaras-accesorios/camaras-digitales/",
    "herramientas-electricas": "https://listado.mercadolibre.cl/herramientas/herramientas-electricas/",
    "electrodomesticos": "https://listado.mercadolibre.cl/electrodomesticos/pequenos/",
    "relojes": "https://listado.mercadolibre.cl/relojes-joyas/relojes-pulsera/",
    # New expanded categories
    "componentes-pc": "https://listado.mercadolibre.cl/computacion/componentes-pc/",
    "cables-adaptadores": "https://listado.mercadolibre.cl/computacion/cables-conectores/",
    "baterias-cargadores": "https://listado.mercadolibre.cl/celulares-telefonos/cargadores-cables/",
}

# ── MercadoLibre CO ──────────────────────────────────────────
ML_CATEGORIES_CO: dict[str, str] = {
    "audifonos": "https://listado.mercadolibre.com.co/electronica-audio-video/audifonos/",
    "parlantes": "https://listado.mercadolibre.com.co/electronica-audio-video/equipos-parlantes/",
    "celulares": "https://listado.mercadolibre.com.co/celulares-telefonos/celulares-smartphones/nuevo/",
    "accesorios-celulares": "https://listado.mercadolibre.com.co/celulares-telefonos/accesorios-celulares/",
    "smartwatch": "https://listado.mercadolibre.com.co/celulares-telefonos/smartwatches-accesorios/",
    "notebooks": "https://listado.mercadolibre.com.co/computacion/laptops-accesorios/notebooks/",
    "tablets": "https://listado.mercadolibre.com.co/computacion/tablets/",
    "perifericos": "https://listado.mercadolibre.com.co/computacion/perifericos-accesorios-pc/",
    "consolas": "https://listado.mercadolibre.com.co/consolas-videojuegos/consolas/",
    "accesorios-gaming": "https://listado.mercadolibre.com.co/consolas-videojuegos/accesorios-consolas/",
    "camaras-digitales": "https://listado.mercadolibre.com.co/camaras-accesorios/camaras-digitales/",
    "herramientas-electricas": "https://listado.mercadolibre.com.co/herramientas/herramientas-electricas/",
    "electrodomesticos": "https://listado.mercadolibre.com.co/electrodomesticos/pequenos/",
    "relojes": "https://listado.mercadolibre.com.co/relojes-joyas/relojes-pulsera/",
    # New expanded categories
    "componentes-pc": "https://listado.mercadolibre.com.co/computacion/componentes-pc/",
    "cables-adaptadores": "https://listado.mercadolibre.com.co/computacion/cables-conectores/",
    "baterias-cargadores": "https://listado.mercadolibre.com.co/celulares-telefonos/cargadores-cables/",
}

# ── Amazon MX browse nodes ───────────────────────────────────
AMAZON_MX_CATEGORIES: dict[str, str] = {
    "audifonos": "https://www.amazon.com.mx/s?rh=n%3A9482558011&fs=true",
    "celulares": "https://www.amazon.com.mx/s?rh=n%3A9482620011&fs=true",
    "accesorios-celulares": "https://www.amazon.com.mx/s?rh=n%3A9482632011&fs=true",
    "notebooks": "https://www.amazon.com.mx/s?rh=n%3A9482530011&fs=true",
    "tablets": "https://www.amazon.com.mx/s?rh=n%3A9482536011&fs=true",
    "monitores": "https://www.amazon.com.mx/s?rh=n%3A9482542011&fs=true",
    "almacenamiento": "https://www.amazon.com.mx/s?rh=n%3A9482548011&fs=true",
    "perifericos": "https://www.amazon.com.mx/s?rh=n%3A9482524011&fs=true",
    "networking": "https://www.amazon.com.mx/s?rh=n%3A9482518011&fs=true",
    "consolas": "https://www.amazon.com.mx/s?rh=n%3A9482596011&fs=true",
    "accesorios-gaming": "https://www.amazon.com.mx/s?rh=n%3A9482602011&fs=true",
    "camaras-digitales": "https://www.amazon.com.mx/s?rh=n%3A9482508011&fs=true",
    "accesorios-camaras": "https://www.amazon.com.mx/s?rh=n%3A9482514011&fs=true",
    "herramientas-electricas": "https://www.amazon.com.mx/s?rh=n%3A13847951011&fs=true",
    "electrodomesticos": "https://www.amazon.com.mx/s?rh=n%3A9482494011&fs=true",
    "relojes": "https://www.amazon.com.mx/s?rh=n%3A9482644011&fs=true",
    "smartwatch": "https://www.amazon.com.mx/s?rh=n%3A16333826011&fs=true",
    # New expanded categories
    "componentes-pc": "https://www.amazon.com.mx/s?rh=n%3A9482500011&fs=true",
    "cables-adaptadores": "https://www.amazon.com.mx/s?rh=n%3A9482524011&fs=true&s=popularity-rank",
    "baterias-cargadores": "https://www.amazon.com.mx/s?rh=n%3A9482632011&fs=true&s=popularity-rank",
    "robots-aspiradora": "https://www.amazon.com.mx/s?rh=n%3A16333858011&fs=true",
    "parlantes": "https://www.amazon.com.mx/s?rh=n%3A9482564011&fs=true",
    "drones": "https://www.amazon.com.mx/s?rh=n%3A16333788011&fs=true",
    "hogar-inteligente": "https://www.amazon.com.mx/s?rh=n%3A16333812011&fs=true",
}

# ── Amazon US browse nodes ───────────────────────────────────
AMAZON_US_CATEGORIES: dict[str, str] = {
    "audifonos": "https://www.amazon.com/s?rh=n%3A172541&fs=true",
    "parlantes": "https://www.amazon.com/s?rh=n%3A3236451011&fs=true",
    "celulares": "https://www.amazon.com/s?rh=n%3A7072561011&fs=true",
    "accesorios-celulares": "https://www.amazon.com/s?rh=n%3A2407749011&fs=true",
    "smartwatch": "https://www.amazon.com/s?rh=n%3A10048700011&fs=true",
    "notebooks": "https://www.amazon.com/s?rh=n%3A565108&fs=true",
    "tablets": "https://www.amazon.com/s?rh=n%3A1232597011&fs=true",
    "monitores": "https://www.amazon.com/s?rh=n%3A1292115011&fs=true",
    "almacenamiento": "https://www.amazon.com/s?rh=n%3A1292110011&fs=true",
    "perifericos": "https://www.amazon.com/s?rh=n%3A172456&fs=true",
    "networking": "https://www.amazon.com/s?rh=n%3A172504&fs=true",
    "consolas": "https://www.amazon.com/s?rh=n%3A720020&fs=true",
    "accesorios-gaming": "https://www.amazon.com/s?rh=n%3A49icons6104&fs=true",
    "camaras-digitales": "https://www.amazon.com/s?rh=n%3A281052&fs=true",
    "drones": "https://www.amazon.com/s?rh=n%3A414986011&fs=true",
    "herramientas-electricas": "https://www.amazon.com/s?rh=n%3A328182011&fs=true",
    "electrodomesticos": "https://www.amazon.com/s?rh=n%3A1055398&fs=true",
    "relojes": "https://www.amazon.com/s?rh=n%3A6358539011&fs=true",
    "hogar-inteligente": "https://www.amazon.com/s?rh=n%3A6563140011&fs=true",
    # New expanded categories
    "componentes-pc": "https://www.amazon.com/s?rh=n%3A193870011&fs=true",
    "cables-adaptadores": "https://www.amazon.com/s?rh=n%3A464394&fs=true",
    "baterias-cargadores": "https://www.amazon.com/s?rh=n%3A10112773011&fs=true",
    "robots-aspiradora": "https://www.amazon.com/s?rh=n%3A3743561&fs=true",
    "seguridad-electronica": "https://www.amazon.com/s?rh=n%3A7icons161092011&fs=true",
    "proyectores": "https://www.amazon.com/s?rh=n%3A300334&fs=true",
}

# ── AliExpress categories ────────────────────────────────────
# AliExpress uses category IDs in URLs
ALIEXPRESS_CATEGORIES: dict[str, str] = {
    "audifonos": "https://www.aliexpress.com/category/200003545/earphones-headphones.html",
    "parlantes": "https://www.aliexpress.com/category/200003546/speakers.html",
    "celulares": "https://www.aliexpress.com/category/5090301/cellphones.html",
    "accesorios-celulares": "https://www.aliexpress.com/category/380208/phone-cases.html",
    "smartwatch": "https://www.aliexpress.com/category/200362144/smart-watches.html",
    "tablets": "https://www.aliexpress.com/category/200216607/tablets.html",
    "almacenamiento": "https://www.aliexpress.com/category/70803/storage-devices.html",
    "perifericos": "https://www.aliexpress.com/category/70802/mice-keyboards.html",
    "networking": "https://www.aliexpress.com/category/200003074/networking.html",
    "consolas": "https://www.aliexpress.com/category/200003064/video-game-consoles.html",
    "accesorios-gaming": "https://www.aliexpress.com/category/200003062/gamepads.html",
    "camaras-digitales": "https://www.aliexpress.com/category/200003547/digital-cameras.html",
    "drones": "https://www.aliexpress.com/category/200003918/camera-drones.html",
    "herramientas-electricas": "https://www.aliexpress.com/category/200003206/power-tools.html",
    "electrodomesticos": "https://www.aliexpress.com/category/200003386/small-appliances.html",
    "relojes": "https://www.aliexpress.com/category/200362143/watches.html",
    "hogar-inteligente": "https://www.aliexpress.com/category/200003499/smart-home.html",
    "iluminacion-led": "https://www.aliexpress.com/category/200003294/led-lighting.html",
    # New expanded categories
    "componentes-pc": "https://www.aliexpress.com/category/200003071/computer-components.html",
    "cables-adaptadores": "https://www.aliexpress.com/category/200003073/cables-adapters.html",
    "baterias-cargadores": "https://www.aliexpress.com/category/200003490/chargers.html",
    "robots-aspiradora": "https://www.aliexpress.com/category/200003388/vacuum-cleaners.html",
    "seguridad-electronica": "https://www.aliexpress.com/category/200003500/security-protection.html",
    "proyectores": "https://www.aliexpress.com/category/200003076/projectors.html",
    "fitness-tracker": "https://www.aliexpress.com/category/200362144/smart-watches.html",
    "memorias-usb": "https://www.aliexpress.com/category/200003072/usb-flash-drives.html",
}

# ── Master mapping ───────────────────────────────────────────
CATEGORY_MAPS: dict[str, dict[str, str]] = {
    "mercadolibre_ar": ML_CATEGORIES_AR,
    "mercadolibre_mx": ML_CATEGORIES_MX,
    "mercadolibre_cl": ML_CATEGORIES_CL,
    "mercadolibre_co": ML_CATEGORIES_CO,
    "amazon": AMAZON_MX_CATEGORIES,
    "amazon_us": AMAZON_US_CATEGORIES,
    "aliexpress": ALIEXPRESS_CATEGORIES,
}

# ── Default category list (used when CATEGORIES env var is empty) ──
DEFAULT_CATEGORIES: list[str] = [
    # Audio
    "audifonos",
    "parlantes",
    "audio-profesional",
    # Phones & Wearables
    "celulares",
    "accesorios-celulares",
    "smartwatch",
    # Computing
    "notebooks",
    "tablets",
    "monitores",
    "almacenamiento",
    "perifericos",
    "networking",
    "impresoras",
    # Gaming
    "consolas",
    "accesorios-gaming",
    "juegos-fisicos",
    # Cameras
    "camaras-digitales",
    "accesorios-camaras",
    "drones",
    # Tools & Industrial
    "herramientas-electricas",
    "instrumentos-medicion",
    # Home & Appliances
    "electrodomesticos",
    "climatizacion",
    # Automotive
    "accesorios-vehiculos",
    "electronica-vehicular",
    # Watches & Smart Home
    "relojes",
    "hogar-inteligente",
    "iluminacion-led",
    # ── New categories for expanded coverage ──
    "componentes-pc",
    "proyectores",
    "ups-reguladores",
    "seguridad-electronica",
    "audio-vehicular",
    "fitness-tracker",
    "memorias-usb",
    "cables-adaptadores",
    "baterias-cargadores",
    "robots-aspiradora",
]

# ── Max pages per category (can be overridden by smart expansion / prioritizer) ──
DEFAULT_MAX_PAGES = 15


def get_category_url(marketplace_id: str, category_slug: str) -> str | None:
    """Returns the base URL for a marketplace+category, or None if not mapped."""
    cat_map = CATEGORY_MAPS.get(marketplace_id, {})
    return cat_map.get(category_slug)


def get_all_categories_for(marketplace_id: str) -> list[str]:
    """Returns all category slugs available for a given marketplace."""
    cat_map = CATEGORY_MAPS.get(marketplace_id, {})
    return list(cat_map.keys())


def get_common_categories() -> list[str]:
    """Returns category slugs that exist in at least 2 marketplaces (maximizes arbitrage coverage)."""
    from collections import Counter
    counter: Counter[str] = Counter()
    for cat_map in CATEGORY_MAPS.values():
        for slug in cat_map:
            counter[slug] += 1
    return [slug for slug, count in counter.most_common() if count >= 2]
