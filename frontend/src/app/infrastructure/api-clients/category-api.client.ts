import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import { CategoryArbitrage, CategoryRefreshResult } from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class CategoryApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  getArbitrage(minRoi?: number, minScore?: number, limit = 50): Observable<CategoryArbitrage[]> {
    let params = new HttpParams().set('limit', limit.toString());
    if (minRoi != null) params = params.set('min_roi', minRoi.toString());
    if (minScore != null) params = params.set('min_score', minScore.toString());
    return this.http.get<CategoryArbitrage[]>(`${this.baseUrl}/categories/arbitrage`, { params });
  }

  refresh(): Observable<CategoryRefreshResult> {
    return this.http.post<CategoryRefreshResult>(`${this.baseUrl}/categories/arbitrage/refresh`, {});
  }
}
