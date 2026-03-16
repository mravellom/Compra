import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import {
  Prediction,
  PredictionRequest,
  PredictionList,
  BatchPredictionRequest,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class PredictionApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  create(request: PredictionRequest): Observable<Prediction> {
    return this.http.post<Prediction>(`${this.baseUrl}/predictions/`, request);
  }

  getByProduct(productId: number, horizonDays?: number): Observable<PredictionList> {
    let params = new HttpParams();
    if (horizonDays) params = params.set('horizon_days', horizonDays.toString());
    return this.http.get<PredictionList>(`${this.baseUrl}/predictions/${productId}`, { params });
  }

  createBatch(request: BatchPredictionRequest): Observable<Prediction[]> {
    return this.http.post<Prediction[]>(`${this.baseUrl}/predictions/batch`, request);
  }
}
