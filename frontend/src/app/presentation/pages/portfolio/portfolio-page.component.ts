import { Component, inject, OnInit } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { ExecutionFacade } from '../../../application/facades/execution.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { StatCardComponent } from '../../../shared/components/stat-card/stat-card.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';
import { KeyValuePipe } from '@angular/common';

@Component({
  selector: 'app-portfolio-page',
  standalone: true,
  imports: [DecimalPipe, LoadingSpinnerComponent, StatCardComponent, EmptyStateComponent, KeyValuePipe],
  template: `
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-bold text-white">Portfolio</h1>
        <button (click)="facade.loadPortfolio()" class="btn-primary">Actualizar</button>
      </div>

      @if (facade.portfolio(); as p) {
        <div class="grid grid-cols-2 lg:grid-cols-5 gap-4">
          <app-stat-card label="Exposicion Total" [displayValue]="'$' + (p.total_exposure | number:'1.2-2')" valueColor="text-accent-yellow" />
          <app-stat-card label="Ordenes Abiertas" [displayValue]="p.open_orders.toString()" valueColor="text-accent-blue" />
          <app-stat-card label="Ejecutadas Hoy" [displayValue]="p.executed_today.toString()" />
          <app-stat-card label="Total Invertido" [displayValue]="'$' + (p.total_invested | number:'1.2-2')" />
          <app-stat-card label="Profit Total"
                         [displayValue]="'$' + (p.total_profit | number:'1.2-2')"
                         [valueColor]="p.total_profit >= 0 ? 'text-accent-green' : 'text-accent-red'" />
        </div>

        <!-- Orders by Status -->
        @if (p.orders_by_status && Object.keys(p.orders_by_status).length > 0) {
          <div class="bg-surface-800 rounded-xl border border-surface-600 p-5">
            <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Ordenes por Estado</h2>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
              @for (item of p.orders_by_status | keyvalue; track item.key) {
                <div class="bg-surface-700/50 rounded-lg p-4 text-center">
                  <p class="text-[10px] text-gray-500 uppercase">{{ item.key }}</p>
                  <p class="text-2xl font-bold text-white mt-1">{{ item.value }}</p>
                </div>
              }
            </div>
          </div>
        }
      } @else {
        <app-empty-state message="Cargando portfolio..." />
      }

      <!-- Risk Assessment -->
      @if (facade.riskAssessment(); as r) {
        <div class="bg-surface-800 rounded-xl border border-surface-600 p-5">
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">Evaluacion de Riesgo</h2>
          <div class="flex items-center gap-3 mb-4">
            <span class="badge text-sm" [class]="r.passed ? 'bg-accent-green/20 text-accent-green' : 'bg-accent-red/20 text-accent-red'">
              {{ r.passed ? 'APROBADO' : 'RECHAZADO' }}
            </span>
            <span class="text-sm text-gray-400">Exposicion: \${{ r.total_exposure | number:'1.2-2' }}</span>
            <span class="text-sm text-gray-400">Trades hoy: {{ r.daily_trade_count }}</span>
          </div>
          @if (r.guards_failed.length > 0) {
            <div class="space-y-1 mb-3">
              @for (g of r.guards_failed; track g) {
                <p class="text-xs text-accent-red">&#10007; {{ g }}</p>
              }
            </div>
          }
          @if (r.guards_passed.length > 0) {
            <div class="space-y-1">
              @for (g of r.guards_passed; track g) {
                <p class="text-xs text-accent-green">&#10003; {{ g }}</p>
              }
            </div>
          }
        </div>
      }
    </div>
  `,
})
export class PortfolioPageComponent implements OnInit {
  readonly facade = inject(ExecutionFacade);
  protected readonly Object = Object;

  ngOnInit(): void {
    this.facade.loadPortfolio();
  }
}
