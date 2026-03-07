export interface Opportunity {
  id: number;
  product_name: string;
  buy_price: number;
  sell_price: number;
  fees: number;
  shipping_cost: number;
  net_profit: number;
  roi: number;
  buy_marketplace: string;
  sell_marketplace: string;
  buy_url: string;
  sell_url: string;
  image_url: string | null;
  status: string;
  created_at: string;
}

export interface RelatedListing {
  id: number;
  title: string;
  price: number;
  currency: string;
  marketplace_id: string;
  url: string;
  image_url: string | null;
  similarity_score: number | null;
  scraped_at: string | null;
}

export interface OpportunityDetail extends Opportunity {
  category: string | null;
  brand: string | null;
  model: string | null;
  related_listings: RelatedListing[];
}

export interface DashboardStats {
  total_opportunities: number;
  avg_roi: number;
  avg_profit: number;
  top_marketplace: string | null;
  last_scan: string | null;
}

export interface OpportunityFilters {
  min_roi: number;
  max_buy_price: number;
  marketplaces: string[];
}
