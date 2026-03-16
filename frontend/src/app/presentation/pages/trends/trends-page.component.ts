import { Component, inject, OnInit } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { TrendFacade } from '../../../application/facades/trend.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';
import { Trend } from '../../../domain/models';

@Component({
  selector: 'app-trends-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, LoadingSpinnerComponent, EmptyStateComponent],
  template: `
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-bold text-white">Tendencias</h1>
        <button (click)="facade.scan()" [disabled]="facade.scanning()"
                class="btn-primary flex items-center gap-2 disabled:opacity-50">
          @if (facade.scanning()) {
            <span class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
            Escaneando...
          } @else {
            Escanear Tendencias
          }
        </button>
      </div>

      @if (facade.lastScanResult(); as sr) {
        <div class="bg-surface-800 rounded-lg border border-surface-600 p-4 flex gap-6 text-sm">
          <span class="text-gray-400">Analizados: <span class="text-white font-medium">{{ sr.products_analyzed }}</span></span>
          <span class="text-gray-400">Detectados: <span class="text-accent-blue font-medium">{{ sr.trends_detected }}</span></span>
          <span class="text-gray-400">Breakouts: <span class="text-accent-green font-medium">{{ sr.breakouts }}</span></span>
        </div>
      }

      <!-- Breakouts -->
      @if (facade.breakouts().length > 0) {
        <div>
          <h2 class="text-sm font-semibold text-accent-green uppercase tracking-wider mb-3">Breakouts</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            @for (t of facade.breakouts(); track t.product_id) {
              <div class="card border-l-4 border-l-accent-green">
                <h3 class="text-sm font-semibold text-white truncate mb-2">{{ t.product_name || 'Producto #' + t.product_id }}</h3>
                <div class="grid grid-cols-2 gap-3 mb-3">
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Score</p>
                    <p class="text-lg font-bold text-accent-green">{{ t.trend_score | number:'1.0-0' }}</p>
                  </div>
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Tipo</p>
                    <p class="text-sm font-medium" [class]="trendTypeColor(t)">{{ t.trend_type }}</p>
                  </div>
                </div>
                <div class="flex gap-4 text-[11px] text-gray-500">
                  <span>Vel: {{ t.velocity_ratio | number:'1.2-2' }}</span>
                  <span>Mom: {{ t.price_momentum | number:'1.2-2' }}</span>
                  <span>Vol: {{ t.volume_change | number:'1.1-1' }}%</span>
                </div>
              </div>
            }
          </div>
        </div>
      }

      @if (facade.loading()) { <app-loading-spinner /> }

      <!-- All Trends -->
      @if (!facade.loading()) {
        <div>
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Todas las Tendencias</h2>
          @if (facade.trends().length === 0) {
            <app-empty-state message="Sin tendencias detectadas" subtitle="Ejecuta un escaneo de tendencias" />
          } @else {
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="text-gray-500 text-xs uppercase border-b border-surface-600">
                    <th class="text-left py-3 px-2">Producto</th>
                    <th class="text-right py-3 px-2">Score</th>
                    <th class="text-center py-3 px-2">Tipo</th>
                    <th class="text-center py-3 px-2">Fuerza</th>
                    <th class="text-right py-3 px-2">Vel. Ratio</th>
                    <th class="text-right py-3 px-2">Momentum</th>
                    <th class="text-right py-3 px-2">Vol. Change</th>
                    <th class="text-right py-3 px-2">Detectado</th>
                  </tr>
                </thead>
                <tbody>
                  @for (t of facade.trends(); track t.product_id) {
                    <tr class="border-b border-surface-600/50 hover:bg-surface-800 transition-colors">
                      <td class="py-3 px-2 text-white font-medium truncate max-w-[200px]">{{ t.product_name || 'Producto #' + t.product_id }}</td>
                      <td class="py-3 px-2 text-right font-bold" [class]="t.trend_score >= 70 ? 'text-accent-green' : t.trend_score >= 40 ? 'text-accent-yellow' : 'text-gray-400'">
                        {{ t.trend_score | number:'1.0-0' }}
                      </td>
                      <td class="py-3 px-2 text-center">
                        <span class="badge" [class]="trendTypeBadge(t.trend_type)">{{ t.trend_type }}</span>
                      </td>
                      <td class="py-3 px-2 text-center">
                        <span class="badge" [class]="strengthBadge(t.trend_strength)">{{ t.trend_strength }}</span>
                      </td>
                      <td class="py-3 px-2 text-right text-gray-300">{{ t.velocity_ratio | number:'1.2-2' }}</td>
                      <td class="py-3 px-2 text-right" [class]="t.price_momentum >= 0 ? 'text-accent-green' : 'text-accent-red'">
                        {{ t.price_momentum | number:'1.2-2' }}
                      </td>
                      <td class="py-3 px-2 text-right text-gray-300">{{ t.volume_change | number:'1.1-1' }}%</td>
                      <td class="py-3 px-2 text-right text-gray-500">{{ t.detected_at | date:'short' }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </div>
      }
    </div>
  `,
})
export class TrendsPageComponent implements OnInit {
  readonly facade = inject(TrendFacade);

  ngOnInit(): void {
    this.facade.loadTrends();
    this.facade.loadBreakouts();
  }

  trendTypeColor(t: Trend): string {
    if (t.trend_type === 'breakout') return 'text-accent-green';
    if (t.trend_type === 'decline') return 'text-accent-red';
    return 'text-gray-400';
  }

  trendTypeBadge(type: string): string {
    switch (type) {
      case 'breakout': return 'bg-accent-green/20 text-accent-green';
      case 'decline': return 'bg-accent-red/20 text-accent-red';
      default: return 'bg-surface-600 text-gray-400';
    }
  }

  strengthBadge(strength: string): string {
    switch (strength) {
      case 'strong': return 'bg-accent-green/20 text-accent-green';
      case 'moderate': return 'bg-accent-yellow/20 text-accent-yellow';
      default: return 'bg-surface-600 text-gray-500';
    }
  }
}
