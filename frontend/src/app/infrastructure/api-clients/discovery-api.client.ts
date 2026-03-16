import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import {
  TrendingProduct,
  NewProduct,
  DiscoveryStats,
  ProductHistory,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class DiscoveryApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  getTrending(limit = 20, category?: string): Observable<TrendingProduct[]> {
    let params = new HttpParams().set('limit', limit.toString());
    if (category) params = params.set('category', category);
    return this.http.get<TrendingProduct[]>(`${this.baseUrl}/discovery/trending`, { params });
  }

  getNew(days = 7, limit = 20): Observable<NewProduct[]> {
    const params = new HttpParams()
      .set('days', days.toString())
      .set('limit', limit.toString());
    return this.http.get<NewProduct[]>(`${this.baseUrl}/discovery/new`, { params });
  }

  getStats(): Observable<DiscoveryStats> {
    return this.http.get<DiscoveryStats>(`${this.baseUrl}/discovery/stats`);
  }

  getProductHistory(productId: number, days = 30): Observable<ProductHistory[]> {
    const params = new HttpParams().set('days', days.toString());
    return this.http.get<ProductHistory[]>(
      `${this.baseUrl}/discovery/product/${productId}/history`,
      { params },
    );
  }
}
