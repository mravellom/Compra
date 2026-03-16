import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { HealthStatus, PipelineHealth } from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class HealthApiClient {
  private readonly http = inject(HttpClient);

  getHealth(): Observable<HealthStatus> {
    return this.http.get<HealthStatus>('/health');
  }

  getPipelineHealth(): Observable<PipelineHealth> {
    return this.http.get<PipelineHealth>('/health/pipeline');
  }
}
