export interface CategoryArbitrage {
  category_name: string;
  total_products: number;
  opportunity_count: number;
  avg_profit: number;
  avg_roi: number;
  avg_sales_velocity: number;
  avg_competition: number;
  avg_demand_trend: number;
  best_roi: number;
  best_profit: number;
  category_score: number;
  updated_at: string;
}

export interface CategoryRefreshResult {
  categories_analyzed: number;
  top_category: string | null;
  top_score: number;
}
