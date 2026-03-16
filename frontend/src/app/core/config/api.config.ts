import { InjectionToken } from '@angular/core';

export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => '/api/v1',
});

export const POLL_INTERVAL_MS = 30_000;
export const DEFAULT_PAGE_SIZE = 50;
export const MAX_PAGE_SIZE = 200;
