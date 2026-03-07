-- Habilitar extensión pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Tabla de productos maestros (catálogo canónico)
CREATE TABLE IF NOT EXISTS master_products (
    id              BIGSERIAL PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    brand           TEXT,
    model           TEXT,
    category        TEXT,
    embedding       vector(384) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Índice HNSW para búsqueda por cosine similarity (rápido en lecturas)
CREATE INDEX IF NOT EXISTS idx_master_products_embedding
    ON master_products
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Índice para búsquedas por brand/model
CREATE INDEX IF NOT EXISTS idx_master_products_brand_model
    ON master_products (brand, model);

-- Tabla de listings vinculados a productos maestros
CREATE TABLE IF NOT EXISTS product_listings (
    id                  BIGSERIAL PRIMARY KEY,
    master_product_id   BIGINT REFERENCES master_products(id),
    title               TEXT NOT NULL,
    normalized_title    TEXT NOT NULL,
    price               NUMERIC(12, 2) NOT NULL,
    currency            TEXT DEFAULT 'USD',
    url                 TEXT NOT NULL,
    marketplace_id      TEXT NOT NULL,
    image_url           TEXT,
    similarity_score    REAL,
    scraped_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_listings_master
    ON product_listings (master_product_id);

CREATE INDEX IF NOT EXISTS idx_product_listings_marketplace
    ON product_listings (marketplace_id);
