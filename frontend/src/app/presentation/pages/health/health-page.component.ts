import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { HealthFacade } from '../../../application/facades/health.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { StatCardComponent } from '../../../shared/components/stat-card/stat-card.component';

@Component({
  selector: 'app-health-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, LoadingSpinnerComponent, StatCardComponent],
  template: `
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-bold text-white">Estado del Sistema</h1>
        <button (click)="refresh()" class="btn-primary">Actualizar</button>
      </div>

      @if (facade.loading()) { <app-loading-spinner /> }

      <!-- Basic Health -->
      @if (facade.health(); as h) {
        <div class="bg-surface-800 rounded-xl border border-surface-600 p-5">
          <div class="flex items-center gap-3">
            <span class="w-4 h-4 rounded-full" [class]="h.status === 'ok' ? 'bg-accent-green' : 'bg-accent-red'"></span>
            <h2 class="text-lg font-bold" [class]="h.status === 'ok' ? 'text-accent-green' : 'text-accent-red'">
              {{ h.status === 'ok' ? 'Sistema Operativo' : 'Problema Detectado' }}
            </h2>
          </div>
        </div>
      }

      <!-- Pipeline Health -->
      @if (facade.pipeline(); as p) {
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <app-stat-card label="Estado Pipeline"
                         [displayValue]="p.status"
                         [valueColor]="p.status === 'ok' ? 'text-accent-green' : p.status === 'warning' ? 'text-accent-yellow' : 'text-accent-red'" />
          <app-stat-card label="Oportunidades" [displayValue]="p.opportunities.total.toString()" />
          <app-stat-card label="Alta Confianza" [displayValue]="p.opportunities.high_confidence.toString()" valueColor="text-accent-green" />
          <app-stat-card label="Score Promedio" [displayValue]="(p.opportunities.avg_score | number:'1.1-1') || '0'" />
        </div>

        <!-- Database -->
        <div class="bg-surface-800 rounded-xl border border-surface-600 p-5">
          <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Base de Datos</h3>
          <div class="grid grid-cols-2 gap-4">
            <div class="flex items-center gap-3">
              <span class="w-3 h-3 rounded-full" [class]="p.database.status === 'ok' ? 'bg-accent-green' : 'bg-accent-red'"></span>
              <span class="text-sm text-white">{{ p.database.status }}</span>
            </div>
            <div>
              <p class="text-xs text-gray-500">Ultimo listing</p>
              <p class="text-sm text-white">
                {{ p.database.minutes_since_last_listing != null ? (p.database.minutes_since_last_listing | number:'1.0-0') + ' min' : '—' }}
              </p>
            </div>
          </div>
        </div>

        <!-- Redis -->
        <div class="bg-surface-800 rounded-xl border border-surface-600 p-5">
          <h3 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Redis Streams</h3>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p class="text-xs text-gray-500">Estado</p>
              <div class="flex items-center gap-2 mt-1">
                <span class="w-2 h-2 rounded-full" [class]="p.redis.status === 'ok' ? 'bg-accent-green' : 'bg-accent-red'"></span>
                <span class="text-sm text-white">{{ p.redis.status }}</span>
              </div>
            </div>
            <div>
              <p class="text-xs text-gray-500">Stream Length</p>
              <p class="text-lg font-bold text-white mt-1">{{ p.redis.stream_length }}</p>
            </div>
            <div>
              <p class="text-xs text-gray-500">Consumer Lag</p>
              <p class="text-lg font-bold mt-1" [class]="p.redis.consumer_lag > 100 ? 'text-accent-yellow' : 'text-white'">{{ p.redis.consumer_lag }}</p>
            </div>
            <div>
              <p class="text-xs text-gray-500">Pending</p>
              <p class="text-lg font-bold mt-1" [class]="p.redis.pending_messages > 50 ? 'text-accent-red' : 'text-white'">{{ p.redis.pending_messages }}</p>
            </div>
          </div>
        </div>

        <!-- Alerts -->
        @if (p.alerts.length > 0) {
          <div class="bg-accent-red/10 rounded-xl border border-accent-red/30 p-5">
            <h3 class="text-sm font-semibold text-accent-red uppercase tracking-wider mb-3">Alertas del Sistema</h3>
            <div class="space-y-2">
              @for (alert of p.alerts; track alert) {
                <p class="text-sm text-accent-red">&#9888; {{ alert }}</p>
              }
            </div>
          </div>
        }
      }
    </div>
  `,
})
export class HealthPageComponent implements OnInit, OnDestroy {
  readonly facade = inject(HealthFacade);

  ngOnInit(): void {
    this.facade.startPolling();
  }

  ngOnDestroy(): void {
    this.facade.stopPolling();
  }

  refresh(): void {
    this.facade.loadHealth();
    this.facade.loadPipeline();
  }
}
