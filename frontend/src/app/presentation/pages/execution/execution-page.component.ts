import { Component, inject, OnInit, signal } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ExecutionFacade } from '../../../application/facades/execution.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';
import { ExecutionOrder } from '../../../domain/models';

@Component({
  selector: 'app-execution-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, FormsModule, LoadingSpinnerComponent, EmptyStateComponent],
  template: `
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-bold text-white">Ejecucion de Ordenes</h1>
        <button (click)="showCreateForm.set(!showCreateForm())"
                class="btn-primary">
          {{ showCreateForm() ? 'Cancelar' : 'Nueva Orden' }}
        </button>
      </div>

      <!-- Create Order Form -->
      @if (showCreateForm()) {
        <div class="bg-surface-800 rounded-xl border border-surface-600 p-5 space-y-4">
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Crear Orden</h2>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <label class="text-xs text-gray-500 block mb-1">Product ID</label>
              <input type="number" [(ngModel)]="newOrder.product_id" class="input-dark w-full" />
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Tipo</label>
              <select [(ngModel)]="newOrder.order_type" class="input-dark w-full">
                <option value="buy">Compra</option>
                <option value="sell">Venta</option>
                <option value="list_item">Listar</option>
              </select>
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Marketplace</label>
              <input type="text" [(ngModel)]="newOrder.marketplace" class="input-dark w-full" placeholder="amazon_us" />
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Precio</label>
              <input type="number" [(ngModel)]="newOrder.price" class="input-dark w-full" step="0.01" />
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Cantidad</label>
              <input type="number" [(ngModel)]="newOrder.quantity" class="input-dark w-full" min="1" />
            </div>
            <div>
              <label class="text-xs text-gray-500 block mb-1">Modo</label>
              <select [(ngModel)]="newOrder.execution_mode" class="input-dark w-full">
                <option value="manual">Manual</option>
                <option value="assisted">Asistido</option>
                <option value="auto">Auto</option>
              </select>
            </div>
          </div>
          <button (click)="createOrder()" [disabled]="facade.executing()"
                  class="btn-primary disabled:opacity-50">Crear Orden</button>
        </div>
      }

      <!-- Status Filter -->
      <div class="flex gap-2 text-sm">
        @for (s of statusFilters; track s.value) {
          <button (click)="filterByStatus(s.value)" class="px-3 py-1.5 rounded-lg transition-colors"
                  [class]="activeFilter() === s.value ? 'bg-accent-blue/20 text-accent-blue' : 'text-gray-500 hover:text-gray-300'">
            {{ s.label }}
          </button>
        }
      </div>

      @if (facade.loading()) { <app-loading-spinner /> }

      @if (!facade.loading()) {
        @if (facade.orders().length === 0) {
          <app-empty-state message="Sin ordenes" subtitle="Crea una nueva orden para comenzar" />
        } @else {
          <div class="space-y-3">
            @for (order of facade.orders(); track order.id) {
              <div class="card flex items-center gap-4">
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2 mb-1">
                    <span class="badge" [class]="orderTypeBadge(order.order_type)">{{ order.order_type }}</span>
                    <span class="badge" [class]="statusBadge(order.status)">{{ order.status }}</span>
                    <span class="badge" [class]="approvalBadge(order.approval_state)">{{ order.approval_state }}</span>
                  </div>
                  <p class="text-sm text-white font-medium">Producto #{{ order.product_id }} — {{ order.marketplace }}</p>
                  <p class="text-xs text-gray-500">{{ order.created_at | date:'medium' }}</p>
                </div>
                <div class="text-right">
                  <p class="text-lg font-bold text-white">\${{ order.price | number:'1.2-2' }}</p>
                  <p class="text-xs text-gray-500">x{{ order.quantity }}</p>
                  @if (order.estimated_profit > 0) {
                    <p class="text-xs text-accent-green">+\${{ order.estimated_profit | number:'1.2-2' }}</p>
                  }
                </div>
                <div class="flex gap-2">
                  @if (order.approval_state === 'pending') {
                    <button (click)="approve(order)" class="px-3 py-1.5 rounded-lg bg-accent-green/20 text-accent-green text-xs hover:bg-accent-green/30">
                      Aprobar
                    </button>
                    <button (click)="reject(order)" class="px-3 py-1.5 rounded-lg bg-accent-red/20 text-accent-red text-xs hover:bg-accent-red/30">
                      Rechazar
                    </button>
                  }
                  @if (order.status === 'approved' || order.approval_state === 'approved') {
                    <button (click)="execute(order)" [disabled]="facade.executing()"
                            class="px-3 py-1.5 rounded-lg bg-accent-blue/20 text-accent-blue text-xs hover:bg-accent-blue/30 disabled:opacity-50">
                      Ejecutar
                    </button>
                  }
                  @if (order.status !== 'executed' && order.status !== 'cancelled') {
                    <button (click)="cancel(order)" class="px-3 py-1.5 rounded-lg bg-surface-600 text-gray-400 text-xs hover:bg-surface-700">
                      Cancelar
                    </button>
                  }
                </div>
              </div>
            }
          </div>
        }
      }
    </div>
  `,
})
export class ExecutionPageComponent implements OnInit {
  readonly facade = inject(ExecutionFacade);
  readonly showCreateForm = signal(false);
  readonly activeFilter = signal<string | null>(null);

  newOrder = {
    product_id: 0,
    order_type: 'buy',
    marketplace: '',
    price: 0,
    quantity: 1,
    execution_mode: 'manual',
  };

  readonly statusFilters = [
    { value: null as string | null, label: 'Todas' },
    { value: 'draft', label: 'Draft' },
    { value: 'pending', label: 'Pendientes' },
    { value: 'approved', label: 'Aprobadas' },
    { value: 'executed', label: 'Ejecutadas' },
    { value: 'cancelled', label: 'Canceladas' },
  ];

  ngOnInit(): void {
    this.facade.loadOrders();
  }

  filterByStatus(status: string | null): void {
    this.activeFilter.set(status);
    this.facade.loadOrders(status ?? undefined);
  }

  createOrder(): void {
    this.facade.createOrder({
      product_id: this.newOrder.product_id,
      order_type: this.newOrder.order_type,
      marketplace: this.newOrder.marketplace,
      price: this.newOrder.price,
      quantity: this.newOrder.quantity,
      execution_mode: this.newOrder.execution_mode,
    });
    this.showCreateForm.set(false);
  }

  approve(order: ExecutionOrder): void {
    if (order.id != null) this.facade.approveOrder(order.id, { approved: true });
  }

  reject(order: ExecutionOrder): void {
    if (order.id != null) this.facade.approveOrder(order.id, { approved: false });
  }

  execute(order: ExecutionOrder): void {
    if (order.id != null) this.facade.executeOrder(order.id);
  }

  cancel(order: ExecutionOrder): void {
    if (order.id != null) this.facade.cancelOrder(order.id);
  }

  orderTypeBadge(type: string): string {
    switch (type) {
      case 'buy': return 'bg-accent-blue/20 text-accent-blue';
      case 'sell': return 'bg-accent-green/20 text-accent-green';
      default: return 'bg-surface-600 text-gray-400';
    }
  }

  statusBadge(status: string): string {
    switch (status) {
      case 'executed': return 'bg-accent-green/20 text-accent-green';
      case 'cancelled': case 'failed': return 'bg-accent-red/20 text-accent-red';
      case 'approved': return 'bg-accent-blue/20 text-accent-blue';
      default: return 'bg-surface-600 text-gray-400';
    }
  }

  approvalBadge(state: string): string {
    switch (state) {
      case 'approved': return 'bg-accent-green/20 text-accent-green';
      case 'rejected': return 'bg-accent-red/20 text-accent-red';
      default: return 'bg-accent-yellow/20 text-accent-yellow';
    }
  }
}
