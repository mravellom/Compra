import { Component, inject, OnInit } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { CategoryFacade } from '../../../application/facades/category.facade';
import { LoadingSpinnerComponent } from '../../../shared/components/loading-spinner/loading-spinner.component';
import { EmptyStateComponent } from '../../../shared/components/empty-state/empty-state.component';

@Component({
  selector: 'app-categories-page',
  standalone: true,
  imports: [DecimalPipe, DatePipe, LoadingSpinnerComponent, EmptyStateComponent],
  template: `
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <h1 class="text-xl font-bold text-white">Arbitraje por Categoria</h1>
        <button (click)="facade.refresh()" [disabled]="facade.refreshing()"
                class="btn-primary flex items-center gap-2 disabled:opacity-50">
          @if (facade.refreshing()) {
            <span class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
            Actualizando...
          } @else {
            Actualizar
          }
        </button>
      </div>

      @if (facade.loading()) { <app-loading-spinner /> }

      @if (!facade.loading()) {
        @if (facade.categories().length === 0) {
          <app-empty-state message="Sin datos de categorias" subtitle="Ejecuta una actualizacion para generar estadisticas" />
        } @else {
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-gray-500 text-xs uppercase border-b border-surface-600">
                  <th class="text-left py-3 px-3">Categoria</th>
                  <th class="text-right py-3 px-3">Score</th>
                  <th class="text-right py-3 px-3">Opps</th>
                  <th class="text-right py-3 px-3">Productos</th>
                  <th class="text-right py-3 px-3">Avg ROI</th>
                  <th class="text-right py-3 px-3">Avg Profit</th>
                  <th class="text-right py-3 px-3">Best ROI</th>
                  <th class="text-right py-3 px-3">Best Profit</th>
                  <th class="text-right py-3 px-3">Velocidad</th>
                  <th class="text-right py-3 px-3">Competencia</th>
                  <th class="text-right py-3 px-3">Actualizado</th>
                </tr>
              </thead>
              <tbody>
                @for (c of facade.categories(); track c.category_name) {
                  <tr class="border-b border-surface-600/50 hover:bg-surface-800 transition-colors">
                    <td class="py-3 px-3 text-white font-medium">{{ c.category_name }}</td>
                    <td class="py-3 px-3 text-right">
                      <span class="font-bold" [class]="c.category_score >= 70 ? 'text-accent-green' : c.category_score >= 40 ? 'text-accent-yellow' : 'text-gray-400'">
                        {{ c.category_score | number:'1.0-0' }}
                      </span>
                    </td>
                    <td class="py-3 px-3 text-right text-accent-blue font-medium">{{ c.opportunity_count }}</td>
                    <td class="py-3 px-3 text-right text-gray-300">{{ c.total_products }}</td>
                    <td class="py-3 px-3 text-right" [class]="c.avg_roi >= 0.4 ? 'text-accent-green' : 'text-gray-300'">
                      {{ c.avg_roi * 100 | number:'1.1-1' }}%
                    </td>
                    <td class="py-3 px-3 text-right text-accent-green">\${{ c.avg_profit | number:'1.2-2' }}</td>
                    <td class="py-3 px-3 text-right text-accent-green font-medium">{{ c.best_roi * 100 | number:'1.1-1' }}%</td>
                    <td class="py-3 px-3 text-right text-accent-green">\${{ c.best_profit | number:'1.2-2' }}</td>
                    <td class="py-3 px-3 text-right text-gray-300">{{ c.avg_sales_velocity | number:'1.0-0' }}</td>
                    <td class="py-3 px-3 text-right" [class]="c.avg_competition > 70 ? 'text-accent-red' : 'text-accent-green'">
                      {{ c.avg_competition | number:'1.0-0' }}
                    </td>
                    <td class="py-3 px-3 text-right text-gray-500">{{ c.updated_at | date:'short' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      }
    </div>
  `,
})
export class CategoriesPageComponent implements OnInit {
  readonly facade = inject(CategoryFacade);

  ngOnInit(): void {
    this.facade.load();
  }
}
