import { Component, inject, signal } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { PredictionFacade } from '../../../application/facades/prediction.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';

@Component({
  selector: 'app-predictions-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, FormsModule, LoadingSpinnerComponent, EmptyStateComponent],
  template: `
    <div class="p-6 space-y-6">
      <h1 class="text-xl font-bold text-white">Predicciones de Precios</h1>

      <!-- Controls -->
      <div class="bg-surface-800 rounded-xl border border-surface-600 p-5 space-y-4">
        <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider">Buscar Prediccion</h2>
        <div class="flex flex-wrap gap-4 items-end">
          <div>
            <label class="text-xs text-gray-500 block mb-1">Product ID</label>
            <input type="number" [(ngModel)]="productId" class="input-dark w-32" placeholder="ID" />
          </div>
          <div>
            <label class="text-xs text-gray-500 block mb-1">Horizonte</label>
            <select [(ngModel)]="horizon" class="input-dark">
              <option [value]="7">7 dias</option>
              <option [value]="14">14 dias</option>
              <option [value]="30">30 dias</option>
            </select>
          </div>
          <button (click)="fetchPrediction()" [disabled]="!productId || facade.computing()"
                  class="btn-primary disabled:opacity-50">
            Calcular
          </button>
          <button (click)="runBatch()" [disabled]="facade.computing()"
                  class="px-4 py-2 bg-surface-600 hover:bg-surface-700 text-gray-300 rounded-lg transition-colors disabled:opacity-50">
            Batch (Top 50)
          </button>
        </div>
      </div>

      @if (facade.computing()) { <app-loading-spinner /> }

      <!-- Single Product Predictions -->
      @if (facade.predictions().length > 0) {
        <div>
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Predicciones del Producto</h2>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            @for (p of facade.predictions(); track p.id ?? $index) {
              <div class="card">
                <div class="flex items-start justify-between mb-3">
                  <div>
                    <p class="text-[10px] text-gray-500 uppercase">Modelo</p>
                    <p class="text-sm font-semibold text-white">{{ p.model_type }}</p>
                  </div>
                  <span class="badge" [class]="p.status === 'computed' ? 'bg-accent-green/20 text-accent-green' : 'bg-surface-600 text-gray-400'">
                    {{ p.status }}
                  </span>
                </div>
                <div class="grid grid-cols-2 gap-3 mb-3">
                  <div class="bg-surface-700/50 rounded-lg p-3 text-center">
                    <p class="text-[10px] text-gray-500 uppercase">Precio Predicho</p>
                    <p class="text-lg font-bold text-accent-blue">\${{ p.predicted_price | number:'1.2-2' }}</p>
                  </div>
                  <div class="bg-surface-700/50 rounded-lg p-3 text-center">
                    <p class="text-[10px] text-gray-500 uppercase">Horizonte</p>
                    <p class="text-lg font-bold text-white">{{ p.horizon_days }}d</p>
                  </div>
                </div>
                @if (p.confidence_lower != null && p.confidence_upper != null) {
                  <div class="flex justify-between text-xs text-gray-500 mb-2">
                    <span>Rango: \${{ p.confidence_lower | number:'1.2-2' }}</span>
                    <span>— \${{ p.confidence_upper | number:'1.2-2' }}</span>
                  </div>
                }
                <div class="flex justify-between text-xs">
                  <span class="text-gray-500">Confianza</span>
                  <div class="flex items-center gap-2">
                    <div class="w-16 h-1.5 bg-surface-600 rounded-full overflow-hidden">
                      <div class="h-full bg-accent-blue rounded-full" [style.width.%]="p.confidence * 100"></div>
                    </div>
                    <span class="text-accent-blue font-medium">{{ p.confidence * 100 | number:'1.0-0' }}%</span>
                  </div>
                </div>
                @if (p.mape != null) {
                  <p class="text-[10px] text-gray-600 mt-2">MAPE: {{ p.mape | number:'1.2-2' }}%</p>
                }
              </div>
            }
          </div>
        </div>
      }

      <!-- Batch Results -->
      @if (facade.batchResults().length > 0) {
        <div>
          <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">Resultados Batch</h2>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-gray-500 text-xs uppercase border-b border-surface-600">
                  <th class="text-left py-3 px-2">Producto</th>
                  <th class="text-left py-3 px-2">Modelo</th>
                  <th class="text-right py-3 px-2">Precio Pred.</th>
                  <th class="text-right py-3 px-2">Confianza</th>
                  <th class="text-right py-3 px-2">Horizonte</th>
                </tr>
              </thead>
              <tbody>
                @for (p of facade.batchResults(); track p.product_id) {
                  <tr class="border-b border-surface-600/50">
                    <td class="py-2 px-2 text-white">#{{ p.product_id }}</td>
                    <td class="py-2 px-2 text-gray-400">{{ p.model_type }}</td>
                    <td class="py-2 px-2 text-right text-accent-blue font-medium">\${{ p.predicted_price | number:'1.2-2' }}</td>
                    <td class="py-2 px-2 text-right text-gray-300">{{ p.confidence * 100 | number:'1.0-0' }}%</td>
                    <td class="py-2 px-2 text-right text-gray-400">{{ p.horizon_days }}d</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        </div>
      }

      @if (!facade.computing() && facade.predictions().length === 0 && facade.batchResults().length === 0) {
        <app-empty-state message="Sin predicciones aun" subtitle="Ingresa un Product ID o ejecuta un batch para comenzar" />
      }
    </div>
  `,
})
export class PredictionsPageComponent {
  readonly facade = inject(PredictionFacade);
  productId: number | null = null;
  horizon = 7;

  fetchPrediction(): void {
    if (!this.productId) return;
    this.facade.computePrediction({
      product_id: this.productId,
      horizon_days: this.horizon,
    });
    this.facade.loadByProduct(this.productId);
  }

  runBatch(): void {
    this.facade.computeBatch({ horizon_days: this.horizon, limit: 50 });
  }
}
