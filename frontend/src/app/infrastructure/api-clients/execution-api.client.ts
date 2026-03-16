import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../../core/config/api.config';
import {
  ExecutionOrder,
  CreateOrderRequest,
  ApprovalRequest,
  ExecutionResult,
  Portfolio,
  RiskAssessment,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class ExecutionApiClient {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = inject(API_BASE_URL);

  createOrder(request: CreateOrderRequest): Observable<ExecutionOrder> {
    return this.http.post<ExecutionOrder>(`${this.baseUrl}/execution/orders`, request);
  }

  getOrders(status?: string, limit = 50): Observable<ExecutionOrder[]> {
    let params = new HttpParams().set('limit', limit.toString());
    if (status) params = params.set('status', status);
    return this.http.get<ExecutionOrder[]>(`${this.baseUrl}/execution/orders`, { params });
  }

  getOrder(orderId: number): Observable<ExecutionOrder> {
    return this.http.get<ExecutionOrder>(`${this.baseUrl}/execution/orders/${orderId}`);
  }

  approve(orderId: number, request: ApprovalRequest): Observable<ExecutionOrder> {
    return this.http.post<ExecutionOrder>(
      `${this.baseUrl}/execution/orders/${orderId}/approve`,
      request,
    );
  }

  execute(orderId: number): Observable<ExecutionResult> {
    return this.http.post<ExecutionResult>(
      `${this.baseUrl}/execution/orders/${orderId}/execute`,
      {},
    );
  }

  cancel(orderId: number): Observable<ExecutionOrder> {
    return this.http.post<ExecutionOrder>(
      `${this.baseUrl}/execution/orders/${orderId}/cancel`,
      {},
    );
  }

  getPortfolio(): Observable<Portfolio> {
    return this.http.get<Portfolio>(`${this.baseUrl}/execution/portfolio`);
  }

  getRiskAssessment(productId: number, price: number, quantity = 1): Observable<RiskAssessment> {
    const params = new HttpParams()
      .set('price', price.toString())
      .set('quantity', quantity.toString());
    return this.http.get<RiskAssessment>(
      `${this.baseUrl}/execution/risk-assessment/${productId}`,
      { params },
    );
  }
}
