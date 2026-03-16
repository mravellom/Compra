export interface HealthStatus {
  status: string;
  timestamp: number;
}

export interface PipelineHealth {
  status: string;
  timestamp: number;
  database: {
    status: string;
    minutes_since_last_listing: number | null;
  };
  redis: {
    status: string;
    stream_length: number;
    consumer_lag: number;
    pending_messages: number;
  };
  opportunities: {
    total: number;
    high_confidence: number;
    avg_score: number;
  };
  alerts: string[];
}
