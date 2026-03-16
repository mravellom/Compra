import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import {
  Opportunity,
  OpportunityDetail,
  DashboardStats,
  ScanResult,
  OpportunityFilters,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class OpportunityApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  getAll(filters?: Partial<OpportunityFilters>): Observable<Opportunity[]> {
    let params = new HttpParams();
    if (filters?.min_roi) params = params.set('min_roi', filters.min_roi.toString());
    if (filters?.max_buy_price) params = params.set('max_buy_price', filters.max_buy_price.toString());
    if (filters?.category) params = params.set('category', filters.category);
    if (filters?.marketplaces?.length) params = params.set('marketplace', filters.marketplaces[0]);
    if (filters?.status) params = params.set('status', filters.status);
    params = params.set('limit', (filters?.limit ?? 200).toString());
    if (filters?.offset) params = params.set('offset', filters.offset.toString());

    return this.http.get<Opportunity[]>(`${this.baseUrl}/opportunities`, { params });
  }

  getById(id: number): Observable<OpportunityDetail> {
    return this.http.get<OpportunityDetail>(`${this.baseUrl}/opportunities/${id}`);
  }

  getStats(): Observable<DashboardStats> {
    return this.http.get<DashboardStats>(`${this.baseUrl}/opportunities/stats`);
  }

  triggerScan(): Observable<ScanResult> {
    return this.http.post<ScanResult>(`${this.baseUrl}/opportunities/scan`, {});
  }
}
