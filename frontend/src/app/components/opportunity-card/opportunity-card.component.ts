import { Component, Input, Output, EventEmitter } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { Opportunity } from '../../models/opportunity.model';

@Component({
  selector: 'app-opportunity-card',
  standalone: true,
  imports: [DecimalPipe, DatePipe],
  template: `
    <div class="card flex flex-col gap-4 cursor-pointer" (click)="selected.emit(opp.id)">
      <!-- Header: image + title -->
      <div class="flex gap-4">
        <div class="w-20 h-20 rounded-lg bg-surface-700 flex-shrink-0 overflow-hidden">
          @if (opp.image_url) {
            <img [src]="opp.image_url" [alt]="opp.product_name"
                 class="w-full h-full object-cover" />
          } @else {
            <div class="w-full h-full flex items-center justify-center text-gray-600 text-2xl">
              ?
            </div>
          }
        </div>
        <div class="flex-1 min-w-0">
          <h3 class="text-sm font-semibold text-white truncate">{{ opp.product_name }}</h3>
          <p class="text-xs text-gray-500 mt-1">{{ opp.created_at | date:'short' }}</p>
          <div class="flex gap-2 mt-2">
            <span class="badge bg-surface-600 text-gray-300">{{ opp.buy_marketplace }}</span>
            <span class="text-gray-600">→</span>
            <span class="badge bg-surface-600 text-gray-300">{{ opp.sell_marketplace }}</span>
          </div>
        </div>
      </div>

      <!-- Price comparison -->
      <div class="grid grid-cols-2 gap-3">
        <div class="bg-surface-700/50 rounded-lg p-3 text-center">
          <p class="text-[10px] text-gray-500 uppercase">Compra</p>
          <p class="text-lg font-bold text-white">\${{ opp.buy_price | number:'1.2-2' }}</p>
        </div>
        <div class="bg-surface-700/50 rounded-lg p-3 text-center">
          <p class="text-[10px] text-gray-500 uppercase">Venta</p>
          <p class="text-lg font-bold text-white">\${{ opp.sell_price | number:'1.2-2' }}</p>
        </div>
      </div>

      <!-- Profit + ROI -->
      <div class="flex items-center justify-between">
        <div>
          <p class="text-[10px] text-gray-500 uppercase">Profit neto</p>
          <p class="text-base font-bold text-accent-green">\${{ opp.net_profit | number:'1.2-2' }}</p>
        </div>
        <div class="text-right">
          <p class="text-[10px] text-gray-500 uppercase">ROI</p>
          <p class="text-xl font-bold" [class]="roiColor">
            {{ opp.roi * 100 | number:'1.1-1' }}%
          </p>
        </div>
      </div>

      <!-- Fees breakdown -->
      <div class="flex gap-4 text-[11px] text-gray-500">
        <span>Comisiones: \${{ opp.fees | number:'1.2-2' }}</span>
        <span>Envio: \${{ opp.shipping_cost | number:'1.2-2' }}</span>
      </div>

      <!-- Action links -->
      <div class="flex gap-2">
        <a [href]="opp.buy_url" target="_blank" rel="noopener" (click)="$event.stopPropagation()"
           class="btn-primary flex-1 text-center text-sm py-1.5">
          Comprar
        </a>
        <a [href]="opp.sell_url" target="_blank" rel="noopener" (click)="$event.stopPropagation()"
           class="flex-1 text-center text-sm py-1.5 bg-surface-600 hover:bg-surface-700 text-gray-300 rounded-lg transition-colors">
          Ver venta
        </a>
      </div>
    </div>
  `,
})
export class OpportunityCardComponent {
  @Input({ required: true }) opp!: Opportunity;
  @Output() selected = new EventEmitter<number>();

  get roiColor(): string {
    if (this.opp.roi >= 0.5) return 'text-accent-green';
    if (this.opp.roi >= 0.25) return 'text-accent-yellow';
    return 'text-accent-red';
  }
}
