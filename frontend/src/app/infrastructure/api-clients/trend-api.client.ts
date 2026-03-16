import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import { Trend, TrendList, TrendScanResult } from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class TrendApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  getAll(topN = 20): Observable<TrendList> {
    const params = new HttpParams().set('top_n', topN.toString());
    return this.http.get<TrendList>(`${this.baseUrl}/trends/`, { params });
  }

  getBreakouts(limit = 10): Observable<TrendList> {
    const params = new HttpParams().set('limit', limit.toString());
    return this.http.get<TrendList>(`${this.baseUrl}/trends/breakouts`, { params });
  }

  getByProduct(productId: number): Observable<Trend> {
    return this.http.get<Trend>(`${this.baseUrl}/trends/${productId}`);
  }

  scan(limit = 100): Observable<TrendScanResult> {
    const params = new HttpParams().set('limit', limit.toString());
    return this.http.post<TrendScanResult>(`${this.baseUrl}/trends/scan`, null, { params });
  }
}
