import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import { AlertConfig, AlertConfigCreate } from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class AlertsApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  createOrUpdate(config: AlertConfigCreate): Observable<AlertConfig> {
    return this.http.post<AlertConfig>(`${this.baseUrl}/alerts/config`, config);
  }

  getByUserId(userId: string): Observable<AlertConfig> {
    return this.http.get<AlertConfig>(`${this.baseUrl}/alerts/config/${userId}`);
  }

  deleteByUserId(userId: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/alerts/config/${userId}`);
  }
}
