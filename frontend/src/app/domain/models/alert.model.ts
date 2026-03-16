export interface AlertConfig {
  id: number;
  user_id: string;
  min_roi: number;
  min_profit: number;
  max_buy_price: number | null;
  categories: string[] | null;
  marketplaces: string[] | null;
  telegram_chat_id: string | null;
  email: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertConfigCreate {
  user_id: string;
  min_roi?: number;
  min_profit?: number;
  max_buy_price?: number | null;
  categories?: string[] | null;
  marketplaces?: string[] | null;
  telegram_chat_id?: string | null;
  email?: string | null;
  enabled?: boolean;
}
