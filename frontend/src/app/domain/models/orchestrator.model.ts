export interface OpportunityInput {
  opportunity_id: number;
  product_id: number;
  buy_price: number;
  sell_price: number;
  net_profit: number;
  roi: number;
  buy_marketplace: string;
  sell_marketplace: string;
  opportunity_score?: number;
  confidence_score?: number;
  risk_score?: number;
}

export interface Decision {
  opportunity_id: number;
  product_id: number;
  decision: string;
  signal_strength: string;
  score: number;
  reasons: string[];
  recommended_action: string | null;
  recommended_price: number | null;
  metadata: Record<string, unknown>;
}

export interface BatchOrchestrationRequest {
  opportunities?: OpportunityInput[];
  limit?: number;
}

export interface Pipeline {
  pipeline_id: string;
  opportunity_id: number;
  product_id: number;
  status: string;
  decision: string | null;
  decision_score: number | null;
  signal_strength: string | null;
  phases: Record<string, unknown>;
  reasons: string[];
  total_duration_ms: number | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface DecisionStats {
  decisions_24h: Record<string, unknown>[];
}
