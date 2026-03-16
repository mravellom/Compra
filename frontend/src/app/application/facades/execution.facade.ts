import { Injectable, inject, signal } from '@angular/core';
import { ExecutionApiClient } from '../../infrastructure/api-clients/execution-api.client';
import { NotificationService } from '../../shared/services/notification.service';
import {
  ExecutionOrder,
  CreateOrderRequest,
  ApprovalRequest,
  Portfolio,
  RiskAssessment,
} from '../../domain/models';

@Injectable({ providedIn: 'root' })
export class ExecutionFacade {
  private readonly api = inject(ExecutionApiClient);
  private readonly notifications = inject(NotificationService);

  readonly orders = signal<ExecutionOrder[]>([]);
  readonly selectedOrder = signal<ExecutionOrder | null>(null);
  readonly portfolio = signal<Portfolio | null>(null);
  readonly riskAssessment = signal<RiskAssessment | null>(null);
  readonly loading = signal(false);
  readonly executing = signal(false);

  loadOrders(status?: string, limit = 50): void {
    this.loading.set(true);
    this.api.getOrders(status, limit).subscribe({
      next: (data) => {
        this.orders.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  loadOrder(orderId: number): void {
    this.api.getOrder(orderId).subscribe({
      next: (data) => this.selectedOrder.set(data),
    });
  }

  createOrder(request: CreateOrderRequest): void {
    this.executing.set(true);
    this.api.createOrder(request).subscribe({
      next: (order) => {
        this.orders.update((list) => [order, ...list]);
        this.executing.set(false);
        this.notifications.success('Orden creada');
      },
      error: () => this.executing.set(false),
    });
  }

  approveOrder(orderId: number, request: ApprovalRequest): void {
    this.api.approve(orderId, request).subscribe({
      next: (order) => {
        this.updateOrderInList(order);
        this.notifications.success(request.approved ? 'Orden aprobada' : 'Orden rechazada');
      },
    });
  }

  executeOrder(orderId: number): void {
    this.executing.set(true);
    this.api.execute(orderId).subscribe({
      next: (result) => {
        this.executing.set(false);
        if (result.success) {
          this.notifications.success('Orden ejecutada exitosamente');
          this.loadOrders();
          this.loadPortfolio();
        } else {
          this.notifications.error(result.error_message || 'Error en ejecucion');
        }
      },
      error: () => this.executing.set(false),
    });
  }

  cancelOrder(orderId: number): void {
    this.api.cancel(orderId).subscribe({
      next: (order) => {
        this.updateOrderInList(order);
        this.notifications.info('Orden cancelada');
      },
    });
  }

  loadPortfolio(): void {
    this.api.getPortfolio().subscribe({
      next: (data) => this.portfolio.set(data),
    });
  }

  assessRisk(productId: number, price: number, quantity = 1): void {
    this.api.getRiskAssessment(productId, price, quantity).subscribe({
      next: (data) => this.riskAssessment.set(data),
    });
  }

  private updateOrderInList(updated: ExecutionOrder): void {
    this.orders.update((list) =>
      list.map((o) => (o.id === updated.id ? updated : o)),
    );
  }
}
