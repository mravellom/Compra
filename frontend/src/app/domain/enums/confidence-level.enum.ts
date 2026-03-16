export enum ConfidenceLevel {
  High = 'high',
  Medium = 'medium',
  Low = 'low',
}

export enum TrendType {
  Stable = 'stable',
  Breakout = 'breakout',
  Decline = 'decline',
}

export enum TrendStrength {
  Weak = 'weak',
  Moderate = 'moderate',
  Strong = 'strong',
}

export enum OrderType {
  Buy = 'buy',
  Sell = 'sell',
  ListItem = 'list_item',
}

export enum OrderStatus {
  Draft = 'draft',
  Pending = 'pending',
  Approved = 'approved',
  Rejected = 'rejected',
  Executing = 'executing',
  Executed = 'executed',
  Cancelled = 'cancelled',
  Failed = 'failed',
}

export enum ApprovalState {
  Pending = 'pending',
  Approved = 'approved',
  Rejected = 'rejected',
}

export enum ExecutionMode {
  Manual = 'manual',
  Assisted = 'assisted',
  Auto = 'auto',
}

export enum SignalStrength {
  Strong = 'strong',
  Moderate = 'moderate',
  Weak = 'weak',
}

export enum PipelineStatus {
  Running = 'running',
  Completed = 'completed',
  Failed = 'failed',
}
