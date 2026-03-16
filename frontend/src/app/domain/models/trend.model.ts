export interface Trend {
  id: number | null;
  product_id: number;
  product_name: string | null;
  trend_score: number;
  velocity_ratio: number;
  price_momentum: number;
  volume_change: number;
  trend_type: string;
  trend_strength: string;
  signals: Record<string, unknown>;
  detected_at: string | null;
}

export interface TrendList {
  trends: Trend[];
  total: number;
}

export interface TrendScanResult {
  products_analyzed: number;
  trends_detected: number;
  breakouts: number;
}
