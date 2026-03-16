export interface Opportunity {
  id: number;
  product_name: string;
  buy_price: number;
  sell_price: number;
  estimated_sell_price: number | null;
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
  route: string;

  // Fees breakdown
  sell_tax: number;
  marketplace_fee: number;
  payment_fee: number;
  import_tax: number;
  domestic_shipping: number;
  international_shipping: number;

  // Scoring
  sales_velocity_score: number;
  competition_score: number;
  price_stability_score: number;
  opportunity_score: number;
  confidence_level: string;
  risk_score: number;
  confidence_score: number;

  // Market depth
  competitor_count: number;
  avg_market_price: number | null;
  lowest_competitor_price: number | null;
  market_depth_score: number;
  estimated_daily_sales: number;
  estimated_monthly_sales: number;
  scalability_level: string;

  // Demand trend
  demand_trend_score: number;
  demand_trend_label: string;

  // Lifecycle
  decay_rate: number;
  lifetime_hours: number;
  urgency_score: number;
  lifecycle_label: string;

  // Capital efficiency
  capital_required: number;
  capital_efficiency_score: number;
  capital_tier: string;
  recommended_quantity: number;
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

export interface OpportunityFilters {
  min_roi: number;
  max_buy_price: number;
  marketplaces: string[];
  category?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export interface DashboardStats {
  total_opportunities: number;
  avg_roi: number;
  avg_profit: number;
  top_marketplace: string | null;
  last_scan: string | null;
}

export interface ScanResult {
  new_opportunities: number;
  notifications_sent: number;
  lifecycles_updated: number;
  categories_analyzed: number;
}
