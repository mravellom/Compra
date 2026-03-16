export interface ExecutionOrder {
  id: number | null;
  opportunity_id: number | null;
  product_id: number;
  order_type: string;
  marketplace: string;
  price: number;
  quantity: number;
  total_cost: number;
  estimated_profit: number;
  status: string;
  approval_state: string;
  execution_mode: string;
  risk_assessment: Record<string, unknown>;
  error_message: string | null;
  approved_by: string | null;
  approved_at: string | null;
  executed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CreateOrderRequest {
  product_id: number;
  order_type: string;
  marketplace: string;
  price: number;
  quantity?: number;
  opportunity_id?: number | null;
  estimated_profit?: number;
  execution_mode?: string;
}

export interface ApprovalRequest {
  approved: boolean;
  reason?: string | null;
  approved_by?: string;
}

export interface ExecutionResult {
  order_id: number;
  success: boolean;
  executed_price: number | null;
  fees: number;
  error_message: string | null;
}

export interface Portfolio {
  total_exposure: number;
  open_orders: number;
  executed_today: number;
  total_invested: number;
  total_profit: number;
  orders_by_status: Record<string, number>;
}

export interface RiskAssessment {
  passed: boolean;
  total_exposure: number;
  daily_trade_count: number;
  guards_passed: string[];
  guards_failed: string[];
  reasons: string[];
}
