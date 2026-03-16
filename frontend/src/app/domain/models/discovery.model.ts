export interface TrendingProduct {
  product_id: number;
  product_name: string;
  brand: string | null;
  category: string | null;
  avg_price: number;
  listing_count: number;
  marketplace_count: number;
  seller_count: number;
  trend_score: number;
  trend_label: string;
  velocity_7d: number;
  velocity_30d: number;
  first_seen_at: string | null;
}

export interface NewProduct {
  product_id: number;
  product_name: string;
  brand: string | null;
  category: string | null;
  avg_price: number;
  listing_count: number;
  marketplace_count: number;
  trend_score: number;
  first_seen_at: string | null;
}

export interface DiscoveryStats {
  total_products: number;
  trending_count: number;
  new_last_7d: number;
  avg_listings_per_product: number;
  avg_marketplaces_per_product: number;
  top_categories: TopCategory[];
}

export interface TopCategory {
  category: string;
  product_count: number;
  avg_trend_score: number;
}

export interface ProductHistory {
  date: string;
  listing_count: number;
  marketplace_count: number;
  seller_count: number;
  avg_price: number;
  min_price: number;
  max_price: number;
  total_reviews: number;
  total_sales: number;
}
