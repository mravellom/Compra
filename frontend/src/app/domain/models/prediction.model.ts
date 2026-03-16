export interface Prediction {
  id: number | null;
  product_id: number;
  model_type: string;
  horizon_days: number;
  predicted_price: number;
  confidence_lower: number | null;
  confidence_upper: number | null;
  mape: number | null;
  confidence: number;
  features_used: Record<string, unknown>;
  status: string;
  created_at: string | null;
}

export interface PredictionRequest {
  product_id: number;
  horizon_days?: number;
  model_type?: string | null;
}

export interface BatchPredictionRequest {
  product_ids?: number[];
  horizon_days?: number;
  model_type?: string | null;
  limit?: number;
}

export interface PredictionList {
  predictions: Prediction[];
  total: number;
  product_id: number | null;
}
