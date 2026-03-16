import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import {
  OpportunityInput,
  Decision,
  BatchOrchestrationRequest,
  Pipeline,
  DecisionStats,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class OrchestratorApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  process(input: OpportunityInput): Observable<Decision> {
    return this.http.post<Decision>(`${this.baseUrl}/orchestrator/process`, input);
  }

  processBatch(request: BatchOrchestrationRequest): Observable<Decision[]> {
    return this.http.post<Decision[]>(`${this.baseUrl}/orchestrator/process/batch`, request);
  }

  getPipeline(pipelineId: string): Observable<Pipeline> {
    return this.http.get<Pipeline>(`${this.baseUrl}/orchestrator/pipelines/${pipelineId}`);
  }

  getDecisions(limit = 20): Observable<Record<string, unknown>[]> {
    const params = new HttpParams().set('limit', limit.toString());
    return this.http.get<Record<string, unknown>[]>(`${this.baseUrl}/orchestrator/decisions`, { params });
  }

  getStats(): Observable<DecisionStats> {
    return this.http.get<DecisionStats>(`${this.baseUrl}/orchestrator/stats`);
  }
}
