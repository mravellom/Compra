import { Component, inject, OnInit } from '@angular/core';
import { DecimalPipe, DatePipe, JsonPipe } from '@angular/common';
import { OrchestratorFacade } from '../../../application/facades/orchestrator.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';

@Component({
  selector: 'app-orchestrator-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, JsonPipe, LoadingSpinnerComponent, EmptyStateComponent],
  template: `
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-bold text-white">AI Decision Pipeline</h1>
        <div class="flex gap-2">
          <button (click)="runBatch()" [disabled]="facade.processing()"
                  class="btn-primary flex items-center gap-2 disabled:opacity-50">
            @if (facade.processing()) {
              <span class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
              Procesando...
            } @else {
              Procesar Top 20
            }
          </button>
        </div>
      </div>

      <!-- Batch Decisions -->
      @if (facade.batchDecisions().length > 0) {
        <div>
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Decisiones Recientes</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            @for (d of facade.batchDecisions(); track d.opportunity_id) {
              <div class="card">
                <div class="flex items-start justify-between mb-3">
                  <div>
                    <p class="text-xs text-gray-500">Opp #{{ d.opportunity_id }}</p>
                    <p class="text-lg font-bold" [class]="decisionColor(d.decision)">{{ d.decision }}</p>
                  </div>
                  <div class="text-right">
                    <span class="badge" [class]="signalBadge(d.signal_strength)">{{ d.signal_strength }}</span>
                    <p class="text-sm font-bold text-white mt-1">{{ d.score | number:'1.0-0' }}</p>
                  </div>
                </div>
                @if (d.recommended_action) {
                  <p class="text-xs text-accent-blue mb-2">Accion: {{ d.recommended_action }}</p>
                }
                @if (d.recommended_price) {
                  <p class="text-xs text-gray-400 mb-2">Precio sugerido: \${{ d.recommended_price | number:'1.2-2' }}</p>
                }
                @if (d.reasons.length > 0) {
                  <div class="space-y-1">
                    @for (r of d.reasons; track r) {
                      <p class="text-[11px] text-gray-500">&#8226; {{ r }}</p>
                    }
                  </div>
                }
              </div>
            }
          </div>
        </div>
      }

      @if (facade.loading()) { <app-loading-spinner /> }

      <!-- Historical Decisions -->
      @if (!facade.loading()) {
        <div>
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Historial de Decisiones</h2>
          @if (facade.decisions().length === 0) {
            <app-empty-state message="Sin decisiones aun" subtitle="Procesa oportunidades para generar decisiones AI" />
          } @else {
            <div class="overflow-x-auto">
              <table class="w-full text-sm">
                <thead>
                  <tr class="text-gray-500 text-xs uppercase border-b border-surface-600">
                    <th class="text-left py-3 px-2">Opp ID</th>
                    <th class="text-left py-3 px-2">Decision</th>
                    <th class="text-left py-3 px-2">Senal</th>
                    <th class="text-right py-3 px-2">Score</th>
                    <th class="text-left py-3 px-2">Razones</th>
                  </tr>
                </thead>
                <tbody>
                  @for (d of facade.decisions(); track $index) {
                    <tr class="border-b border-surface-600/50">
                      <td class="py-2 px-2 text-white">#{{ d['opportunity_id'] }}</td>
                      <td class="py-2 px-2" [class]="decisionColor(d['decision']?.toString() ?? '')">{{ d['decision'] }}</td>
                      <td class="py-2 px-2"><span class="badge" [class]="signalBadge(d['signal_strength']?.toString() ?? '')">{{ d['signal_strength'] }}</span></td>
                      <td class="py-2 px-2 text-right text-gray-300">{{ d['score'] }}</td>
                      <td class="py-2 px-2 text-gray-500 text-xs truncate max-w-[300px]">{{ d['reasons'] }}</td>
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
export class OrchestratorPageComponent implements OnInit {
  readonly facade = inject(OrchestratorFacade);

  ngOnInit(): void {
    this.facade.loadDecisions();
    this.facade.loadStats();
  }

  runBatch(): void {
    this.facade.processBatch({ limit: 20 });
  }

  decisionColor(decision: string): string {
    if (decision === 'buy' || decision === 'strong_buy') return 'text-accent-green';
    if (decision === 'sell' || decision === 'strong_sell') return 'text-accent-red';
    if (decision === 'hold') return 'text-accent-yellow';
    return 'text-gray-400';
  }

  signalBadge(strength: string): string {
    switch (strength) {
      case 'strong': return 'bg-accent-green/20 text-accent-green';
      case 'moderate': return 'bg-accent-yellow/20 text-accent-yellow';
      default: return 'bg-surface-600 text-gray-500';
    }
  }
}
